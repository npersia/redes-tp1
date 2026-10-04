"""Recepcion de datos: StopAndWait.recv() (orden, duplicados, ACK, FIN)."""

import time
import unittest

from base import ConnectionClosed, Hilo, SWTestCase, packet, sw


MAX = sw.MAX_PAYLOAD_SIZE


class ReceptorConPeer(SWTestCase):

    def receptor(self, seq=2000, exp=1000):
        peer = self.peer()
        t = sw.StopAndWait("127.0.0.1", peer.addr[1])
        t.sock = sw.socket.socket(sw.socket.AF_INET, sw.socket.SOCK_DGRAM)
        t.sock.bind(("127.0.0.1", 0))
        t.sock.role = "receptor"
        t.remote_address = peer.addr
        t.sequence_number = seq
        t.exp_sequence_number = exp
        t.is_closed = False
        self._transportes.append(t)
        return t, peer

    def datos(self, peer, destino, seq, payload, fin=False):
        peer.send_pkt(
            destino,
            flags=packet.FIN_MASK if fin else 0,
            sequence_number=seq,
            payload=payload,
        )


class TestRecvCaminoFeliz(ReceptorConPeer):

    def test_un_paquete_con_fin_retorna_el_payload(self):
        t, peer = self.receptor(seq=2000, exp=1000)
        hilo = Hilo(t.recv)
        hilo.start()

        self.datos(peer, t.sock.getsockname(), 1000, b"contenido", fin=True)
        self.assertEqual(hilo.resultado_o_error(), b"contenido")

        ack, _ = peer.recv(timeout=2.0)
        self.assertPaquete(
            ack, ACK=1, SYN=0, FIN=0, seq=2000, ack=1000 + len(b"contenido")
        )
        self.assertEqual(t.exp_sequence_number, 1000 + len(b"contenido"))

    def test_reensambla_varios_paquetes_en_orden(self):
        t, peer = self.receptor(exp=1000)
        hilo = Hilo(t.recv)
        hilo.start()
        destino = t.sock.getsockname()

        trozos = [b"a" * MAX, b"b" * MAX, b"c" * 13]
        seq = 1000
        for i, trozo in enumerate(trozos):
            self.datos(peer, destino, seq, trozo, fin=(i == len(trozos) - 1))
            ack, _ = peer.recv(timeout=2.0)
            seq += len(trozo)
            self.assertPaquete(ack, ACK=1, ack=seq)

        self.assertEqual(hilo.resultado_o_error(), b"".join(trozos))
        self.assertEqual(t.exp_sequence_number, seq)

    def test_payload_vacio_con_fin_retorna_bytes_vacios_y_avanza_uno(self):
        t, peer = self.receptor(exp=1000)
        hilo = Hilo(t.recv)
        hilo.start()
        self.datos(peer, t.sock.getsockname(), 1000, b"", fin=True)

        self.assertEqual(hilo.resultado_o_error(), b"")
        ack, _ = peer.recv(timeout=2.0)
        self.assertPaquete(ack, ACK=1, ack=1001)
        self.assertEqual(t.exp_sequence_number, 1001)

    def test_el_ack_es_acumulativo_en_bytes(self):
        t, peer = self.receptor(exp=500)
        hilo = Hilo(t.recv)
        hilo.start()
        destino = t.sock.getsockname()

        self.datos(peer, destino, 500, b"12345")
        ack1, _ = peer.recv(timeout=2.0)
        self.assertEqual(ack1["ack"], 505)

        self.datos(peer, destino, 505, b"678", fin=True)
        ack2, _ = peer.recv(timeout=2.0)
        self.assertEqual(ack2["ack"], 508)
        self.assertEqual(hilo.resultado_o_error(), b"12345678")


