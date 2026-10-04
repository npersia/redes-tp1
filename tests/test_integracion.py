"""Integracion de Stop & Wait con las capas de aplicacion (client.py / server.py)."""

import logging
import os
import shutil
import tempfile
import threading
import time
import types
import unittest

from base import HOST, ConnectionClosed, Hilo, SWTestCase, packet, sw

import lib.client as client_app
import lib.server as server_app
from lib.file_transfer.file_transfer import receive_content, send_file
from lib.protocols.factory import TransportFactory


def args(**kwargs):
    base = {"verbosity": -1, "protocol": "sw", "host": HOST, "port": 0, "name": None}
    base.update(kwargs)
    return types.SimpleNamespace(**base)


class TestFactory(SWTestCase):

    def test_la_factory_devuelve_stop_and_wait(self):
        t = TransportFactory.get_transport("sw", HOST, 1234)
        self.assertIsInstance(t, sw.StopAndWait)
        self.assertEqual((t.host, t.port), (HOST, 1234))

    def test_la_factory_normaliza_el_nombre(self):
        for nombre in ("SW", " sw ", "Sw"):
            with self.subTest(nombre=nombre):
                self.assertIsInstance(
                    TransportFactory.get_transport(nombre, HOST, 1), sw.StopAndWait)

    def test_protocolo_desconocido(self):
        with self.assertRaises(ValueError):
            TransportFactory.get_transport("xyz", HOST, 1)


class TestFlujosDeAplicacion(SWTestCase):
    """upload()/download() del cliente contra handle_connection() del servidor."""

    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp(prefix="swtest-")
        logging.disable(logging.CRITICAL)

    def tearDown(self):
        logging.disable(logging.NOTSET)
        shutil.rmtree(self.dir, ignore_errors=True)
        super().tearDown()

    def _arrancar_servidor(self, storage_dir):
        servidor = self.servidor()
        puerto = servidor.sock.getsockname()[1]
        parada = threading.Event()

        def atender():
            conexion = self.aceptar(servidor)
            self._transportes.append(conexion)
            server_app.handle_connection(conexion, storage_dir, parada)
            return conexion

        hilo = Hilo(atender)
        hilo.start()
        return servidor, puerto, hilo

    def _archivo(self, nombre, contenido):
        ruta = os.path.join(self.dir, nombre)
        with open(ruta, "wb") as f:
            f.write(contenido)
        return ruta

    # -- upload ------------------------------------------------------------

    def test_upload_de_un_archivo_binario(self):
        contenido = os.urandom(sw.MAX_PAYLOAD_SIZE * 3 + 55)
        origen = self._archivo("origen.bin", contenido)
        destino = os.path.join(self.dir, "recibido.bin")

        _, puerto, hilo = self._arrancar_servidor(self.dir)
        client_app.upload(args(port=puerto, src=origen, name=os.path.basename(destino)))
        hilo.resultado_o_error(15.0)

        with open(destino, "rb") as f:
            self.assertEqual(f.read(), contenido)

    def test_upload_de_un_archivo_vacio(self):
        origen = self._archivo("vacio.bin", b"")
        destino = os.path.join(self.dir, "recibido-vacio.bin")

        _, puerto, hilo = self._arrancar_servidor(self.dir)
        client_app.upload(args(port=puerto, src=origen, name=os.path.basename(destino)))
        hilo.resultado_o_error(15.0)

        self.assertTrue(os.path.exists(destino))
        self.assertEqual(os.path.getsize(destino), 0)

    def test_upload_de_un_archivo_que_no_existe_no_abre_conexion(self):
        antes = len(self.net.sockets)
        client_app.upload(args(port=1, src=os.path.join(self.dir, "no-esta")))
        self.assertEqual(len(self.net.sockets), antes, "no intento conectarse")

    def test_upload_con_el_servidor_caido(self):
        origen = self._archivo("origen.bin", b"contenido")
        # no levanta excepcion: upload() captura ConnectionClosed y loguea
        client_app.upload(args(port=_puerto_muerto(), src=origen))

    # -- download ----------------------------------------------------------

    def test_download_de_un_archivo_existente(self):
        contenido = os.urandom(sw.MAX_PAYLOAD_SIZE * 2 + 3)
        self._archivo("pedido.bin", contenido)
        storage = self.dir
        destino = os.path.join(self.dir, "bajado.bin")

        _, puerto, hilo = self._arrancar_servidor(storage)
        client_app.download(args(port=puerto, dst=destino, name="pedido.bin"))
        hilo.resultado_o_error(15.0)

        with open(destino, "rb") as f:
            self.assertEqual(f.read(), contenido)

    def test_download_de_un_archivo_inexistente_devuelve_error(self):
        storage = self.dir
        destino = os.path.join(self.dir, "no-deberia-existir.bin")

        _, puerto, hilo = self._arrancar_servidor(storage)
        client_app.download(args(port=puerto, dst=destino, name="fantasma.bin"))
        hilo.resultado_o_error(15.0)

        self.assertFalse(os.path.exists(destino),
                         "no debe escribir el archivo ante un ERROR")

    def test_download_usa_basename_para_evitar_path_traversal(self):
        storage = self.dir
        destino = os.path.join(self.dir, "passwd")
        _, puerto, hilo = self._arrancar_servidor(storage)
        client_app.download(args(port=puerto, dst=destino,
                                 name="../../../../etc/passwd"))
        hilo.resultado_o_error(15.0)
        self.assertFalse(os.path.exists(destino))

    # -- perdida en el flujo completo --------------------------------------

    def test_upload_con_perdida_en_el_handshake_y_en_los_datos(self):
        import netsim
        contenido = os.urandom(sw.MAX_PAYLOAD_SIZE * 2 + 1)
        origen = self._archivo("origen.bin", contenido)
        destino = os.path.join(self.dir, "recibido.bin")

        _, puerto, hilo = self._arrancar_servidor(self.dir)
        # el primer datagrama de cada socket nuevo se pierde (SYN, SYN-ACK, 1er dato)
        self.net.on_create = lambda s: setattr(s, "policy", netsim.drop_nth(1))

        client_app.upload(args(port=puerto, src=origen, name=os.path.basename(destino)))
        hilo.resultado_o_error(15.0)

        with open(destino, "rb") as f:
            self.assertEqual(f.read(), contenido)


