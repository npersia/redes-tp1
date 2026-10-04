"""Limite de tamanio del upload: el cliente declara el tamanio y el
servidor decide.

El header RDT no cambia: todo viaja en el payload, como la accion y el nombre.

    cliente                         servidor
    UPLOAD <tamanio> <nombre>  ->
                               <-   OK                (tamanio <=
                                    MAX_FILE_SIZE)
    <bytes del archivo>        ->

  o bien, en otra ejecucion:

    UPLOAD <tamanio> <nombre>  ->
                               <-   ERROR <motivo>    (el cliente termina)

El armado y el parseo de los mensajes viven en file_transfer.py: client.py y
server.py trabajan con UploadRequest y con las excepciones UploadRejected /
InvalidMessage.

Los tests que necesitan un archivo "demasiado grande" bajan MAX_FILE_SIZE con
mock.patch en vez de armar 2 GB; los que prueban el valor real de la constante
juegan del lado del cliente a mano y solo declaran el tamanio.
"""

import logging
import os
import shutil
import tempfile
import threading
import types
import unittest
from unittest import mock

from base import HOST, Hilo, SACKTestCase, SWTestCase

import lib.client as client_app
import lib.file_transfer.file_transfer as ft
import lib.server as server_app
from lib.logger.logger import logger

UN_GB = 1 * 1024**3


def args(**kwargs):
    base = {
        "verbosity": -1, "protocol": "sw", "host": HOST, "port": 0,
        "name": None
    }
    base.update(kwargs)
    return types.SimpleNamespace(**base)


class TransporteFalso:
    """Guarda lo que se manda y devuelve respuestas encoladas."""

    def __init__(self, *respuestas):
        self.enviados = []
        self.respuestas = list(respuestas)

    def send(self, data):
        self.enviados.append(data)

    def recv(self):
        return self.respuestas.pop(0)


# ---------------------------------------------------------------------------
# Mensajes (file_transfer.py), sin red
# ---------------------------------------------------------------------------


class TestMensajeDeUpload(unittest.TestCase):

    def test_request_upload_manda_tamanio_y_nombre_en_un_mensaje(self):
        t = TransporteFalso(b"OK")
        ft.request_upload(t, "a.bin", 1234)
        self.assertEqual(t.enviados, [b"UPLOAD 1234 a.bin"])

    def test_request_upload_con_ok_retorna(self):
        self.assertIsNone(
            ft.request_upload(
                TransporteFalso(b"OK"),
                "a.bin",
                1
            )
        )

    def test_request_upload_con_error_levanta_upload_rejected_con_el_motivo(
            self
    ):
        t = TransporteFalso(b"ERROR El archivo supera el limite")
        with self.assertRaises(ft.UploadRejected) as cm:
            ft.request_upload(t, "a.bin", 1)
        self.assertEqual(str(cm.exception), "El archivo supera el limite")

    def test_request_upload_con_respuesta_desconocida_tambien_rechaza(self):
        with self.assertRaises(ft.UploadRejected):
            ft.request_upload(TransporteFalso(b"cualquier cosa"), "a.bin", 1)

    def test_send_ok(self):
        t = TransporteFalso()
        ft.send_ok(t)
        self.assertEqual(t.enviados, [b"OK"])

    def test_is_upload_request(self):
        self.assertTrue(ft.is_upload_request(b"UPLOAD 1 a"))
        self.assertFalse(ft.is_upload_request(b"DOWNLOAD a"))
        self.assertFalse(ft.is_upload_request(b"UPLOADX"))

    def test_parse_upload_request(self):
        self.assertEqual(
            ft.parse_upload_request(b"UPLOAD 5 a.txt"),
            ft.UploadRequest(filename="a.txt", size=5),
        )

    def test_parse_acepta_nombres_con_espacios(self):
        self.assertEqual(
            ft.parse_upload_request(b"UPLOAD 3 mi archivo.txt").filename,
            "mi archivo.txt",
        )

    def test_parse_acepta_tamanios_grandes(self):
        self.assertEqual(
            ft.parse_upload_request(
                f"UPLOAD {UN_GB + 1} a".encode()
            ).size,
            UN_GB + 1
        )

    def test_parse_rechaza_mensajes_mal_formados(self):
        for mensaje in (
            b"UPLOAD a.txt",  # sin tamanio
            b"UPLOAD diez a.txt",  # tamanio no numerico
            b"UPLOAD -5 a.txt",  # tamanio negativo
            "UPLOAD ²3 a.txt".encode(),  # digito unicode
            b"UPLOAD 5",  # sin nombre
            b"UPLOAD 5 ",  # nombre vacio
            b"UPLOAD 5 \xff\xfe",
        ):  # utf-8 invalido
            with self.subTest(mensaje=mensaje):
                with self.assertRaises(ft.InvalidMessage):
                    ft.parse_upload_request(mensaje)


