"""Envio de datos: StopAndWait.send() (chunking, ACK, retransmision)."""

import time
import unittest

from base import ConnectionClosed, Hilo, SWTestCase, packet, sw
from netsim import drop_nth


MAX = sw.MAX_PAYLOAD_SIZE


class EmisorConPeer(SWTestCase):
    """Un StopAndWait ya 'conectado' a mano contra un peer crudo.

    Evita el handshake para poder inyectar exactamente las respuestas que
    queremos y ejercitar cada rama de send() por separado.
    """

    def emisor(self, seq=1000, exp=2000, policy=None):
        peer = self.peer()
        t = sw.StopAndWait("127.0.0.1", peer.addr[1])
        t.sock = sw.socket.socket(sw.socket.AF_INET, sw.socket.SOCK_DGRAM)
        t.sock.bind(("127.0.0.1", 0))
        t.sock.role = "emisor"
        t.sock.policy = policy
        t.remote_address = peer.addr
        t.sequence_number = seq
        t.exp_sequence_number = exp
        t.is_closed = False
        self._transportes.append(t)
        return t, peer

    def ack(self, peer, addr, n, seq=0):
        peer.send_pkt(addr, flags=packet.ACK_MASK, sequence_number=seq, ack=n)


class TestSendChunking(EmisorConPeer):

    def _enviar_y_ackear(self, datos, seq=1000):
        """Envia `datos` y va ACKeando todo; devuelve los paquetes vistos."""
        t, peer = self.emisor(seq=seq)
        hilo = Hilo(t.send, datos)
        hilo.start()

        vistos = []
        esperados = max(1, -(-len(datos) // MAX))
        for _ in range(esperados):
            p, addr = peer.recv(timeout=2.0)
            vistos.append(p)
            n = p["seq"] + (len(p["payload"]) or 1)
            self.ack(peer, addr, n)
        hilo.resultado_o_error()
        return t, vistos

    def test_un_chunk_chico_lleva_fin_y_avanza_el_seq(self):
        t, vistos = self._enviar_y_ackear(b"hola mundo")
        self.assertEqual(len(vistos), 1)
        self.assertPaquete(
            vistos[0],
            FIN=1,
            ACK=0,
            SYN=0,
            ERR=0,
            seq=1000,
            payload=b"hola mundo"
        )
        self.assertEqual(t.sequence_number, 1000 + len(b"hola mundo"))

    def test_payload_vacio_manda_un_paquete_con_fin_y_avanza_uno(self):
        t, vistos = self._enviar_y_ackear(b"")
        self.assertEqual(len(vistos), 1)
        self.assertPaquete(vistos[0], FIN=1, payload=b"", seq=1000)
        self.assertEqual(
            t.sequence_number,
            1001,
            "el paquete vacio cuenta como 1 byte"
            )

    def test_exactamente_max_payload_es_un_solo_chunk(self):
        datos = b"a" * MAX
        t, vistos = self._enviar_y_ackear(datos)
        self.assertEqual(len(vistos), 1)
        self.assertPaquete(vistos[0], FIN=1)
        self.assertEqual(len(vistos[0]["payload"]), MAX)
        self.assertEqual(t.sequence_number, 1000 + MAX)

    def test_max_payload_mas_uno_se_parte_en_dos(self):
        datos = bytes(range(256)) * 6  # 1536 > 1400
        t, vistos = self._enviar_y_ackear(datos)
        self.assertEqual(len(vistos), 2)
        self.assertPaquete(vistos[0], FIN=0, seq=1000)
        self.assertPaquete(vistos[1], FIN=1, seq=1000 + MAX)
        self.assertEqual(len(vistos[0]["payload"]), MAX)
        self.assertEqual(len(vistos[1]["payload"]), len(datos) - MAX)
        self.assertEqual(b"".join(p["payload"] for p in vistos), datos)
        self.assertEqual(t.sequence_number, 1000 + len(datos))

    def test_multiplo_exacto_de_max_payload(self):
        datos = b"z" * (MAX * 3)
        t, vistos = self._enviar_y_ackear(datos)
        self.assertEqual(len(vistos), 3)
        self.assertEqual(
            [p["FIN"] for p in vistos],
            [0, 0, 1],
            "FIN solo en el ultimo chunk"
        )
        self.assertEqual(
            [p["seq"] for p in vistos],
            [
                1000,
                1000 + MAX,
                1000 + 2 * MAX
            ]
        )

    def test_los_seq_son_contiguos_en_bytes(self):
        datos = b"x" * (MAX * 2 + 7)
        t, vistos = self._enviar_y_ackear(datos)
        esperado = 1000
        for p in vistos:
            self.assertEqual(p["seq"], esperado)
            esperado += len(p["payload"])
        self.assertEqual(t.sequence_number, esperado)

    def test_dos_send_consecutivos_continuan_la_numeracion(self):
        t, peer = self.emisor(seq=1000)

        for datos in (b"primero", b"segundo!!"):
            hilo = Hilo(t.send, datos)
            hilo.start()
            p, addr = peer.recv(timeout=2.0)
            self.ack(peer, addr, p["seq"] + len(p["payload"]))
            hilo.resultado_o_error()

        self.assertEqual(
            t.sequence_number,
            1000 + len(b"primero") + len(b"segundo!!")
            )

    def test_cada_send_marca_fin_en_su_ultimo_chunk(self):
        """OBSERVACION: FIN no cierra la conexion, solo delimita el mensaje.

        Dos send() sobre la misma conexion emiten dos FIN. El receptor
        retorna de recv() en el primero, asi que el protocolo es
        mensaje-a-mensaje, no un stream.
        """
        t, peer = self.emisor(seq=1000)
        fins = []
        for datos in (b"uno", b"dos"):
            hilo = Hilo(t.send, datos)
            hilo.start()
            p, addr = peer.recv(timeout=2.0)
            fins.append(p["FIN"])
            self.ack(peer, addr, p["seq"] + len(p["payload"]))
            hilo.resultado_o_error()
        self.assertEqual(fins, [1, 1])


class TestSendRetransmision(EmisorConPeer):

    def test_retransmite_si_se_pierde_el_paquete_de_datos(self):
        t, peer = self.emisor(policy=drop_nth(1))
        hilo = Hilo(t.send, b"payload")
        hilo.start()

        t0 = time.monotonic()
        p, addr = peer.recv(timeout=2.0)
        self.assertGreaterEqual(
            time.monotonic() - t0,
            self.timeout * 0.8,
            "la retransmision espera el timeout",
            )
        self.assertEqual(p["payload"], b"payload")
        self.ack(peer, addr, p["seq"] + len(p["payload"]))
        hilo.resultado_o_error()
        self.assertEqual(t.sock.tx, 2, "un envio original + una retransmision")

    def test_la_retransmision_es_byte_a_byte_identica(self):
        t, peer = self.emisor()
        hilo = Hilo(t.send, b"identico")
        hilo.start()
        p1, _ = peer.recv(timeout=2.0)
        p2, addr = peer.recv(timeout=2.0)  # no ackeamos la primera
        self.assertEqual(p1["raw"], p2["raw"])
        self.ack(peer, addr, p1["seq"] + len(p1["payload"]))
        hilo.resultado_o_error()

    def test_retransmite_si_se_pierde_el_ack(self):
        t, peer = self.emisor()
        hilo = Hilo(t.send, b"datos")
        hilo.start()

        p, addr = peer.recv(timeout=2.0)  # lo recibimos y "perdemos" el ACK
        p2, addr = peer.recv(timeout=2.0)  # el emisor reintenta
        self.assertEqual(p["raw"], p2["raw"])
        self.ack(peer, addr, p["seq"] + len(p["payload"]))
        hilo.resultado_o_error()
        self.assertEqual(t.sequence_number, 1000 + len(b"datos"))

    def test_agota_reintentos_y_levanta_connectionclosed(self):
        t, peer = self.emisor()
        t0 = time.monotonic()
        with self.assertRaises(ConnectionClosed) as cm:
            t.send(b"nadie contesta")
        transcurrido = time.monotonic() - t0

        self.assertIn("Connexion lost", str(cm.exception))
        self.assertEqual(
            t.sock.tx,
            self.retries,
            f"exactamente RETRIES={self.retries} intentos"
        )
        self.assertGreaterEqual(
            transcurrido,
            self.timeout * self.retries * 0.8
            )
        self.assertEqual(
            t.sequence_number,
            1000,
            "el seq no avanza si no hubo ACK"
            )

    def test_el_reintento_se_cuenta_por_chunk_no_por_mensaje(self):
        """Cada chunk arranca con su propio presupuesto de RETRIES."""
        t, peer = self.emisor()
        datos = b"a" * MAX + b"b" * 10
        hilo = Hilo(t.send, datos)
        hilo.start()

        # perdemos el primer intento de cada chunk (no ackeamos la 1ra copia)
        for _ in range(2):
            peer.recv(timeout=2.0)
            p, addr = peer.recv(timeout=2.0)
            self.ack(peer, addr, p["seq"] + len(p["payload"]))
        hilo.resultado_o_error()
        self.assertEqual(t.sequence_number, 1000 + len(datos))

    def test_un_ack_atrasado_no_avanza_el_seq_y_provoca_reenvio_inmediato(
            self
    ):
        """HALLAZGO (sin arreglar): un ACK que no matchea reenvia sin consumir
        reintentos.

        En send(), `ret` solo crece en `except socket.timeout`. Si llega un
        ACK viejo o duplicado, ninguna rama matchea, el while reitera y
        vuelve a hacer sendto de inmediato: tormenta de retransmisiones que
        ademas nunca termina.
        """
        t, peer = self.emisor(seq=1000)
        hilo = Hilo(t.send, b"dato")
        hilo.start()

        copias = 0
        fin = time.monotonic() + self.timeout * self.retries * 3
        while time.monotonic() < fin:
            p, addr = peer.try_recv(0.2)
            if p is None:
                break
            copias += 1
            self.ack(peer, addr, 12345)  # ACK que nunca corresponde

        self.assertTrue(hilo.is_alive(), "send() sigue girando, no aborta")
        self.assertGreater(
            copias,
            self.retries,
            f"{copias} reenvios, mas que los {self.retries} reintentos",
            )
        self.assertEqual(t.sequence_number, 1000, "el seq nunca avanzo")
        t.shutdown()

    def test_un_paquete_de_otra_direccion_tambien_provoca_reenvio_sin_contar(
            self
    ):
        """HALLAZGO (sin arreglar): el `continue` por `addr != remote_address`
        no cuenta reintento."""
        t, peer = self.emisor(seq=1000)
        intruso = self.peer()
        hilo = Hilo(t.send, b"dato")
        hilo.start()

        p, addr = peer.recv(timeout=2.0)
        local = t.sock.getsockname()
        antes = t.sock.tx

        # ACKs validos pero de un tercero: send() los descarta por direccion
        for _ in range(5):
            intruso.send_pkt(
                local, flags=packet.ACK_MASK, ack=p["seq"] + len(p["payload"])
                )
        time.sleep(0.02)
        self.assertGreater(
            t.sock.tx,
            antes,
            "cada paquete ajeno dispara un reenvio sin consumir reintentos",
            )
        self.assertEqual(t.sequence_number, 1000, "no avanzo con el ACK ajeno")

        # el ACK legitimo si cierra el chunk
        p2, addr2 = peer.recv(timeout=2.0)
        self.ack(peer, addr2, p2["seq"] + len(p2["payload"]))
        hilo.resultado_o_error()
        self.assertEqual(t.sequence_number, 1004)

    def test_un_ack_duplicado_posterior_no_rompe_el_chunk_siguiente(self):
        t, peer = self.emisor(seq=1000)
        datos = b"c" * (MAX + 5)
        hilo = Hilo(t.send, datos)
        hilo.start()

        p1, addr = peer.recv(timeout=2.0)
        self.ack(peer, addr, p1["seq"] + MAX)
        self.ack(peer, addr, p1["seq"] + MAX)  # duplicado

        p2, addr = peer.recv(timeout=2.0)
        self.assertEqual(p2["seq"], 1000 + MAX)
        self.ack(peer, addr, p2["seq"] + len(p2["payload"]))
        hilo.resultado_o_error()
        self.assertEqual(t.sequence_number, 1000 + len(datos))


class TestSendErrores(EmisorConPeer):

    def test_flag_err_aborta_con_connectionclosed(self):
        t, peer = self.emisor()
        hilo = Hilo(t.send, b"dato")
        hilo.start()
        p, addr = peer.recv(timeout=2.0)
        peer.send_pkt(addr, flags=packet.ERR_MASK, sequence_number=0, ack=0)

        with self.assertRaises(ConnectionClosed) as cm:
            hilo.resultado_o_error()
        self.assertIn("ERR", str(cm.exception))

    def test_err_junto_con_ack_valido_tiene_prioridad(self):
        t, peer = self.emisor(seq=1000)
        hilo = Hilo(t.send, b"dato")
        hilo.start()
        p, addr = peer.recv(timeout=2.0)
        peer.send_pkt(
            addr,
            flags=packet.ERR_MASK | packet.ACK_MASK,
            sequence_number=0,
            ack=1004
        )
        with self.assertRaises(ConnectionClosed):
            hilo.resultado_o_error()

    def test_send_con_la_conexion_cerrada(self):
        t, peer = self.emisor()
        t.is_closed = True
        with self.assertRaises(ConnectionClosed) as cm:
            t.send(b"x")
        self.assertIn("closed", str(cm.exception))

    def test_send_sin_socket(self):
        t = sw.StopAndWait("127.0.0.1", 1234)
        t.is_closed = False
        t.sock = None
        with self.assertRaises(ConnectionClosed):
            t.send(b"x")

    def test_send_sobre_transporte_recien_creado(self):
        t = sw.StopAndWait("127.0.0.1", 1234)
        with self.assertRaises(ConnectionClosed):
            t.send(b"x")

    def test_una_respuesta_truncada_se_descarta(self):
        """Antes disparaba IndexError en get_header_flags."""
        t, peer = self.emisor(seq=1000)
        hilo = Hilo(t.send, b"dato")
        hilo.start()
        p, addr = peer.recv(timeout=2.0)

        peer.send(b"\x00", addr)
        peer.send(b"", addr)
        self.assertTrue(hilo.is_alive(), "send sobrevive a la basura")

        p2, addr2 = peer.recv(timeout=2.0)
        self.ack(peer, addr2, p2["seq"] + len(p2["payload"]))
        hilo.resultado_o_error()
        self.assertEqual(t.sequence_number, 1004)


if __name__ == "__main__":
    unittest.main()
