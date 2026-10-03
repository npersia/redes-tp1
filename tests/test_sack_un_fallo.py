"""Selective ACK con un unico fallo: una perdida, un duplicado o un desorden."""

import os
import time
import unittest

from base import ConnectionClosed, Hilo, SACK_TIMEOUT_EXACTO, SACKTestCase, netsim, sack

MAX = sack.MAX_PAYLOAD_SIZE
SEGMENTOS = 6
DATOS = SEGMENTOS * MAX


def seq_del(n):
    """seq del n-esimo segmento (0-based) de una transferencia que arranca en 1."""
    return 1 + n * MAX


class UnFallo(SACKTestCase):

    timeout = SACK_TIMEOUT_EXACTO

    def correr(self, emisor=None, receptor=None, tam=DATOS):
        """Corre la transferencia en las dos direcciones y devuelve, por direccion,
        (copias por seq del emisor, trace del send)."""
        resultados = {}
        datos = os.urandom(tam)
        for direccion, e, r, rol_e, rol_r in self.direcciones(emisor, receptor):
            with self.subTest(direccion=direccion):
                self.net.entries.clear()
                self.assertEqual(self.transferir(e, r, datos), datos, self.volcado())
                self.assertVentanaRespetada(rol_e, rol_r)
                resultados[direccion] = (self.copias_por_seq(rol_e), e._send_trace)
        return resultados

    def reenviados(self, copias):
        return {seq: n for seq, n in copias.items() if n > 1}


class TestPerdidaDeUnSegmento(UnFallo):

    def test_se_pierde_el_primero(self):
        """Los que siguen generan ACKs duplicados: fast retransmit, sin esperar el timeout."""
        for copias, trace in self.correr(emisor=lambda: netsim.drop_seq(seq_del(0))).values():
            self.assertEqual(self.reenviados(copias), {seq_del(0): 2})
            self.assertEqual((trace.fast_retransmits, trace.timeouts), (1, 0))

    def test_se_pierde_uno_del_medio(self):
        for copias, trace in self.correr(emisor=lambda: netsim.drop_seq(seq_del(2))).values():
            self.assertEqual(self.reenviados(copias), {seq_del(2): 2})
            self.assertEqual((trace.fast_retransmits, trace.timeouts), (1, 0))

    def test_se_pierde_el_ultimo_con_fin(self):
        """Nada viene detras para generar duplicados: solo lo rescata el timeout."""
        for copias, trace in self.correr(emisor=lambda: netsim.drop_seq(seq_del(SEGMENTOS - 1))).values():
            self.assertEqual(self.reenviados(copias), {seq_del(SEGMENTOS - 1): 2})
            self.assertEqual((trace.fast_retransmits, trace.timeouts), (0, 1))

    def test_se_pierde_el_fin_de_un_archivo_vacio(self):
        """El FIN vacio ocupa 1 seq: si se pierde, el timeout lo reenvia y los dos terminan."""
        for copias, trace in self.correr(emisor=lambda: netsim.drop_seq(1), tam=0).values():
            self.assertEqual(copias, {1: 2})
            self.assertEqual((trace.fast_retransmits, trace.timeouts), (0, 1))

    def test_se_pierde_el_unico_segmento(self):
        for copias, trace in self.correr(emisor=lambda: netsim.drop_seq(1), tam=10).values():
            self.assertEqual(self.reenviados(copias), {1: 2})
            self.assertEqual((trace.fast_retransmits, trace.timeouts), (0, 1))

    def test_el_receptor_bufferea_lo_que_llega_detras_del_hueco(self):
        cliente, conexion = self.conectados(policy_cliente=netsim.drop_seq(seq_del(0)))
        self.transferir(cliente, conexion, os.urandom(DATOS))
        acks = [p for p in self.net.wire("servidor") if netsim.es_ack_puro_sack_pkt(p)]
        # mientras falta el primero, el ACK no avanza y los bloques SACK crecen
        bloques = [sack.parse_sack_option(sack.packet.get_header_options(p["raw"]))
                   for p in acks if p["ack"] == 1]
        self.assertEqual(bloques[:3], [[(seq_del(1), seq_del(2))],
                                       [(seq_del(1), seq_del(3))],
                                       [(seq_del(1), seq_del(4))]], self.volcado())