class TestConstante(unittest.TestCase):

    def test_el_limite_del_servidor_es_2_gb(self):
        self.assertEqual(server_app.MAX_FILE_SIZE, UN_GB)


# ---------------------------------------------------------------------------
# Con red: Stop & Wait y Selective ACK
# ---------------------------------------------------------------------------


class _Captura(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.mensajes = []

    def emit(self, record):
        self.mensajes.append((record.levelno, record.getMessage()))


class _LimiteBase:
    protocolo = "sw"

    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp(prefix="limite-")
        self.storage = os.path.join(self.dir, "storage")
        os.makedirs(self.storage)
        self.captura = _Captura()
        logger.addHandler(self.captura)
        self._propagate = logger.propagate
        logger.propagate = False  # sin ruido en la salida de la suite

    def tearDown(self):
        logger.removeHandler(self.captura)
        logger.propagate = self._propagate
        shutil.rmtree(self.dir, ignore_errors=True)
        super().tearDown()

    # -- helpers ------------------------------------------------------------

    def _archivo(self, contenido):
        ruta = os.path.join(self.dir, "origen.bin")
        with open(ruta, "wb") as f:
            f.write(contenido)
        return ruta

    def _servidor_real(self):
        """Un servidor que atiende una conexion con handle_connection()."""
        servidor = self.servidor()
        puerto = servidor.sock.getsockname()[1]

        def atender():
            conexion = self.aceptar(servidor)
            self._transportes.append(conexion)
            server_app.handle_connection(
                conexion,
                self.storage,
                threading.Event()
            )

        hilo = Hilo(atender)
        hilo.start()
        return puerto, hilo

    def _upload(self, puerto, origen, nombre="subido.bin"):
        hilo = Hilo(
            client_app.upload,
            args(
                protocol=self.protocolo,
                port=puerto,
                src=origen,
                name=nombre
            ),
        )
        hilo.start()
        return hilo

    def _upload_completo(self, contenido, limite=None):
        puerto, servidor = self._servidor_real()
        origen = self._archivo(contenido)
        if limite is None:
            self._upload(puerto, origen).resultado_o_error(15.0)
            servidor.resultado_o_error(15.0)
            return
        with mock.patch.object(server_app, "MAX_FILE_SIZE", limite):
            self._upload(puerto, origen).resultado_o_error(15.0)
            servidor.resultado_o_error(15.0)

    def _atendido_a_mano(self):
        """(
            cliente conectado a mano,
            hilo con handle_connection del servidor
        )."""
        cliente, conexion = self.conectados()
        hilo = Hilo(
            server_app.handle_connection,
            conexion,
            self.storage,
            threading.Event()
        )
        hilo.start()
        return cliente, hilo

    @staticmethod
    def _recv(transporte, timeout=5.0):
        recibiendo = Hilo(transporte.recv)
        recibiendo.start()
        return recibiendo.resultado_o_error(timeout)

    def _errores(self):
        return [
            m for nivel, m in self.captura.mensajes
            if nivel >= logging.ERROR
        ]

    def _guardado(self, nombre="subido.bin"):
        with open(os.path.join(self.storage, nombre), "rb") as f:
            return f.read()

    # -- de punta a punta: client.upload() contra handle_connection() -------

    def test_upload_por_debajo_del_limite_se_guarda(self):
        contenido = os.urandom(3000)
        self._upload_completo(contenido, limite=5000)
        self.assertEqual(self._guardado(), contenido)
        self.assertEqual(self._errores(), [])

    def test_upload_exactamente_en_el_limite_se_guarda(self):
        contenido = os.urandom(4000)
        self._upload_completo(contenido, limite=4000)
        self.assertEqual(self._guardado(), contenido)

    def test_upload_un_byte_por_encima_se_rechaza(self):
        self._upload_completo(os.urandom(4001), limite=4000)
        self.assertEqual(
            os.listdir(self.storage),
            [],
            "el servidor no guarda nada"
        )

    def test_un_upload_rechazado_no_manda_el_archivo(self):
        contenido = os.urandom(6000)
        self._upload_completo(contenido, limite=100)
        self.assertFalse(
            any(contenido[:64] in p["payload"] for p in self.net.sent() if p),
            "el cliente no debe empezar a mandar el archivo" + self.volcado(),
        )

    def test_el_cliente_informa_el_rechazo_y_termina(self):
        # _upload_completo espera al cliente con tope: si se colgara, falla
        self._upload_completo(os.urandom(500), limite=100)

        errores = self._errores()
        self.assertTrue(
            any(m.startswith("[Cliente]") for m in errores),
            f"el cliente loguea el motivo del servidor: {errores}",
        )
        self.assertFalse(
            any("cerró la conexión" in m for m in errores),
            f"es un rechazo, no un corte de conexion: {errores}",
        )
        self.assertFalse(
            any(
                "Transferencia completada" in m for _,
                m in self.captura.mensajes
            )
        )

    def test_upload_de_archivo_vacio_sigue_funcionando(self):
        self._upload_completo(b"")
        self.assertEqual(self._guardado(), b"")

    # -- lado cliente: que manda --------------------------------------------

    def test_el_cliente_manda_el_archivo_solo_despues_del_ok(self):
        contenido = os.urandom(1234)
        servidor = self.servidor()
        puerto = servidor.sock.getsockname()[1]
        aceptando = Hilo(self.aceptar, servidor)
        aceptando.start()

        subiendo = self._upload(puerto, self._archivo(contenido))
        conexion = aceptando.resultado_o_error(5.0)
        self._transportes.append(conexion)

        self.assertEqual(self._recv(conexion), b"UPLOAD 1234 subido.bin")
        conexion.send(b"OK")
        self.assertEqual(self._recv(conexion), contenido)
        subiendo.resultado_o_error(15.0)

    def test_el_cliente_ante_un_error_loguea_el_motivo(self):
        servidor = self.servidor()
        puerto = servidor.sock.getsockname()[1]
        aceptando = Hilo(self.aceptar, servidor)
        aceptando.start()

        subiendo = self._upload(puerto, self._archivo(os.urandom(300)))
        conexion = aceptando.resultado_o_error(5.0)
        self._transportes.append(conexion)

        self._recv(conexion)
        conexion.send(b"ERROR El archivo supera el limite")
        subiendo.resultado_o_error(15.0)

        self.assertIn("[Cliente] El archivo supera el limite", self._errores())

    # -- lado servidor: cliente a mano contra handle_connection() -----------

    def test_el_servidor_acepta_con_ok_y_guarda(self):
        cliente, servidor = self._atendido_a_mano()

        cliente.send(b"UPLOAD 5 a.txt")
        self.assertEqual(self._recv(cliente), b"OK")
        cliente.send(b"hola!")
        servidor.resultado_o_error(5.0)

        self.assertEqual(self._guardado("a.txt"), b"hola!")

    def test_el_servidor_rechaza_mas_de_2_gb_con_la_constante_real(self):
        cliente, servidor = self._atendido_a_mano()

        cliente.send(f"UPLOAD {UN_GB + 1} enorme.bin".encode())
        respuesta = self._recv(cliente)
        servidor.resultado_o_error(5.0)

        self.assertTrue(respuesta.startswith(b"ERROR "), respuesta)
        self.assertEqual(os.listdir(self.storage), [])

    def test_el_servidor_acepta_exactamente_2_gb(self):
        cliente, _ = self._atendido_a_mano()
        cliente.send(f"UPLOAD {UN_GB} justo.bin".encode())
        # no mandamos 2 GB: alcanza con ver que lo acepta
        self.assertEqual(self._recv(cliente), b"OK")

    def test_el_servidor_no_guarda_si_llegan_menos_bytes_que_los_declarados(
            self
    ):
        cliente, servidor = self._atendido_a_mano()

        cliente.send(b"UPLOAD 10 corto.bin")
        self.assertEqual(self._recv(cliente), b"OK")
        cliente.send(b"12345")
        servidor.resultado_o_error(5.0)

        self.assertEqual(os.listdir(self.storage), [])

    def test_el_servidor_no_guarda_si_llegan_mas_bytes_que_los_declarados(
            self
    ):
        """Si no, declarar poco y mandar mucho saltearia el limite."""
        cliente, servidor = self._atendido_a_mano()

        cliente.send(b"UPLOAD 3 largo.bin")
        self.assertEqual(self._recv(cliente), b"OK")
        cliente.send(b"mucho mas que tres bytes")
        servidor.resultado_o_error(5.0)

        self.assertEqual(os.listdir(self.storage), [])

    def test_el_servidor_contesta_error_a_un_pedido_mal_formado(self):
        cliente, servidor = self._atendido_a_mano()

        cliente.send(b"UPLOAD diez a.txt")
        respuesta = self._recv(cliente)
        servidor.resultado_o_error(5.0)

        self.assertTrue(respuesta.startswith(b"ERROR "), respuesta)
        self.assertEqual(os.listdir(self.storage), [])

    def test_el_upload_usa_basename_para_evitar_path_traversal(self):
        cliente, servidor = self._atendido_a_mano()

        cliente.send(b"UPLOAD 3 ../../fuera.txt")
        self.assertEqual(self._recv(cliente), b"OK")
        cliente.send(b"abc")
        servidor.resultado_o_error(5.0)

        self.assertEqual(os.listdir(self.storage), ["fuera.txt"])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "fuera.txt")))


class TestLimiteStopAndWait(_LimiteBase, SWTestCase):
    protocolo = "sw"


class TestLimiteSelectiveAck(_LimiteBase, SACKTestCase):
    protocolo = "sack"


if __name__ == "__main__":
    unittest.main()
