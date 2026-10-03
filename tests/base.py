"""Base comun de los tests de Stop & Wait."""

import os
import socket
import sys
import threading
import time
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(RAIZ, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)
if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lib.protocols.packet.packet as packet                      # noqa: E402
import lib.protocols.base_transport as bt                         # noqa: E402
import lib.protocols.stop_and_wait.stop_wait as sw                # noqa: E402
from lib.protocols.base_transport import (                        # noqa: E402
    ConnectionClosed,
    TransferCancelled,
)

import netsim                                                     # noqa: E402
from netsim import Net, RawPeer, parse                            # noqa: E402

HOST = "127.0.0.1"

# Escala de tiempo de los tests: timeouts chicos para que la suite sea rapida
# pero suficientemente grandes frente al RTT de loopback (~decenas de us).
TEST_TIMEOUT = 0.05
TEST_RETRIES = 4


class Hilo(threading.Thread):
    """Hilo que guarda el resultado o la excepcion de la funcion."""

    def __init__(self, fn, *args, **kwargs):
        super().__init__(daemon=True)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self.resultado = None
        self.error = None

    def run(self):
        try:
            self.resultado = self._fn(*self._args, **self._kwargs)
        except BaseException as e:      # noqa: BLE001 - queremos capturar todo
            self.error = e

    def esperar(self, timeout=5.0):
        self.join(timeout)
        return not self.is_alive()

    def resultado_o_error(self, timeout=5.0):
        if not self.esperar(timeout):
            raise AssertionError("el hilo no termino a tiempo")
        if self.error is not None:
            raise self.error
        return self.resultado


class SWTestCase(unittest.TestCase):
    """Parchea el modulo con la red simulada y acelera los timeouts."""

    timeout = TEST_TIMEOUT
    retries = TEST_RETRIES

    def setUp(self):
        self.net = Net()
        # El handshake vive en base_transport y send/recv en stop_wait:
        # los dos modulos tienen que usar la red simulada.
        self._parches = [self.net.patch(sw), self.net.patch(bt)]
        for parche in self._parches:
            parche.__enter__()

        self._timeout_orig = bt.TIMEOUT
        self._retries_orig = bt.MAX_RETRIES
        bt.TIMEOUT = self.timeout
        bt.MAX_RETRIES = self.retries

        self._peers = []
        self._transportes = []

    def tearDown(self):
        bt.TIMEOUT = self._timeout_orig
        bt.MAX_RETRIES = self._retries_orig
        for t in self._transportes:
            try:
                t.close()
            except Exception:
                pass
        for p in self._peers:
            p.close()
        for parche in reversed(self._parches):
            parche.__exit__(None, None, None)

    # -- helpers ----------------------------------------------------------
    def peer(self):
        p = RawPeer(HOST)
        self._peers.append(p)
        return p

    def servidor(self, port=0):
        s = sw.StopAndWait(HOST, port)
        s.start_server()
        s.sock.role = "listen"
        self._transportes.append(s)
        return s

    def aceptar(self, servidor, timeout=5.0):
        """accept() reintentando: ahora propaga socket.timeout al llamador.

        Es el mismo patron que usa Dispatcher.start() en src/lib/server.py.
        """
        limite = time.monotonic() + timeout
        while time.monotonic() < limite:
            try:
                return servidor.accept()
            except socket.timeout:
                continue
        raise AssertionError("no llego ninguna conexion en el plazo esperado")

    def cliente(self, port):
        c = sw.StopAndWait(HOST, port)
        self._transportes.append(c)
        return c

    def conectados(self, policy_cliente=None, policy_servidor=None):
        """Handshake completo; devuelve (cliente, conexion_del_servidor)."""
        servidor = self.servidor()
        _, puerto = servidor.sock.getsockname()

        hilo = Hilo(self.aceptar, servidor)
        hilo.start()

        cliente = self.cliente(puerto)
        cliente.connect()
        cliente.sock.role = "cliente"

        conexion = hilo.resultado_o_error(5.0)
        conexion.sock.role = "servidor"
        self._transportes.append(conexion)

        # Las politicas se aplican recien despues del handshake.
        cliente.sock.policy = policy_cliente
        conexion.sock.policy = policy_servidor
        return cliente, conexion

    def assertPaquete(self, pkt, **esperado):
        for clave, valor in esperado.items():
            self.assertEqual(
                pkt[clave], valor,
                f"campo {clave}: esperaba {valor!r}, llego {pkt[clave]!r} "
                f"en {netsim.describe(pkt)}",
            )

    def volcado(self):
        return "\n--- trafico ---\n" + self.net.dump()