class TestPerdidaDeUnAck(UnFallo):

    def test_se_pierde_un_ack_del_medio(self):
        """El ACK siguiente es acumulativo y lo cubre: no hay nada que reenviar."""
        for copias, trace in self.correr(receptor=lambda: netsim.drop_acks_sack_nth(2)).values():
            self.assertEqual(self.reenviados(copias), {})
            self.assertEqual(trace.retransmissions, 0)

    def test_se_pierde_el_primer_ack(self):
        for copias, _ in self.correr(receptor=lambda: netsim.drop_acks_sack_nth(1)).values():
            self.assertEqual(self.reenviados(copias), {})


class TestDuplicadoYDesorden(UnFallo):

    def test_un_segmento_duplicado_no_rompe_nada(self):
        for direccion, e, r, rol_e, _ in self.direcciones(emisor=lambda: netsim.dup_seq(seq_del(1))):
            with self.subTest(direccion=direccion):
                datos = os.urandom(DATOS)
                self.assertEqual(self.transferir(e, r, datos), datos)
                self.assertEqual(e._send_trace.retransmissions, 0,
                                 "el duplicado lo genero la red, no el emisor")

    def test_un_segmento_demorado_llega_igual(self):
        """El desorden genera ACKs duplicados: un fast retransmit de mas es esperable."""
        for copias, trace in self.correr(emisor=lambda: netsim.delay_seq(seq_del(1), 0.02)).values():
            self.assertLessEqual(trace.fast_retransmits, 1)
            self.assertEqual(trace.timeouts, 0)

    def test_un_desorden_corto_no_dispara_nada(self):
        """Si el demorado llega antes del 3er duplicado, no se reenvia."""
        for copias, trace in self.correr(emisor=lambda: netsim.delay_seq(seq_del(SEGMENTOS - 2), 0.002),
                                         ).values():
            self.assertEqual(trace.timeouts, 0)


class TestHandshakeQueTerminaConDatos(SACKTestCase):

    timeout = SACK_TIMEOUT_EXACTO

    def test_se_pierde_el_ack_final_del_handshake(self):
        """El servidor sigue esperando el ACK final; el primer dato lo trae (flag ACK)."""
        servidor = self.servidor()
        puerto = servidor.sock.getsockname()[1]
        aceptando = Hilo(self.aceptar, servidor)
        aceptando.start()

        # el cliente pierde su primer datagrama post-SYN: el ACK final
        self.net.on_create = lambda s: setattr(s, "policy", netsim.drop_nth(2))
        cliente = self.cliente(puerto)
        cliente.connect()
        self.net.on_create = None

        datos = os.urandom(MAX * 2)
        enviando = Hilo(cliente.send, datos)
        enviando.start()
        conexion = aceptando.resultado_o_error()
        self._transportes.append(conexion)
        self.assertIsInstance(conexion, sack.SelectiveAck)
        self.assertEqual(conexion.recv(), datos)
        enviando.resultado_o_error()


class TestHallazgosDeUnFallo(UnFallo):

    def test_perder_el_ultimo_ack_hace_fallar_al_emisor(self):
        """HALLAZGO (sin arreglar): igual que en SW, se pierde el ACK del FIN.

        recv() retorna apenas entrega el FIN y nadie vuelve a ACKear. El emisor
        retransmite el FIN hasta agotar los reintentos y termina con error,
        aunque el receptor tiene el archivo completo. No hay TIME_WAIT.
        """
        cliente, conexion = self.conectados(policy_servidor=netsim.drop_acks_sack_nth(SEGMENTOS))
        datos = os.urandom(DATOS)
        recibiendo = Hilo(conexion.recv)
        recibiendo.start()
        enviando = Hilo(cliente.send, datos)
        enviando.start()

        self.assertEqual(recibiendo.resultado_o_error(), datos, "el receptor tiene todo")
        with self.assertRaises(ConnectionClosed) as cm:
            enviando.resultado_o_error()
        self.assertIn("Too many retries", str(cm.exception))
        self.assertEqual(self.copias_por_seq("cliente")[seq_del(SEGMENTOS - 1)], 1 + self.retries)


if __name__ == "__main__":
    unittest.main()
