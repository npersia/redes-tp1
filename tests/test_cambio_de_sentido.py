"""Cambio de sentido en una misma conexion: el que recibia pasa a mandar.

Es lo que hacen download (pedido -> archivo) y upload (pedido -> OK ->
archivo).
El caso delicado es perder el ACK del ultimo segmento de un mensaje justo
antes del cambio: el receptor ya retorno de recv() y paso a send(), y el
emisor sigue en send() reintentando ese ultimo segmento.

Antes, en Stop & Wait, cada extremo ignoraba los datos del otro (no traen flag
ACK) sin consumir reintentos: livelock a maxima velocidad, sin timeout. Ahora,
el que esta en send() y recibe un segmento viejo (seq < esperado) lo vuelve a
confirmar, asi el otro destraba su send() y pasa a recv().

En Selective ACK los datos llevan el flag ACK con el ack acumulativo, asi que
el primer segmento del otro ya confirma: se corre igual para fijar que anda.
"""

import logging
import os
import shutil
import tempfile
import threading
import time
import types
import unittest

from base import HOST, Hilo, SACKTestCase, SWTestCase, sack, sw
import netsim

import lib.client as client_app
import lib.server as server_app

# Tope de datagramas por extremo: con livelock son miles en un segundo.
TOPE_TX = 60


class _CambioDeSentido:

    drop_acks = staticmethod(netsim.drop_acks_nth)
    max_payload = sw.MAX_PAYLOAD_SIZE

    def _conversacion(
        self, mensajes, policy_cliente=None, policy_servidor=None, timeout=5.0
    ):
        """Alterna send()/recv() entre cliente y servidor, empezando el
        cliente.

        Devuelve lo que recibio cada lado, en orden.
        """
        cliente, conexion = self.conectados(policy_cliente, policy_servidor)

        def lado(transporte, propios):
            recibidos = []
            for i, mensaje in enumerate(mensajes):
                if (i % 2 == 0) == propios:
                    transporte.send(mensaje)
                else:
                    recibidos.append(transporte.recv())
            return recibidos

        hilo_cli = Hilo(lado, cliente, True)
        hilo_srv = Hilo(lado, conexion, False)
        hilo_cli.start()
        hilo_srv.start()
        try:
            recibido_cli = hilo_cli.resultado_o_error(timeout)
            recibido_srv = hilo_srv.resultado_o_error(timeout)
        except AssertionError:
            cliente.shutdown()
            conexion.shutdown()
            raise AssertionError(
                (
                    f"la conversacion no termino: cliente tx="
                    f"{cliente.sock and cliente.sock.tx}"
                    f"{self.volcado()}"
                )
            )

        self.assertLess(
            cliente.sock.tx, TOPE_TX,
            "el cliente inundo la red" + self.volcado()
        )
        self.assertLess(
            conexion.sock.tx, TOPE_TX,
            "el servidor inundo la red" + self.volcado()
        )
        return recibido_cli, recibido_srv

    # -- sin perdidas (caracterizacion) -------------------------------------

    def test_pedido_y_respuesta_sin_perdidas(self):
        cli, srv = self._conversacion([b"DOWNLOAD a.txt", b"contenido" * 50])
        self.assertEqual(srv, [b"DOWNLOAD a.txt"])
        self.assertEqual(cli, [b"contenido" * 50])

    def test_upload_en_tres_mensajes_sin_perdidas(self):
        cuerpo = os.urandom(self.max_payload * 3 + 7)
        cli, srv = self._conversacion([b"UPLOAD 10 a", b"OK", cuerpo])
        self.assertEqual(srv, [b"UPLOAD 10 a", cuerpo])
        self.assertEqual(cli, [b"OK"])

    # -- se pierde el ACK del ultimo segmento antes del cambio --------------

    def test_se_pierde_el_ack_del_pedido(self):
        """El viejo HALLAZGO del download: antes era un livelock."""
        cli, srv = self._conversacion(
            [b"DOWNLOAD a.txt", b"contenido" * 50],
            policy_servidor=self.drop_acks(1)
        )
        self.assertEqual(srv, [b"DOWNLOAD a.txt"])
        self.assertEqual(cli, [b"contenido" * 50])

    def test_se_pierde_el_ack_del_pedido_dos_veces(self):
        """Tambien se pierde la re-confirmacion: hay que volver a confirmar."""
        cli, srv = self._conversacion(
            [b"DOWNLOAD a.txt", b"contenido" * 50],
            policy_servidor=self.drop_acks(1, 2)
        )
        self.assertEqual(cli, [b"contenido" * 50])

    def test_se_pierde_el_ack_del_ultimo_chunk_de_un_pedido_largo(self):
        pedido = os.urandom(self.max_payload * 2 + 10)  # 3 chunks
        cli, srv = self._conversacion(
            [pedido, b"respuesta"], policy_servidor=self.drop_acks(3)
        )
        self.assertEqual(srv, [pedido])
        self.assertEqual(cli, [b"respuesta"])

    def test_upload_se_pierde_el_ack_del_ok(self):
        cuerpo = os.urandom(self.max_payload * 2 + 1)
        cli, srv = self._conversacion(
            [b"UPLOAD 10 a", b"OK", cuerpo], policy_cliente=self.drop_acks(1)
        )
        self.assertEqual(cli, [b"OK"])
        self.assertEqual(srv, [b"UPLOAD 10 a", cuerpo])

    def test_upload_se_pierden_los_dos_acks_del_cambio(self):
        cuerpo = os.urandom(self.max_payload * 2 + 1)
        cli, srv = self._conversacion(
            [b"UPLOAD 10 a", b"OK", cuerpo],
            policy_cliente=self.drop_acks(1),
            policy_servidor=self.drop_acks(1),
        )
        self.assertEqual(cli, [b"OK"])
        self.assertEqual(srv, [b"UPLOAD 10 a", cuerpo])

    def test_mensajes_vacios_con_el_ack_perdido(self):
        """Un FIN sin payload avanza 1: tambien se re-confirma."""
        cli, srv = self._conversacion(
            [b"", b"", b"fin"],
            policy_cliente=self.drop_acks(1),
            policy_servidor=self.drop_acks(1),
        )
        self.assertEqual(cli, [b""])
        self.assertEqual(srv, [b"", b"fin"])