class TestRecvDuplicadosYDesorden(ReceptorConPeer):

    def test_un_duplicado_se_reackea_y_no_se_duplica_el_payload(self):
        t, peer = self.receptor(exp=1000)
        hilo = Hilo(t.recv)
        hilo.start()
        destino = t.sock.getsockname()

        self.datos(peer, destino, 1000, b"hola")
        ack1, _ = peer.recv(timeout=2.0)
        self.assertEqual(ack1["ack"], 1004)

        self.datos(peer, destino, 1000, b"hola")  # retransmision del emisor
        ack2, _ = peer.recv(timeout=2.0)
        self.assertEqual(ack2["ack"], 1004, "re-ACK acumulativo, sin avanzar")

        self.datos(peer, destino, 1004, b"!", fin=True)
        peer.recv(timeout=2.0)
        self.assertEqual(
            hilo.resultado_o_error(),
            b"hola!",
            "el duplicado no se agrego al buffer"
        )

    def test_un_duplicado_muy_viejo_tambien_se_reackea(self):
        t, peer = self.receptor(exp=1000)
        hilo = Hilo(t.recv)
        hilo.start()
        destino = t.sock.getsockname()

        self.datos(peer, destino, 1000, b"abcdefghij")
        peer.recv(timeout=2.0)
        self.datos(peer, destino, 1, b"basura muy vieja")
        ack, _ = peer.recv(timeout=2.0)
        self.assertPaquete(ack, ACK=1, ack=1010)

        self.datos(peer, destino, 1010, b"", fin=True)
        peer.recv(timeout=2.0)
        self.assertEqual(hilo.resultado_o_error(), b"abcdefghij")

    def test_un_paquete_futuro_se_descarta_en_silencio(self):
        """HALLAZGO: ante un hueco no se manda ningun ACK ni NAK.

        La rama `seq > exp_sequence_number` no existe: el paquete se pierde
        sin respuesta. La recuperacion depende enteramente del timeout del
        emisor (no hay ACK duplicado que la acelere).
        """
        t, peer = self.receptor(exp=1000)
        hilo = Hilo(t.recv)
        hilo.start()
        destino = t.sock.getsockname()

        self.datos(peer, destino, 5000, b"fuera de orden")
        self.assertIsNone(peer.try_recv(0.3)[0], "no contesto nada")
        self.assertEqual(t.exp_sequence_number, 1000, "no avanzo")
        self.assertTrue(hilo.is_alive())

        self.datos(peer, destino, 1000, b"ok", fin=True)
        peer.recv(timeout=2.0)
        self.assertEqual(
            hilo.resultado_o_error(),
            b"ok",
            "el paquete futuro no quedo en el buffer"
        )

    def test_un_paquete_de_otra_direccion_se_ignora(self):
        t, peer = self.receptor(exp=1000)
        intruso = self.peer()
        hilo = Hilo(t.recv)
        hilo.start()
        destino = t.sock.getsockname()

        self.datos(intruso, destino, 1000, b"inyectado", fin=True)
        self.assertIsNone(intruso.try_recv(0.3)[0])
        self.assertTrue(hilo.is_alive(), "recv no acepto datos de un tercero")
        self.assertEqual(t.exp_sequence_number, 1000)

        self.datos(peer, destino, 1000, b"legitimo", fin=True)
        peer.recv(timeout=2.0)
        self.assertEqual(hilo.resultado_o_error(), b"legitimo")

    def test_reentrega_del_syn_ack_se_contesta_sin_cortar_la_recepcion(self):
        t, peer = self.receptor(seq=2000, exp=1000)
        hilo = Hilo(t.recv)
        hilo.start()
        destino = t.sock.getsockname()

        peer.send_pkt(
            destino,
            flags=packet.SYN_MASK | packet.ACK_MASK,
            sequence_number=999,
            ack=2000,
        )
        ack, _ = peer.recv(timeout=2.0)
        self.assertPaquete(ack, ACK=1, SYN=0, seq=2000, ack=1000)
        self.assertTrue(hilo.is_alive(), "sigue esperando datos")
        self.assertEqual(
            t.exp_sequence_number,
            1000,
            "el SYN-ACK no avanza el exp"
        )

        self.datos(peer, destino, 1000, b"datos", fin=True)
        peer.recv(timeout=2.0)
        self.assertEqual(hilo.resultado_o_error(), b"datos")

    def test_un_ack_puro_del_par_se_descarta_sin_respuesta(self):
        """Un ACK cruzado tiene seq del emisor, que no coincide con exp."""
        t, peer = self.receptor(seq=2000, exp=1000)
        hilo = Hilo(t.recv)
        hilo.start()
        destino = t.sock.getsockname()

        peer.send_pkt(
            destino,
            flags=packet.ACK_MASK,
            sequence_number=7777,
            ack=2000
        )
        self.assertIsNone(peer.try_recv(0.3)[0], "no genera respuesta")
        self.assertTrue(hilo.is_alive())

        self.datos(peer, destino, 1000, b"x", fin=True)
        peer.recv(timeout=2.0)
        self.assertEqual(hilo.resultado_o_error(), b"x")


