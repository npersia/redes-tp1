"""Cancelacion en Selective ACK: lo mismo que test_cancel.py exige a Stop & Wait.

- cancel() (el Enter del usuario) corta send()/recv() con TransferCancelled y
  le avisa al otro extremo con ERR+CANCEL.
- Recibir ERR+CANCEL da TransferCancelled ("el otro extremo..."); un ERR solo
  sigue siendo ConnectionClosed.
- shutdown() de un lado (el servidor que se apaga) llega al otro como
  cancelacion, no como timeout.
- Un close() concurrente nunca termina en AttributeError.
"""

import os
import shutil
import tempfile
import threading
import time
import unittest

from base import (ConnectionClosed, Hilo, SACKTestCase, TransferCancelled, bt,
                  netsim, packet, sack)

import lib.server as server_app

PROTO = sack.SelectiveAck.PROTOCOL_ID
MAX = sack.MAX_PAYLOAD_SIZE
# Un cancel() se tiene que notar en la proxima vuelta del bucle (POLL_INTERVAL),
# no al vencer un timeout. Holgura para el scheduler.
REACCION = sack.POLL_INTERVAL + 0.5


class ConPeer(SACKTestCase):

    # Timeout largo a proposito: si algo tarda lo que un timeout, es que la
    # cancelacion no se noto en el momento.
    timeout = 5.0

    def mandar(self, peer, t, **kwargs):
        kwargs.setdefault("protocol", PROTO)
        peer.send_pkt(t.sock.getsockname(), **kwargs)

    def avisos(self, peer):
        return [p for p in peer.drain() if p["ERR"]]


class TestCancelacionLocal(ConPeer):
    """Lo que hace este extremo cuando el usuario cancela."""

    def test_notify_abort_manda_err_con_cancel(self):
        t, peer = self.transporte_a_mano(seq=1000, exp=2000)
        t.notify_abort()
        vistos = peer.drain()
        self.assertEqual(len(vistos), bt.ABORT_NOTICES)
        for aviso in vistos:
            self.assertPaquete(aviso, ERR=1, CANCEL=1, SYN=0, FIN=0, protocol=PROTO,
                               seq=1000, ack=2000, payload=b"")

    def test_cancelar_durante_send_con_la_ventana_llena(self):
        t, peer = self.transporte_a_mano(seq=1000)
        enviando = Hilo(t.send, b"z" * MAX * 10)
        enviando.start()
        for _ in range(sack.CWND):          # sale la ventana entera; nadie la ACKea
            peer.recv()

        inicio = time.monotonic()
        t.cancel()
        with self.assertRaises(TransferCancelled) as cm:
            enviando.resultado_o_error()
        self.assertLess(time.monotonic() - inicio, REACCION, "no espero el timeout")
        self.assertIn("cancelada por el usuario", str(cm.exception))

        avisos = self.avisos(peer)
        self.assertEqual(len(avisos), bt.ABORT_NOTICES)
        for aviso in avisos:
            self.assertPaquete(aviso, ERR=1, CANCEL=1, protocol=PROTO)

    def test_cancelar_antes_de_send_no_manda_datos(self):
        t, peer = self.transporte_a_mano(seq=1000)
        t.cancel()
        with self.assertRaises(TransferCancelled):
            t.send(b"z" * MAX * 3)
        vistos = peer.drain()
        self.assertTrue(vistos, "tiene que avisar")
        self.assertTrue(all(p["ERR"] and p["CANCEL"] for p in vistos),
                        "solo el aviso, ningun segmento de datos")

    def test_cancelar_durante_recv(self):
        t, peer = self.transporte_a_mano(exp=2000)
        recibiendo = Hilo(t.recv)
        recibiendo.start()
        # llega algo, para que la cancelacion sea a mitad de camino
        self.mandar(peer, t, flags=packet.ACK_MASK, sequence_number=2000, payload=b"abc")
        peer.recv()

        inicio = time.monotonic()
        t.cancel()
        with self.assertRaises(TransferCancelled) as cm:
            recibiendo.resultado_o_error()
        self.assertLess(time.monotonic() - inicio, REACCION)
        self.assertIn("cancelada por el usuario", str(cm.exception))
        self.assertIn("3 bytes", str(cm.exception))

        avisos = self.avisos(peer)
        self.assertEqual(len(avisos), bt.ABORT_NOTICES)
        for aviso in avisos:
            self.assertPaquete(aviso, ERR=1, CANCEL=1, protocol=PROTO, ack=2003)

    def test_cancelar_antes_de_recv(self):
        t, peer = self.transporte_a_mano(exp=2000)
        t.cancel()
        with self.assertRaises(TransferCancelled):
            t.recv()
        self.assertTrue(self.avisos(peer))