class TestCambioDeSentidoStopAndWait(_CambioDeSentido, SWTestCase):
    pass


class TestCambioDeSentidoSelectiveAck(_CambioDeSentido, SACKTestCase):
    drop_acks = staticmethod(netsim.drop_acks_sack_nth)
    max_payload = sack.MAX_PAYLOAD_SIZE


class TestUploadDePuntaAPunta(SWTestCase):
    """client.upload() contra handle_connection() perdiendo los ACKs del
    cambio."""

    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp(prefix="sentido-")
        logging.disable(logging.CRITICAL)

    def tearDown(self):
        logging.disable(logging.NOTSET)
        shutil.rmtree(self.dir, ignore_errors=True)
        super().tearDown()

    def test_upload_completo_perdiendo_el_ack_del_pedido_y_el_del_ok(self):
        contenido = os.urandom(sw.MAX_PAYLOAD_SIZE * 3 + 5)
        origen = os.path.join(self.dir, "origen.bin")
        with open(origen, "wb") as f:
            f.write(contenido)
        storage = os.path.join(self.dir, "storage")
        os.makedirs(storage)

        servidor = self.servidor()
        puerto = servidor.sock.getsockname()[1]

        def atender():
            conexion = self.aceptar(servidor)
            self._transportes.append(conexion)
            server_app.handle_connection(conexion, storage, threading.Event())

        atendiendo = Hilo(atender)
        atendiendo.start()

        # Cada socket nuevo (cliente y efimero del servidor) pierde su 2do ACK
        # puro: el 1ro es el del handshake (ACK final / SYN-ACK), el 2do es el
        # que confirma el pedido (servidor) o el OK (cliente).
        self.net.on_create = lambda s: setattr(
            s,
            "policy",
            netsim.drop_acks_nth(2)
        )

        t0 = time.monotonic()
        client_app.upload(
            types.SimpleNamespace(
                verbosity=-1,
                protocol="sw",
                host=HOST,
                port=puerto,
                src=origen,
                name="subido.bin",
            )
        )
        atendiendo.resultado_o_error(10.0)

        with open(os.path.join(storage, "subido.bin"), "rb") as f:
            self.assertEqual(f.read(), contenido)
        self.assertLess(time.monotonic() - t0, 5.0)


if __name__ == "__main__":
    unittest.main()