class TestRecvErroresYCierre(ReceptorConPeer):

    def test_flag_err_aborta_con_connectionclosed(self):
        t, peer = self.receptor(exp=1000)
        hilo = Hilo(t.recv)
        hilo.start()
        peer.send_pkt(
            t.sock.getsockname(),
            flags=packet.ERR_MASK,
            sequence_number=1000
        )
        with self.assertRaises(ConnectionClosed) as cm:
            hilo.resultado_o_error()
        self.assertIn("ERR", str(cm.exception))

    def test_err_descarta_lo_ya_recibido(self):
        t, peer = self.receptor(exp=1000)
        hilo = Hilo(t.recv)
        hilo.start()
        destino = t.sock.getsockname()
        self.datos(peer, destino, 1000, b"parcial")
        peer.recv(timeout=2.0)
        peer.send_pkt(destino, flags=packet.ERR_MASK, sequence_number=1007)
        with self.assertRaises(ConnectionClosed):
            hilo.resultado_o_error()

    def test_recv_con_la_conexion_cerrada(self):
        t, peer = self.receptor()
        t.is_closed = True
        with self.assertRaises(ConnectionClosed):
            t.recv()

    def test_recv_sin_socket(self):
        t = sw.StopAndWait("127.0.0.1", 1234)
        t.is_closed = False
        t.sock = None
        with self.assertRaises(ConnectionClosed):
            t.recv()

    def test_shutdown_desde_otro_hilo_corta_recv(self):
        """recv() esta bloqueado con timeout, asi que el shutdown si lo saca.
        """
        t, peer = self.receptor()
        hilo = Hilo(t.recv)
        hilo.start()
        time.sleep(self.timeout * 2)
        t.shutdown()

        self.assertTrue(hilo.esperar(2.0), "recv quedo colgado")
        self.assertIsInstance(hilo.error, ConnectionClosed)

    def test_recv_nunca_se_rinde_si_el_par_desaparece(self):
        """HALLAZGO (sin arreglar): recv() no tiene presupuesto de reintentos.

        `except socket.timeout: continue` gira indefinidamente. Un cliente
        que se va sin avisar deja el hilo del servidor colgado para siempre
        (fuga de hilo + socket por cada transferencia abortada).
        """
        t, peer = self.receptor()
        hilo = Hilo(t.recv)
        hilo.start()
        time.sleep(self.timeout * (self.retries + 3))
        self.assertTrue(
            hilo.is_alive(),
            "sigue esperando tras muchos timeouts"
        )
        t.shutdown()
        hilo.esperar(2.0)

    def test_los_datagramas_truncados_se_descartan(self):
        """Antes disparaban IndexError en get_header_flags."""
        t, peer = self.receptor(exp=1000)
        hilo = Hilo(t.recv)
        hilo.start()
        destino = t.sock.getsockname()

        for basura in (b"", b"\x00", b"\x11\x40\x0c"):
            peer.send(basura, destino)
        self.assertTrue(hilo.is_alive(), "recv sobrevive a la basura")

        self.datos(peer, destino, 1000, b"ok", fin=True)
        peer.recv(timeout=2.0)
        self.assertEqual(hilo.resultado_o_error(5.0), b"ok")

    def test_recv_despues_de_fin_no_recuerda_nada(self):
        """HALLAZGO (sin arreglar): si se pierde el ACK del FIN, el emisor
        queda solo.

        recv() retorna apenas procesa el FIN. La retransmision del FIN que
        haga el emisor (porque no le llego el ACK) ya no encuentra a nadie
        escuchando: no se re-ACKea. No hay estado tipo TIME_WAIT.
        """
        t, peer = self.receptor(exp=1000)
        hilo = Hilo(t.recv)
        hilo.start()
        destino = t.sock.getsockname()

        self.datos(peer, destino, 1000, b"final", fin=True)
        self.assertEqual(hilo.resultado_o_error(), b"final")
        peer.recv(timeout=2.0)  # el ACK que "se pierde"

        # el emisor retransmite el FIN: nadie lo re-ACKea
        self.datos(peer, destino, 1000, b"final", fin=True)
        self.assertIsNone(
            peer.try_recv(0.3)[0], "el FIN retransmitido quedo sin respuesta"
        )


if __name__ == "__main__":
    unittest.main()
