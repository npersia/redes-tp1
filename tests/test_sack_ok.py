"""Selective ACK sin fallos: todo sale bien, en las dos direcciones."""

import os
import unittest

from base import SACK_TIMEOUT_EXACTO, SACKTestCase, netsim, sack

MAX = sack.MAX_PAYLOAD_SIZE
CWND = sack.CWND

TAMANIOS = {
    "1 byte": 1,
    "justo un segmento": MAX,
    "un segmento y un byte": MAX + 1,
    "menos que la ventana": MAX * (CWND - 1),
    "justo la ventana": MAX * CWND,
    "varias ventanas": MAX * CWND * 5 + 7,
}


class TestTodoBien(SACKTestCase):

    timeout = SACK_TIMEOUT_EXACTO

    def test_tamanios_en_las_dos_direcciones(self):
        for nombre_tam, tam in TAMANIOS.items():
            datos = os.urandom(tam)
            for (
                    direccion, emisor, receptor, rol_e, rol_r
            ) in self.direcciones():
                with self.subTest(tamanio=nombre_tam, direccion=direccion):
                    self.net.entries.clear()
                    recibido = self.transferir(emisor, receptor, datos)
                    self.assertEqual(recibido, datos)

                    copias = self.copias_por_seq(rol_e)
                    segmentos = -(-tam // MAX)
                    self.assertEqual(
                        len(copias),
                        segmentos,
                        f"cantidad de segmentos{self.volcado()}"
                    )
                    self.assertTrue(
                        all(n == 1 for n in copias.values()),
                        (
                            f"en una red limpia no se retransmite nada"
                            f"{self.volcado()}"
                        ),
                    )
                    self.assertVentanaRespetada(rol_e, rol_r)

    def test_archivo_vacio_manda_solo_el_fin(self):
        for direccion, emisor, receptor, rol_e, _ in self.direcciones():
            with self.subTest(direccion=direccion):
                self.net.entries.clear()
                self.assertEqual(self.transferir(emisor, receptor, b""), b"")
                datos = self.datos_en_el_cable(rol_e)
                self.assertEqual(len(datos), 1)
                self.assertPaquete(datos[0], FIN=1, payload=b"")

    def test_archivo_grande(self):
        datos = os.urandom(1 * 1024 * 1024 + 3)
        cliente, conexion = self.conectados()
        self.assertEqual(
            self.transferir(cliente, conexion, datos, timeout=60),
            datos
        )
        self.assertVentanaRespetada("cliente", "servidor")


class TestFormaDeLosSegmentos(SACKTestCase):

    timeout = SACK_TIMEOUT_EXACTO

    def setUp(self):
        super().setUp()
        self.cliente, self.conexion = self.conectados()
        self.datos = os.urandom(MAX * 3 + 10)
        self.transferir(self.cliente, self.conexion, self.datos)
        self.segmentos = self.datos_en_el_cable("cliente")

    def test_protocolo_y_version(self):
        for p in self.segmentos:
            self.assertPaquete(
                p,
                protocol=sack.SelectiveAck.PROTOCOL_ID,
                version=1
            )

    def test_seqs_consecutivos_por_bytes(self):
        seqs = [p["seq"] for p in self.segmentos]
        self.assertEqual(seqs, [1, 1 + MAX, 1 + 2 * MAX, 1 + 3 * MAX])

    def test_solo_el_ultimo_lleva_fin(self):
        self.assertEqual([p["FIN"] for p in self.segmentos], [0, 0, 0, 1])

    def test_payloads_al_maximo_salvo_el_ultimo(self):
        self.assertEqual(
            [len(p["payload"]) for p in self.segmentos], [MAX, MAX, MAX, 10]
        )

    def test_los_datos_llevan_el_ack_acumulativo(self):
        """Asi un peer trabado en el handshake lo puede cerrar con el
        primer dato."""
        for p in self.segmentos:
            self.assertPaquete(p, ACK=1, ack=self.cliente.receiver.rcv_next)

    def test_los_acks_son_acumulativos_y_crecientes(self):
        acks = [
            p["ack"]
            for p in self.net.wire("servidor")
            if netsim.es_ack_puro_sack_pkt(p)
        ]
        self.assertEqual(acks, sorted(acks))
        self.assertEqual(acks[-1], 1 + len(self.datos))

    def test_sin_huecos_los_acks_no_llevan_opcion_sack(self):
        for p in self.net.wire("servidor"):
            if netsim.es_ack_puro_sack_pkt(p):
                self.assertEqual(p["hlen"], 12)

    def test_los_numeros_de_secuencia_quedan_al_dia(self):
        self.assertEqual(self.cliente.sender.send_base, 1 + len(self.datos))
        self.assertTrue(self.cliente.sender.is_idle)
        self.assertEqual(self.conexion.receiver.rcv_next, 1 + len(self.datos))


class TestVariasTransferenciasPorConexion(SACKTestCase):

    def test_dos_send_seguidos_por_la_misma_conexion(self):
        """El seq sigue de un send() al siguiente, como en el protocolo de
        aplicacion."""
        cliente, conexion = self.conectados()
        pedido = b"DOWNLOAD x"
        self.assertEqual(self.transferir(cliente, conexion, pedido), pedido)
        respuesta = os.urandom(MAX * 2 + 1)
        self.assertEqual(
            self.transferir(conexion, cliente, respuesta),
            respuesta
        )
        otra = os.urandom(MAX + 5)
        self.assertEqual(self.transferir(cliente, conexion, otra), otra)


if __name__ == "__main__":
    unittest.main()
