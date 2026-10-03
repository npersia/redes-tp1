"""Cancelacion deliberada: el flag CANCEL (bit 3 de FLAGS) junto con ERR.

Un ERR solo dice "algo se rompio". ERR+CANCEL dice "el usuario apreto Enter",
que es una condicion distinta y el otro extremo la tiene que poder informar
como tal en lugar de hablar de un error.
"""

import time
import unittest

from base import ConnectionClosed, Hilo, SWTestCase, TransferCancelled, packet, bt, sw


MAX = sw.MAX_PAYLOAD_SIZE


class ConPeer(SWTestCase):
    """Un StopAndWait 'conectado' a mano contra un peer crudo."""

    def transporte(self, seq=1000, exp=2000):
        peer = self.peer()
        t = sw.StopAndWait("127.0.0.1", peer.addr[1])
        t.sock = sw.socket.socket(sw.socket.AF_INET, sw.socket.SOCK_DGRAM)
        t.sock.bind(("127.0.0.1", 0))
        t.sock.role = "local"
        t.remote_address = peer.addr
        t.sequence_number = seq
        t.exp_sequence_number = exp
        t.is_closed = False
        self._transportes.append(t)
        return t, peer


class TestAvisoDeCancelacion(ConPeer):
    """Lo que sale al cable cuando este extremo cancela."""

    def test_notify_abort_manda_err_con_cancel(self):
        t, peer = self.transporte(seq=1000, exp=2000)
        t.notify_abort()

        vistos = peer.drain()
        self.assertEqual(len(vistos), bt.ABORT_NOTICES,
                         f"esperaba {bt.ABORT_NOTICES} avisos{self.volcado()}")
        for aviso in vistos:
            self.assertPaquete(aviso, ERR=1, CANCEL=1, SYN=0, FIN=0,
                               seq=1000, ack=2000, payload=b"")

    def test_cancelar_durante_send_avisa_con_cancel(self):
        t, peer = self.transporte(seq=1000)
        hilo = Hilo(t.send, b"z" * MAX * 2)
        hilo.start()

        peer.recv(timeout=2.0)          # primer chunk, no lo ACKeo
        t.cancel()

        with self.assertRaises(TransferCancelled):
            hilo.resultado_o_error()

        avisos = [p for p in peer.drain() if p["ERR"]]
        self.assertTrue(avisos, f"no salio ningun aviso de aborto{self.volcado()}")
        for aviso in avisos:
            self.assertPaquete(aviso, ERR=1, CANCEL=1)

    def test_cancelar_durante_recv_avisa_con_cancel(self):
        t, peer = self.transporte(exp=2000)
        t.cancel()

        with self.assertRaises(TransferCancelled):
            t.recv()

        avisos = [p for p in peer.drain() if p["ERR"]]
        self.assertTrue(avisos, f"no salio ningun aviso de aborto{self.volcado()}")
        for aviso in avisos:
            self.assertPaquete(aviso, ERR=1, CANCEL=1)


