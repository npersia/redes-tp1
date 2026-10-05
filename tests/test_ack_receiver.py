"""Estado del receptor de SACK: entrega en orden, buffer y bloques a anunciar.
"""

import unittest

import base  # noqa: F401 - arma el sys.path hacia src/
from lib.protocols.selective_ack.ack_receiver import (
    BUFFERED,
    DELIVERED,
    DUPLICATE,
    OUT_OF_WINDOW,
    ACKReceiver,
)

INICIO = 1
RWIND = 400


def seg(seq, largo, relleno=None):
    """(seq, n_bytes, payload) con un payload reconocible por posicion."""
    relleno = relleno if relleno is not None else bytes([seq % 256])
    return seq, largo, relleno * largo


class TestEntregaEnOrden(unittest.TestCase):

    def setUp(self):
        self.r = ACKReceiver(INICIO, RWIND)

    def test_el_esperado_se_entrega(self):
        entregado, estado = self.r.accept(*seg(1, 100))
        self.assertEqual(estado, DELIVERED)
        self.assertEqual(len(entregado), 100)
        self.assertEqual(self.r.rcv_next, 101)

    def test_varios_seguidos(self):
        for seq in (1, 101, 201):
            _, estado = self.r.accept(*seg(seq, 100))
            self.assertEqual(estado, DELIVERED)
        self.assertEqual(self.r.rcv_next, 301)

    def test_fin_sin_payload_avanza_uno(self):
        entregado, estado = self.r.accept(1, 1, b"")
        self.assertEqual(estado, DELIVERED)
        self.assertEqual(entregado, b"")
        self.assertEqual(self.r.rcv_next, 2)

    def test_sin_huecos_no_hay_bloques(self):
        self.r.accept(*seg(1, 100))
        self.assertEqual(self.r.blocks(), [])


class TestBufferFueraDeOrden(unittest.TestCase):

    def setUp(self):
        self.r = ACKReceiver(INICIO, RWIND)

    def test_adelantado_queda_en_el_buffer(self):
        entregado, estado = self.r.accept(*seg(101, 100))
        self.assertEqual(estado, BUFFERED)
        self.assertEqual(entregado, b"")
        self.assertEqual(self.r.rcv_next, 1, "no avanza hasta llenar el hueco")
        self.assertEqual(self.r.blocks(), [(101, 201)])

    def test_llenar_el_hueco_entrega_toda_la_cadena(self):
        self.r.accept(*seg(201, 100, b"c"))
        self.r.accept(*seg(101, 100, b"b"))
        entregado, estado = self.r.accept(*seg(1, 100, b"a"))
        self.assertEqual(estado, DELIVERED)
        self.assertEqual(entregado, b"a" * 100 + b"b" * 100 + b"c" * 100)
        self.assertEqual(self.r.rcv_next, 301)
        self.assertEqual(self.r.out_of_order, {})
        self.assertEqual(self.r.blocks(), [])

    def test_llenar_un_hueco_entrega_hasta_el_siguiente_hueco(self):
        self.r.accept(*seg(101, 100))
        self.r.accept(*seg(301, 100))
        entregado, _ = self.r.accept(*seg(1, 100))
        self.assertEqual(len(entregado), 200)
        self.assertEqual(self.r.rcv_next, 201)
        self.assertEqual(self.r.blocks(), [(301, 401)])

    def test_bloques_adyacentes_se_anuncian_unidos(self):
        self.r.accept(*seg(101, 100))
        self.r.accept(*seg(201, 100))
        self.assertEqual(self.r.blocks(), [(101, 301)])

    def test_bloques_salen_ordenados(self):
        self.r.accept(*seg(301, 50))
        self.r.accept(*seg(101, 50))
        self.assertEqual(self.r.blocks(), [(101, 151), (301, 351)])

    def test_option_blocks_respeta_el_limite(self):
        for seq in (101, 201, 301):
            self.r.accept(*seg(seq, 50))
        self.assertEqual(self.r.option_blocks(2), [(101, 151), (201, 251)])
        self.assertEqual(self.r.option_blocks(10), self.r.blocks())


class TestDuplicados(unittest.TestCase):

    def setUp(self):
        self.r = ACKReceiver(INICIO, RWIND)

    def test_repetido_de_algo_ya_entregado(self):
        self.r.accept(*seg(1, 100))
        entregado, estado = self.r.accept(*seg(1, 100))
        self.assertEqual(estado, DUPLICATE)
        self.assertEqual(entregado, b"")
        self.assertEqual(self.r.rcv_next, 101)

    def test_repetido_de_algo_en_el_buffer(self):
        self.r.accept(*seg(101, 100, b"x"))
        entregado, estado = self.r.accept(*seg(101, 100, b"y"))
        self.assertEqual(estado, DUPLICATE)
        self.assertEqual(entregado, b"")
        self.assertEqual(
            self.r.out_of_order[101][1],
            b"x" * 100,
            "se queda con la primera copia"
        )

    def test_fin_repetido(self):
        self.r.accept(1, 1, b"")
        _, estado = self.r.accept(1, 1, b"")
        self.assertEqual(estado, DUPLICATE)


class TestSolapamiento(unittest.TestCase):

    def test_solapamiento_parcial_entrega_solo_lo_nuevo(self):
        r = ACKReceiver(INICIO, RWIND)
        r.accept(1, 100, b"a" * 100)
        # retransmision que arranca 50 bytes antes de lo que falta
        entregado, estado = r.accept(51, 100, b"a" * 50 + b"b" * 50)
        self.assertEqual(estado, DELIVERED)
        self.assertEqual(entregado, b"b" * 50)
        self.assertEqual(r.rcv_next, 151)


class TestVentanaDeRecepcion(unittest.TestCase):

    def setUp(self):
        self.r = ACKReceiver(INICIO, RWIND)

    def test_justo_antes_del_borde_entra(self):
        _, estado = self.r.accept(*seg(INICIO + RWIND - 1, 1))
        self.assertEqual(estado, BUFFERED)

    def test_en_el_borde_se_descarta(self):
        entregado, estado = self.r.accept(*seg(INICIO + RWIND, 10))
        self.assertEqual(estado, OUT_OF_WINDOW)
        self.assertEqual(entregado, b"")
        self.assertEqual(self.r.out_of_order, {})
        self.assertEqual(self.r.blocks(), [])

    def test_la_ventana_se_mueve_con_rcv_next(self):
        self.r.accept(*seg(1, 100))
        _, estado = self.r.accept(*seg(INICIO + RWIND, 10))
        self.assertEqual(
            estado,
            BUFFERED,
            "con rcv_next=101 el borde paso a 501"
        )


if __name__ == "__main__":
    unittest.main()
