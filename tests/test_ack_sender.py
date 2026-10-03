"""Estado del emisor de SACK: ventana, ACKs acumulativos/duplicados/viejos y timeouts."""

import time
import unittest

import base  # noqa: F401 - arma el sys.path hacia src/
from lib.protocols.selective_ack.ack_sender import (
    DUP_ACK,
    DUP_ACKS_THRESHOLD,
    FAST_RETRANSMIT,
    NEW_ACK,
    STALE_ACK,
    ACKSender,
    SentSegment,
)

CWND = 4
LARGO = 100
NUNCA = 3600.0      # timeout que no vence durante el test
YA = 0.0            # timeout vencido de entrada


def emisor_con(n, timeout=NUNCA, cwnd=CWND):
    """Emisor con `n` segmentos de LARGO bytes en vuelo, desde seq=1."""
    s = ACKSender(1, cwnd, timeout)
    segmentos = [s.add(b"x" * LARGO, 0) for _ in range(n)]
    return s, segmentos


class TestSentSegment(unittest.TestCase):

    def test_end_es_el_seq_siguiente(self):
        self.assertEqual(SentSegment(10, b"abc", 0, NUNCA).end, 13)

    def test_refresh_suma_un_intento_y_mueve_el_deadline(self):
        seg = SentSegment(1, b"x", 0, YA)
        antes = seg.deadline
        seg.refresh(NUNCA)
        self.assertEqual(seg.retries, 1)
        self.assertGreater(seg.deadline, antes + NUNCA / 2)

    def test_arranca_sin_intentos_ni_marca(self):
        seg = SentSegment(1, b"x", 0, NUNCA)
        self.assertEqual(seg.retries, 0)
        self.assertFalse(seg.sacked)


class TestVentana(unittest.TestCase):

    def test_arranca_vacio(self):
        s = ACKSender(1, CWND, NUNCA)
        self.assertTrue(s.is_idle)
        self.assertEqual(s.in_flight, 0)
        self.assertTrue(s.has_room())

    def test_add_asigna_seqs_consecutivos(self):
        s, segs = emisor_con(3)
        self.assertEqual([g.seq for g in segs], [1, 101, 201])
        self.assertEqual(s.next_seq, 301)
        self.assertEqual(s.in_flight, 3)
        self.assertFalse(s.is_idle)

    def test_la_ventana_se_llena_en_cwnd(self):
        s, _ = emisor_con(CWND)
        self.assertFalse(s.has_room())

    def test_fin_vacio_avanza_uno(self):
        s = ACKSender(1, CWND, NUNCA)
        fin = s.add(b"", 1)
        self.assertEqual(fin.seq, 1)
        self.assertEqual(s.next_seq, 2)

    def test_add_guarda_flags_y_payload(self):
        s = ACKSender(1, CWND, NUNCA)
        seg = s.add(b"hola", 7)
        self.assertEqual((seg.payload, seg.flags), (b"hola", 7))


class TestAckNuevo(unittest.TestCase):

    def test_ack_parcial_libera_el_prefijo(self):
        s, _ = emisor_con(3)
        objetivo, estado = s.handle_ack(101, [])
        self.assertEqual((objetivo, estado), (None, NEW_ACK))
        self.assertEqual(s.send_base, 101)
        self.assertEqual(s.in_flight, 2)
        self.assertTrue(s.has_room())

    def test_ack_total_vacia_la_ventana(self):
        s, _ = emisor_con(3)
        s.handle_ack(301, [])
        self.assertTrue(s.is_idle)

    def test_ack_nuevo_reinicia_los_duplicados(self):
        s, _ = emisor_con(3)
        s.handle_ack(1, [])
        s.handle_ack(1, [])
        self.assertEqual(s.dup_acks, 2)
        s.handle_ack(101, [])
        self.assertEqual(s.dup_acks, 0)

    def test_ack_que_cae_en_medio_de_un_segmento_no_lo_libera(self):
        s, segs = emisor_con(2)
        s.handle_ack(150, [])
        self.assertEqual(s.send_base, 150)
        self.assertEqual(s.window, [segs[1]],
                         "1..101 queda confirmado; 101..201 solo a medias y sigue en vuelo")


class TestAckViejo(unittest.TestCase):

    def test_ack_menor_que_send_base(self):
        s, _ = emisor_con(3)
        s.handle_ack(201, [])
        objetivo, estado = s.handle_ack(101, [])
        self.assertEqual((objetivo, estado), (None, STALE_ACK))
        self.assertEqual(s.send_base, 201)

    def test_un_ack_viejo_no_cuenta_como_duplicado(self):
        s, _ = emisor_con(3)
        s.handle_ack(201, [])
        for _ in range(DUP_ACKS_THRESHOLD + 1):
            s.handle_ack(101, [])
        self.assertEqual(s.dup_acks, 0)

    def test_ack_igual_sin_nada_en_vuelo(self):
        s, _ = emisor_con(1)
        s.handle_ack(101, [])
        objetivo, estado = s.handle_ack(101, [])
        self.assertEqual((objetivo, estado), (None, STALE_ACK))
        self.assertEqual(s.dup_acks, 0)