class TestRecepcionDeCancelacion(ConPeer):
    """Como se interpreta un ERR+CANCEL que llega, frente a un ERR pelado."""

    def test_recv_con_err_cancel_es_transfercancelled(self):
        t, peer = self.transporte(exp=1000)
        hilo = Hilo(t.recv)
        hilo.start()

        peer.send_pkt(t.sock.getsockname(),
                      flags=packet.ERR_MASK | packet.CANCEL_MASK,
                      sequence_number=1000)

        with self.assertRaises(TransferCancelled) as cm:
            hilo.resultado_o_error()
        self.assertIn("cancel", str(cm.exception).lower())

    def test_recv_con_err_pelado_sigue_siendo_error(self):
        t, peer = self.transporte(exp=1000)
        hilo = Hilo(t.recv)
        hilo.start()

        peer.send_pkt(t.sock.getsockname(), flags=packet.ERR_MASK,
                      sequence_number=1000)

        with self.assertRaises(ConnectionClosed) as cm:
            hilo.resultado_o_error()
        self.assertNotIsInstance(cm.exception, TransferCancelled)

    def test_send_con_err_cancel_es_transfercancelled(self):
        t, peer = self.transporte(seq=1000)
        hilo = Hilo(t.send, b"dato")
        hilo.start()

        _, addr = peer.recv(timeout=2.0)
        peer.send_pkt(addr, flags=packet.ERR_MASK | packet.CANCEL_MASK,
                      sequence_number=0, ack=0)

        with self.assertRaises(TransferCancelled) as cm:
            hilo.resultado_o_error()
        self.assertIn("cancel", str(cm.exception).lower())

    def test_send_con_err_pelado_sigue_siendo_error(self):
        t, peer = self.transporte(seq=1000)
        hilo = Hilo(t.send, b"dato")
        hilo.start()

        _, addr = peer.recv(timeout=2.0)
        peer.send_pkt(addr, flags=packet.ERR_MASK, sequence_number=0, ack=0)

        with self.assertRaises(ConnectionClosed) as cm:
            hilo.resultado_o_error()
        self.assertNotIsInstance(cm.exception, TransferCancelled)


class TestCancelacionEntreExtremosReales(SWTestCase):
    """Handshake de verdad y cancelacion en cada direccion.

    `retries` alto a proposito: el que espera no se tiene que rendir por
    timeout antes de que le llegue el aviso; queremos ver el ERR+CANCEL
    llegando, no los reintentos agotandose.
    """

    retries = 100

    def test_el_que_recibe_cancela_y_el_que_manda_se_entera(self):
        cliente, conexion = self.conectados()

        enviando = Hilo(conexion.send, b"z" * MAX * 3)
        enviando.start()

        cliente.cancel()
        recibiendo = Hilo(cliente.recv)
        recibiendo.start()

        with self.assertRaises(TransferCancelled):
            recibiendo.resultado_o_error()
        with self.assertRaises(TransferCancelled) as cm:
            enviando.resultado_o_error()
        self.assertIn("otro extremo", str(cm.exception))

    def test_el_que_manda_cancela_y_el_que_recibe_se_entera(self):
        cliente, conexion = self.conectados()

        recibiendo = Hilo(cliente.recv)
        recibiendo.start()

        conexion.cancel()
        enviando = Hilo(conexion.send, b"z" * MAX * 3)
        enviando.start()

        with self.assertRaises(TransferCancelled):
            enviando.resultado_o_error()
        with self.assertRaises(TransferCancelled) as cm:
            recibiendo.resultado_o_error()
        self.assertIn("otro extremo", str(cm.exception))


class TestCierreForzadoEntreExtremos(SWTestCase):
    """shutdown() de un lado: el otro se entera por el aviso, no por timeout.

    `retries` alto a proposito. Con el presupuesto por defecto el que espera
    podria rendirse por timeout y el test pasaria por el motivo equivocado
    (ConnectionClosed en vez de TransferCancelled); queremos ver el ERR+CANCEL
    llegando.
    """

    retries = 100

    def test_el_receptor_cierra_y_el_que_manda_se_entera(self):
        """Es el Enter en el servidor: Dispatcher._stop() -> connection.shutdown()."""
        cliente, conexion = self.conectados()

        enviando = Hilo(cliente.send, b"z" * MAX * 3)
        enviando.start()
        time.sleep(self.timeout * 2)   # que el send() ya este esperando el ACK

        conexion.shutdown()

        with self.assertRaises(TransferCancelled) as cm:
            enviando.resultado_o_error()
        self.assertIn("otro extremo", str(cm.exception))

    def test_el_emisor_cierra_y_el_que_recibe_se_entera(self):
        cliente, conexion = self.conectados()

        recibiendo = Hilo(conexion.recv)
        recibiendo.start()
        time.sleep(self.timeout * 2)

        cliente.shutdown()

        with self.assertRaises(TransferCancelled) as cm:
            recibiendo.resultado_o_error()
        self.assertIn("otro extremo", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