class TestDispatcher(SWTestCase):
    """El bucle de aceptacion de src/lib/server.py."""

    def setUp(self):
        super().setUp()
        logging.disable(logging.CRITICAL)

    def tearDown(self):
        logging.disable(logging.NOTSET)
        super().tearDown()

    def test_el_shutdown_event_detiene_el_dispatcher(self):
        """Apretar Enter tiene que frenar el servidor.

        Dispatcher.start() vuelve a mirar el shutdown_event cada vez que
        accept() devuelve None por timeout. Antes el socket de escucha era
        bloqueante y accept() no volvia nunca, asi que
        `server_thread.join()` de main() no retornaba nunca.
        """
        transporte = self.servidor()
        parada = threading.Event()
        dispatcher = server_app.Dispatcher()

        hilo = Hilo(dispatcher.start, parada, transporte, "/tmp/no-usado")
        hilo.start()
        time.sleep(0.1)

        parada.set()
        self.assertTrue(
            hilo.esperar(2.0),
            "Dispatcher.start() tiene que retornar tras el shutdown_event",
        )
        self.assertTrue(dispatcher.stopping.is_set(), "paso por el finally")
        self.assertTrue(transporte.is_closed, "cerro el transporte de escucha")

    def test_el_dispatcher_atiende_varias_conexiones(self):
        dir_tmp = tempfile.mkdtemp(prefix="swdisp-")
        self.addCleanup(shutil.rmtree, dir_tmp, True)
        transporte = self.servidor()
        puerto = transporte.sock.getsockname()[1]
        parada = threading.Event()
        dispatcher = server_app.Dispatcher()

        hilo = Hilo(dispatcher.start, parada, transporte, dir_tmp)
        hilo.start()
        time.sleep(0.05)

        for i in range(3):
            cliente = self.cliente(puerto)
            cliente.connect()
            cuerpo = f"cliente-{i}".encode()
            cliente.send(f"UPLOAD {len(cuerpo)} salida.bin".encode())
            respuesta = Hilo(cliente.recv)
            respuesta.start()
            self.assertEqual(respuesta.resultado_o_error(5.0), b"OK")
            cliente.send(cuerpo)
            cliente.close()

        time.sleep(0.3)
        with open(os.path.join(dir_tmp, "salida.bin"), "rb") as f:
            self.assertTrue(f.read().startswith(b"cliente-"))
        transporte.is_closed = True


def _puerto_muerto():
    import socket as s
    x = s.socket(s.AF_INET, s.SOCK_DGRAM)
    x.bind((HOST, 0))
    p = x.getsockname()[1]
    x.close()
    return p


if __name__ == "__main__":
    unittest.main()
