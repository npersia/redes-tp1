"""Base comun de los tests de Stop & Wait."""

import os
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
import lib.protocols.listener as listener                         # noqa: E402
import lib.protocols.stop_and_wait.stop_wait as sw                # noqa: E402
import lib.protocols.selective_ack.selective_ack as sack          # noqa: E402
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
    # Clase del cliente que arma cliente()/conectados(). La conexion del
    # servidor la elige el Listener segun el protocolo del SYN.
    transporte = sw.StopAndWait

    def setUp(self):
        self.net = Net()
        # El handshake vive en base_transport (cliente) y listener (servidor),
        # y send/recv en cada protocolo: todos tienen que usar la red simulada.
        self._parches = [self.net.patch(sw), self.net.patch(bt),
                         self.net.patch(listener), self.net.patch(sack)]
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
        s = listener.Listener(HOST, port)
        s.start_server()
        s.sock.role = "listen"
        self._transportes.append(s)
        return s

    def aceptar(self, servidor, timeout=5.0):
        """accept() reintentando: devuelve None si vence su timeout sin conexion.

        Es el mismo patron que usa Dispatcher.start() en src/lib/server.py.
        """
        limite = time.monotonic() + timeout
        while time.monotonic() < limite:
            conexion = servidor.accept()
            if conexion is not None:
                return conexion
        raise AssertionError("no llego ninguna conexion en el plazo esperado")

    def cliente(self, port):
        c = self.transporte(HOST, port)
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


# Timeout para los tests de SACK que cuentan retransmisiones EXACTAS.
# Con varios segmentos en vuelo, cada uno con su timer, una demora del
# scheduler mayor al timeout dispara reenvios espurios: con 0.05 s fallaban ~6
# por corrida con la CPU saturada. Con 0.2 s la demora tiene que ser 4 veces
# mayor. Los tests que solo miran integridad (perdida aleatoria, errores) se
# quedan con TEST_TIMEOUT para no alargar la suite.
SACK_TIMEOUT_EXACTO = 0.2


class SACKTestCase(SWTestCase):
    """Igual que SWTestCase, pero los clientes hablan Selective ACK."""

    transporte = sack.SelectiveAck

    def transporte_a_mano(self, seq=1000, exp=2000):
        """Un SelectiveAck 'conectado' a mano contra un peer crudo.

        Sin handshake: arma el socket, el remoto y el estado de la ventana
        (`_init_peer`) como los dejaria un connect() exitoso.
        """
        peer = self.peer()
        t = sack.SelectiveAck(HOST, peer.addr[1])
        t.sock = sack.socket.socket(sack.socket.AF_INET, sack.socket.SOCK_DGRAM)
        t.sock.bind((HOST, 0))
        t.sock.role = "local"
        t.remote_address = peer.addr
        t._init_peer(seq, exp)
        self._transportes.append(t)
        return t, peer

    def transferir(self, emisor, receptor, datos, timeout=10.0):
        """`emisor.send(datos)` contra `receptor.recv()`; devuelve lo recibido.

        Propaga la excepcion de cualquiera de los dos lados.
        """
        recibiendo = Hilo(receptor.recv)
        recibiendo.start()
        enviando = Hilo(emisor.send, datos)
        enviando.start()
        enviando.resultado_o_error(timeout)
        return recibiendo.resultado_o_error(timeout)

    def direcciones(self, emisor=None, receptor=None):
        """Las dos direcciones de una conexion real: (nombre, emisor, receptor, rol del emisor, rol del receptor).

        `emisor` y `receptor` son fabricas de politicas (sin argumentos): las
        politicas llevan estado, asi que cada direccion arma las suyas sobre su
        propia conexion.
        """
        for nombre in ("cliente->servidor", "servidor->cliente"):
            pol_e = emisor() if emisor else None
            pol_r = receptor() if receptor else None
            if nombre == "cliente->servidor":
                cliente, conexion = self.conectados(policy_cliente=pol_e, policy_servidor=pol_r)
                yield nombre, cliente, conexion, "cliente", "servidor"
            else:
                cliente, conexion = self.conectados(policy_cliente=pol_r, policy_servidor=pol_e)
                yield nombre, conexion, cliente, "servidor", "cliente"

    def assertVentanaRespetada(self, rol_emisor, rol_receptor, cwnd=None):
        """Nunca hay mas de CWND segmentos nuevos sin confirmar.

        Por causalidad: para que el emisor mande su k-esimo segmento nuevo, el
        receptor tuvo que haber emitido antes un ACK que cubra el (k-CWND)-esimo.
        """
        cwnd = cwnd or sack.CWND
        nuevos = []         # end de cada seq nuevo, en orden de salida
        vistos = set()
        mejor_ack = 0
        for _, rol, evento, _, p in self.net.entries:
            if not p or evento not in ("tx", "drop", "delay"):
                continue
            if rol == rol_receptor and netsim.es_ack_puro_sack_pkt(p) and evento == "tx":
                mejor_ack = max(mejor_ack, p["ack"])
            elif rol == rol_emisor and netsim.es_dato_sack_pkt(p) and p["seq"] not in vistos:
                vistos.add(p["seq"])
                nuevos.append(p["seq"] + (len(p["payload"]) or 1))
                # el volcado se arma solo si falla: es todo el trafico
                if len(nuevos) > cwnd and mejor_ack < nuevos[-cwnd - 1]:
                    self.fail(f"salio el segmento nuevo #{len(nuevos)} con {cwnd} sin confirmar"
                              f"{self.volcado()}")

    def tiempos_por_seq(self, role):
        """seq -> instantes (s) en que salio cada copia de ese segmento desde `role`,
        se haya perdido o no."""
        tiempos = {}
        for t, rol, evento, _, p in self.net.entries:
            if rol == role and evento in ("tx", "drop", "delay") and p and netsim.es_dato_sack_pkt(p):
                tiempos.setdefault(p["seq"], []).append(t)
        return tiempos

    def datos_en_el_cable(self, role):
        """Segmentos de datos (payload o FIN) que salieron de `role`, sin los tirados."""
        return [p for p in self.net.wire(role) if netsim.es_dato_sack_pkt(p)]

    def copias_por_seq(self, role, incluir_drops=True):
        """Cuantas veces salio cada seq de datos desde `role` (contando los tirados)."""
        cuenta = {}
        for p in self.net.sent(role, incluir_drops=incluir_drops):
            if netsim.es_dato_sack_pkt(p):
                cuenta[p["seq"]] = cuenta.get(p["seq"], 0) + 1
        return cuenta