class TestCancelacionRemota(ConPeer):
    """Lo que hace este extremo cuando el otro le avisa."""

    def test_recv_con_err_cancel_es_transfercancelled(self):
        t, peer = self.transporte_a_mano(exp=2000)
        recibiendo = Hilo(t.recv)
        recibiendo.start()
        time.sleep(0.02)
        self.mandar(peer, t, flags=packet.ERR_MASK | packet.CANCEL_MASK, sequence_number=2000)
        with self.assertRaises(TransferCancelled) as cm:
            recibiendo.resultado_o_error()
        self.assertIn("otro extremo", str(cm.exception))

    def test_recv_con_err_pelado_sigue_siendo_error(self):
        t, peer = self.transporte_a_mano(exp=2000)
        recibiendo = Hilo(t.recv)
        recibiendo.start()
        time.sleep(0.02)
        self.mandar(peer, t, flags=packet.ERR_MASK, sequence_number=2000)
        with self.assertRaises(ConnectionClosed) as cm:
            recibiendo.resultado_o_error()
        self.assertNotIsInstance(cm.exception, TransferCancelled)

    def test_send_con_err_cancel_es_transfercancelled(self):
        t, peer = self.transporte_a_mano(seq=1000)
        enviando = Hilo(t.send, b"z" * 10)
        enviando.start()
        peer.recv()
        self.mandar(peer, t, flags=packet.ERR_MASK | packet.CANCEL_MASK, sequence_number=1)
        with self.assertRaises(TransferCancelled) as cm:
            enviando.resultado_o_error()
        self.assertIn("otro extremo", str(cm.exception))

    def test_send_con_err_pelado_sigue_siendo_error(self):
        t, peer = self.transporte_a_mano(seq=1000)
        enviando = Hilo(t.send, b"z" * 10)
        enviando.start()
        peer.recv()
        self.mandar(peer, t, flags=packet.ERR_MASK, sequence_number=1)
        with self.assertRaises(ConnectionClosed) as cm:
            enviando.resultado_o_error()
        self.assertNotIsInstance(cm.exception, TransferCancelled)

    def test_quien_recibe_el_aviso_no_contesta(self):
        """El que se entera de la cancelacion no le devuelve otro aviso al que cancelo."""
        t, peer = self.transporte_a_mano(exp=2000)
        recibiendo = Hilo(t.recv)
        recibiendo.start()
        time.sleep(0.02)
        self.mandar(peer, t, flags=packet.ERR_MASK | packet.CANCEL_MASK, sequence_number=2000)
        with self.assertRaises(TransferCancelled):
            recibiendo.resultado_o_error()
        self.assertEqual(peer.drain(0.1), [])