class TestDuplicadosYFastRetransmit(unittest.TestCase):

    def test_duplicados_por_debajo_del_umbral(self):
        s, _ = emisor_con(4)
        for n in range(1, DUP_ACKS_THRESHOLD):
            objetivo, estado = s.handle_ack(1, [])
            self.assertEqual((objetivo, estado), (None, DUP_ACK))
            self.assertEqual(s.dup_acks, n)

    def test_al_llegar_al_umbral_devuelve_el_hueco(self):
        s, segs = emisor_con(4)
        for _ in range(DUP_ACKS_THRESHOLD - 1):
            s.handle_ack(1, [(101, 401)])
        objetivo, estado = s.handle_ack(1, [(101, 401)])
        self.assertEqual(estado, FAST_RETRANSMIT)
        self.assertIs(objetivo, segs[0])

    def test_el_umbral_reinicia_el_contador(self):
        s, _ = emisor_con(4)
        for _ in range(DUP_ACKS_THRESHOLD):
            s.handle_ack(1, [])
        self.assertEqual(s.dup_acks, 0, "no reenvia el mismo hueco en cada duplicado")
        _, estado = s.handle_ack(1, [])
        self.assertEqual(estado, DUP_ACK)

    def test_el_hueco_saltea_los_segmentos_sackeados(self):
        s, segs = emisor_con(4)
        s.handle_ack(101, [])                       # 1..101 confirmado
        bloques = [(201, 401)]                      # tiene 201..401, falta 101..201
        for _ in range(DUP_ACKS_THRESHOLD):
            objetivo, estado = s.handle_ack(101, bloques)
        self.assertEqual(estado, FAST_RETRANSMIT)
        self.assertIs(objetivo, segs[1])

    def test_fast_retransmit_sin_hueco_devuelve_none(self):
        """Todo lo que esta en vuelo esta sackeado: no hay nada que reenviar."""
        s, _ = emisor_con(2)
        for _ in range(DUP_ACKS_THRESHOLD):
            objetivo, estado = s.handle_ack(1, [(1, 201)])
        self.assertEqual((objetivo, estado), (None, FAST_RETRANSMIT))


class TestMarcasSack(unittest.TestCase):

    def test_un_bloque_marca_los_segmentos_que_cubre_entero(self):
        s, segs = emisor_con(4)
        s.handle_ack(1, [(101, 301)])
        self.assertEqual([g.sacked for g in segs], [False, True, True, False])

    def test_un_bloque_parcial_no_marca(self):
        s, segs = emisor_con(2)
        s.handle_ack(1, [(101, 150)])
        self.assertFalse(segs[1].sacked)

    def test_hole_devuelve_el_primer_no_confirmado_ni_sackeado(self):
        s, segs = emisor_con(4)
        s.handle_ack(1, [(1, 101), (201, 301)])
        self.assertIs(s.hole(), segs[1])

    def test_hole_sin_huecos(self):
        s, _ = emisor_con(2)
        s.handle_ack(1, [(1, 201)])
        self.assertIsNone(s.hole())


class TestTimeout(unittest.TestCase):

    def test_sin_nada_en_vuelo(self):
        self.assertIsNone(ACKSender(1, CWND, YA).on_timeout())

    def test_sin_vencer(self):
        s, _ = emisor_con(2, timeout=NUNCA)
        self.assertIsNone(s.on_timeout())

    def test_vencido_devuelve_el_hueco(self):
        s, segs = emisor_con(2, timeout=YA)
        time.sleep(0.001)
        self.assertIs(s.on_timeout(), segs[0])

    def test_vencido_borra_las_marcas_sack(self):
        """Las marcas pudieron quedar viejas (el receptor las descarta): se decide de cero."""
        s, segs = emisor_con(3, timeout=YA)
        s.handle_ack(1, [(1, 101), (201, 301)])
        time.sleep(0.001)
        self.assertIs(s.on_timeout(), segs[0])
        self.assertEqual([g.sacked for g in segs], [False, False, False])

    def test_solo_mira_el_deadline_del_mas_viejo(self):
        s, segs = emisor_con(2, timeout=NUNCA)
        segs[1].deadline = 0            # vencido, pero no es el primero de la ventana
        self.assertIsNone(s.on_timeout())


class TestFinVacio(unittest.TestCase):
    """El FIN de un archivo vacio ocupa 1 numero de secuencia, como en el receptor.

    Antes `end` era `seq + len(payload)`: un FIN sin payload quedaba con
    end == seq, se daba por confirmado sin su ACK y nunca se reenviaba (los dos
    extremos se colgaban si se perdia).
    """

    def test_el_fin_vacio_termina_un_seq_despues(self):
        fin = SentSegment(1, b"", 1, NUNCA)
        self.assertEqual(fin.end, 2)

    def test_no_se_da_por_confirmado_sin_su_ack(self):
        s = ACKSender(1, CWND, NUNCA)
        fin = s.add(b"", 1)
        self.assertFalse(s.is_acked(fin))
        self.assertIs(s.hole(), fin)

    def test_el_timeout_lo_retransmite(self):
        s = ACKSender(1, CWND, YA)
        fin = s.add(b"", 1)
        time.sleep(0.001)
        self.assertIs(s.on_timeout(), fin)

    def test_un_ack_que_no_lo_cubre_no_lo_saca(self):
        s = ACKSender(1, CWND, NUNCA)
        s.add(b"", 1)
        _, estado = s.handle_ack(1, [])
        self.assertEqual(estado, DUP_ACK)
        self.assertFalse(s.is_idle)

    def test_su_ack_lo_saca_de_la_ventana(self):
        s = ACKSender(1, CWND, NUNCA)
        s.add(b"", 1)
        _, estado = s.handle_ack(2, [])
        self.assertEqual(estado, NEW_ACK)
        self.assertTrue(s.is_idle)

    def test_un_bloque_sack_del_fin_lo_marca(self):
        """El receptor anuncia un FIN vacio bufferado como (seq, seq+1)."""
        s = ACKSender(1, CWND, NUNCA)
        s.add(b"x" * LARGO, 0)
        fin = s.add(b"", 1)
        s.handle_ack(1, [(101, 102)])
        self.assertTrue(fin.sacked)


if __name__ == "__main__":
    unittest.main()