class TestCancelacionEntreExtremosReales(SACKTestCase):
    """Handshake de verdad y cancelacion en cada direccion.

    `retries` alto a proposito: el que espera no se tiene que rendir por
    timeout ni por silencio antes de que le llegue el aviso.
    """

    retries = 100

    def test_el_que_recibe_cancela_y_el_que_manda_se_entera(self):
        for direccion, emisor, receptor, _, _ in self.direcciones():
            with self.subTest(direccion=direccion):
                enviando = Hilo(emisor.send, b"z" * MAX * 50)
                enviando.start()
                receptor.cancel()
                recibiendo = Hilo(receptor.recv)
                recibiendo.start()

                with self.assertRaises(TransferCancelled):
                    recibiendo.resultado_o_error()
                with self.assertRaises(TransferCancelled) as cm:
                    enviando.resultado_o_error()
                self.assertIn("otro extremo", str(cm.exception))

    def test_el_que_manda_cancela_y_el_que_recibe_se_entera(self):
        for direccion, emisor, receptor, _, _ in self.direcciones():
            with self.subTest(direccion=direccion):
                recibiendo = Hilo(receptor.recv)
                recibiendo.start()
                emisor.cancel()
                enviando = Hilo(emisor.send, b"z" * MAX * 50)
                enviando.start()

                with self.assertRaises(TransferCancelled):
                    enviando.resultado_o_error()
                with self.assertRaises(TransferCancelled) as cm:
                    recibiendo.resultado_o_error()
                self.assertIn("otro extremo", str(cm.exception))

    def test_cancelar_a_mitad_de_una_transferencia_larga(self):
        """El emisor ya mando parte cuando el receptor cancela."""
        cliente, conexion = self.conectados()
        recibiendo = Hilo(conexion.recv)
        recibiendo.start()
        # el ultimo segmento nunca llega: la transferencia no puede terminar sola
        cliente.sock.policy = netsim.drop_seq(1 + 99 * MAX, 10 ** 6)
        enviando = Hilo(cliente.send, os.urandom(MAX * 100))
        enviando.start()

        time.sleep(0.1)
        conexion.cancel()
        with self.assertRaises(TransferCancelled):
            recibiendo.resultado_o_error()
        with self.assertRaises(TransferCancelled) as cm:
            enviando.resultado_o_error()
        self.assertIn("otro extremo", str(cm.exception))


class TestCierreForzadoEntreExtremos(SACKTestCase):
    """shutdown() de un lado: el otro se entera por el aviso, no por timeout."""

    retries = 100

    def test_el_receptor_cierra_y_el_que_manda_se_entera(self):
        """Es el apagado del servidor: Dispatcher._stop() -> connection.shutdown()."""
        cliente, conexion = self.conectados()
        cliente.sock.policy = netsim.drop_seq(1 + 9 * MAX, 10 ** 6)   # no termina sola
        enviando = Hilo(cliente.send, b"z" * MAX * 10)
        enviando.start()
        time.sleep(0.05)

        conexion.shutdown()
        with self.assertRaises(TransferCancelled) as cm:
            enviando.resultado_o_error()
        self.assertIn("otro extremo", str(cm.exception))

    def test_el_emisor_cierra_y_el_que_recibe_se_entera(self):
        cliente, conexion = self.conectados()
        recibiendo = Hilo(conexion.recv)
        recibiendo.start()
        time.sleep(0.05)

        cliente.shutdown()
        with self.assertRaises(TransferCancelled) as cm:
            recibiendo.resultado_o_error()
        self.assertIn("otro extremo", str(cm.exception))


class TestCierreConcurrente(ConPeer):
    """close() desde otro hilo justo entre dos usos del socket.

    Se fuerza la carrera cerrando desde la red simulada. Antes terminaba en
    AttributeError (sock=None); tiene que ser ConnectionClosed.
    """

    timeout = SACKTestCase.timeout

    def test_close_durante_send(self):
        t, _ = self.transporte_a_mano(seq=1000)
        mandados = {"n": 0}

        def cerrar_al_segundo(ctx):
            mandados["n"] += 1
            if mandados["n"] == 2:
                t.close()
            return netsim.PASS
        t.sock.policy = cerrar_al_segundo
        with self.assertRaises(ConnectionClosed):
            t.send(b"x" * 5000)

    def test_close_durante_recv(self):
        t, peer = self.transporte_a_mano(exp=2000)
        t.sock.policy = lambda ctx: (t.close(), netsim.PASS)[1]
        destino = t.sock.getsockname()
        recibiendo = Hilo(t.recv)
        recibiendo.start()
        time.sleep(0.02)
        peer.send_pkt(destino, protocol=PROTO, flags=packet.ACK_MASK, sequence_number=2000, payload=b"hola")
        with self.assertRaises(ConnectionClosed):
            recibiendo.resultado_o_error()


class SocketQueFallaAlEnviar:
    """Socket que entrega lo que le den para recibir y falla en cada sendto().

    Si `cerrar` esta, lo llama antes de fallar: asi se simula un close() de otro
    hilo justo antes del envio, con un socket real (netsim se traga esos errores).
    """

    def __init__(self, recibir=(), cerrar=None):
        self.recibir = list(recibir)
        self.cerrar = cerrar

    def sendto(self, data, addr):
        if self.cerrar:
            self.cerrar()
        raise OSError(9, "Bad file descriptor")

    def recvfrom(self, bufsize):
        if self.recibir:
            return self.recibir.pop(0)
        raise sack.socket.timeout()

    def close(self):
        pass


class TestEnvioSobreSocketCerrado(ConPeer):
    """sendto() falla: si la conexion se cerro es ConnectionClosed; si no, el OSError sigue."""

    def marcar_cerrado(self, t):
        return lambda: setattr(t, "is_closed", True)

    def test_send_con_la_conexion_cerrada_en_el_medio(self):
        t, _ = self.transporte_a_mano(seq=1000)
        t.sock = SocketQueFallaAlEnviar(cerrar=self.marcar_cerrado(t))
        with self.assertRaises(ConnectionClosed) as cm:
            t.send(b"x" * 10)
        self.assertNotIsInstance(cm.exception, TransferCancelled)

    def test_send_con_un_error_de_socket_genuino(self):
        t, _ = self.transporte_a_mano(seq=1000)
        t.sock = SocketQueFallaAlEnviar()
        with self.assertRaises(OSError) as cm:
            t.send(b"x" * 10)
        self.assertNotIsInstance(cm.exception, ConnectionClosed)

    def test_recv_con_la_conexion_cerrada_al_contestar(self):
        t, peer = self.transporte_a_mano(exp=2000)
        dato = packet.make_packet(version=1, protocol=PROTO, flags=packet.ACK_MASK,
                                  sequence_number=2000, payload=b"hola")
        t.sock = SocketQueFallaAlEnviar(recibir=[(dato, peer.addr)], cerrar=self.marcar_cerrado(t))
        with self.assertRaises(ConnectionClosed):
            t.recv()

    def test_recv_con_un_error_de_socket_genuino(self):
        t, peer = self.transporte_a_mano(exp=2000)
        dato = packet.make_packet(version=1, protocol=PROTO, flags=packet.ACK_MASK,
                                  sequence_number=2000, payload=b"hola")
        t.sock = SocketQueFallaAlEnviar(recibir=[(dato, peer.addr)])
        with self.assertRaises(OSError) as cm:
            t.recv()
        self.assertNotIsInstance(cm.exception, ConnectionClosed)


class TestCancelacionEnLaAplicacion(SACKTestCase):
    """De punta a punta con el servidor: un upload cancelado no deja archivo."""

    retries = 100

    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp(prefix="sackcancel-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        super().tearDown()

    def test_upload_cancelado_por_el_cliente(self):
        cliente, conexion = self.conectados()
        atendiendo = Hilo(server_app.handle_connection, conexion, self.dir, threading.Event())
        atendiendo.start()

        cliente.sock.policy = netsim.drop_seq(1 + 99 * MAX, 10 ** 6)   # no termina sola
        datos = b"UPLOAD subido.bin\n" + os.urandom(MAX * 100)
        enviando = Hilo(cliente.send, datos)
        enviando.start()
        time.sleep(0.1)
        cliente.cancel()

        with self.assertRaises(TransferCancelled):
            enviando.resultado_o_error()
        with self.assertRaises(TransferCancelled):
            atendiendo.resultado_o_error()
        self.assertEqual(os.listdir(self.dir), [], "no se guarda nada")


if __name__ == "__main__":
    unittest.main()
