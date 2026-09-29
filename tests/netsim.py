"""
Simulador de red para los tests de Stop & Wait.

No reemplaza a UDP: envuelve sockets UDP reales sobre loopback y se
interpone unicamente en el `sendto`, de modo que los timeouts, el
bloqueo y la semantica de `recvfrom` son los del sistema operativo.
Eso permite ejercitar los reintentos y los timeouts de verdad, solo que
con una escala de tiempo chica.

Uso tipico:

    net = Net()
    with net.patch(stop_wait_module):
        ...  # todo socket creado por el modulo queda instrumentado
"""

import contextlib
import socket as _real_socket
import threading
import time

import lib.protocols.packet.packet as packet


# ---------------------------------------------------------------------------
# Inspeccion de paquetes
# ---------------------------------------------------------------------------

def parse(data: bytes) -> dict:
    """Vuelca un datagrama RDT a un dict comodo para los asserts."""
    flags = bytes([packet.get_header_flags(data)])
    return {
        "version": packet.get_header_version(data),
        "protocol": packet.get_header_protocol(data),
        "SYN": packet.get_flag_SYN(flags),
        "FIN": packet.get_flag_FIN(flags),
        "ERR": packet.get_flag_ERR(flags),
        "ACK": packet.get_flag_ACK(flags),
        "CANCEL": packet.get_flag_CANCEL(flags),
        "hlen": packet.get_header_hlen(data),
        "seq": packet.get_header_sequence_paquet(data),
        "ack": packet.get_header_ack(data),
        "payload": packet.get_payload(data),
        "raw": data,
    }


def describe(p: dict) -> str:
    banderas = "+".join(
        nombre for nombre in ("SYN", "FIN", "ERR", "ACK", "CANCEL") if p[nombre]
    ) or "-"
    return f"[{banderas} seq={p['seq']} ack={p['ack']} len={len(p['payload'])}]"


# ---------------------------------------------------------------------------
# Politicas de perdida
# ---------------------------------------------------------------------------

PASS = "pass"
DROP = "drop"
DUP = "dup"


class Ctx:
    """Contexto que recibe una politica antes de que salga un datagrama."""

    def __init__(self, sock, data, addr, n):
        self.sock = sock
        self.data = data
        self.addr = addr
        self.n = n              # numero de datagrama de ESTE socket, 1-based
        self.pkt = parse(data)


def drop_nth(*indices):
    """Descarta los datagramas cuyo numero de orden este en `indices`."""
    objetivo = set(indices)
    return lambda ctx: DROP if ctx.n in objetivo else PASS


def dup_nth(*indices):
    objetivo = set(indices)
    return lambda ctx: DUP if ctx.n in objetivo else PASS


def drop_if(pred):
    return lambda ctx: DROP if pred(ctx) else PASS


def drop_data_nth(*indices):
    """Descarta el n-esimo datagrama *de datos* (ignora los ACK puros)."""
    objetivo = set(indices)
    contador = {"n": 0}

    def politica(ctx):
        if ctx.pkt["ACK"] and not ctx.pkt["payload"]:
            return PASS
        contador["n"] += 1
        return DROP if contador["n"] in objetivo else PASS

    return politica


def drop_acks_nth(*indices):
    """Descarta el n-esimo ACK puro."""
    objetivo = set(indices)
    contador = {"n": 0}

    def politica(ctx):
        if not (ctx.pkt["ACK"] and not ctx.pkt["payload"]):
            return PASS
        contador["n"] += 1
        return DROP if contador["n"] in objetivo else PASS

    return politica


def lossy(rate, rng):
    """Perdida aleatoria reproducible (rng es un random.Random sembrado)."""
    return lambda ctx: DROP if rng.random() < rate else PASS


def first_n_then(n, primera, segunda):
    return lambda ctx: primera(ctx) if ctx.n <= n else segunda(ctx)


# ---------------------------------------------------------------------------
# Socket instrumentado
# ---------------------------------------------------------------------------

class ControlledSocket:
    """Socket UDP real con un gancho de salida y registro de trafico."""

    def __init__(self, net, *args, **kwargs):
        self._net = net
        self._sock = _real_socket.socket(*args, **kwargs)
        self.policy = None          # los tests la asignan cuando quieren
        self.role = None            # etiqueta libre para leer los logs
        self.tx = 0                 # datagramas ofrecidos a sendto
        self.tx_wire = 0            # datagramas que realmente salieron
        self.rx = 0
        self.dropped = 0
        self.closed = False
        net._register(self)

    # -- API que usa StopAndWait ------------------------------------------
    def bind(self, addr):
        return self._sock.bind(addr)

    def settimeout(self, value):
        self._timeout = value
        return self._sock.settimeout(value)

    def gettimeout(self):
        return self._sock.gettimeout()

    def getsockname(self):
        return self._sock.getsockname()

    def sendto(self, data, addr):
        self.tx += 1
        ctx = Ctx(self, data, addr, self.tx)
        accion = PASS
        politica = self.policy or self._net.default_policy
        if politica is not None:
            accion = politica(ctx) or PASS

        if isinstance(accion, tuple):
            nombre, arg = accion
        else:
            nombre, arg = accion, None

        if nombre == DROP:
            self.dropped += 1
            self._net._log(self, "drop", addr, ctx.pkt)
            return len(data)

        if nombre == "replace":
            data = arg
            ctx.pkt = parse(data) if len(data) >= 12 else ctx.pkt

        if nombre == "delay":
            self._net._log(self, "delay", addr, ctx.pkt)
            t = threading.Timer(arg, self._wire, args=(data, addr, ctx.pkt))
            t.daemon = True
            self._net._timers.append(t)
            t.start()
            return len(data)

        enviado = self._wire(data, addr, ctx.pkt)
        if nombre == DUP:
            self._wire(data, addr, ctx.pkt, etiqueta="tx-dup")
        return enviado

    def _wire(self, data, addr, pkt, etiqueta="tx"):
        self.tx_wire += 1
        self._net._log(self, etiqueta, addr, pkt)
        try:
            return self._sock.sendto(data, addr)
        except OSError:
            return 0

    def recvfrom(self, bufsize):
        data, addr = self._sock.recvfrom(bufsize)
        self.rx += 1
        try:
            self._net._log(self, "rx", addr, parse(data))
        except IndexError:
            self._net._log(self, "rx-malformado", addr, None)
        return data, addr

    def close(self):
        self.closed = True
        return self._sock.close()

    def fileno(self):
        return self._sock.fileno()

    def __getattr__(self, name):
        return getattr(self._sock, name)


class _SocketModuleProxy:
    """Sustituto de `import socket` dentro del modulo bajo prueba.

    Delega todo al modulo real salvo la fabrica `socket`, que devuelve
    ControlledSocket. Asi `socket.timeout`, `AF_INET`, etc. siguen siendo
    los de verdad y no se toca el modulo socket global.
    """

    def __init__(self, net):
        self._net = net

    def socket(self, *args, **kwargs):
        s = ControlledSocket(self._net, *args, **kwargs)
        if self._net.on_create:
            self._net.on_create(s)
        return s

    def __getattr__(self, name):
        return getattr(_real_socket, name)


class Net:
    def __init__(self):
        self.sockets = []
        self.entries = []           # (t, role, evento, addr, pkt)
        self.default_policy = None
        self.on_create = None
        self._lock = threading.Lock()
        self._timers = []
        self._t0 = time.monotonic()

    def _register(self, sock):
        with self._lock:
            self.sockets.append(sock)

    def _log(self, sock, evento, addr, pkt):
        with self._lock:
            self.entries.append(
                (time.monotonic() - self._t0, sock.role or "?", evento, addr, pkt)
            )

    @contextlib.contextmanager
    def patch(self, modulo):
        original = modulo.socket
        modulo.socket = _SocketModuleProxy(self)
        try:
            yield self
        finally:
            modulo.socket = original
            for t in self._timers:
                t.cancel()
            for s in self.sockets:
                if not s.closed:
                    with contextlib.suppress(OSError):
                        s.close()

    # -- consultas ---------------------------------------------------------
    def sent(self, role=None, incluir_drops=True):
        eventos = {"tx", "tx-dup"} | ({"drop", "delay"} if incluir_drops else set())
        with self._lock:
            return [
                e[4] for e in self.entries
                if e[2] in eventos and (role is None or e[1] == role)
            ]

    def wire(self, role=None):
        return self.sent(role, incluir_drops=False)

    def dump(self):
        lineas = []
        for t, role, evento, addr, pkt in self.entries:
            desc = describe(pkt) if pkt else "<malformado>"
            lineas.append(f"{t*1000:8.1f}ms {role:>10} {evento:<8} {addr} {desc}")
        return "\n".join(lineas)


# ---------------------------------------------------------------------------
# Peer crudo: para inyectar paquetes a mano (paquetes invalidos, spoofing, ...)
# ---------------------------------------------------------------------------

class RawPeer:
    """Socket UDP pelado para inyectar datagramas arbitrarios."""

    def __init__(self, host="127.0.0.1"):
        self.sock = _real_socket.socket(_real_socket.AF_INET, _real_socket.SOCK_DGRAM)
        self.sock.bind((host, 0))
        self.sock.settimeout(2.0)

    @property
    def addr(self):
        return self.sock.getsockname()

    def send(self, data, addr):
        self.sock.sendto(data, addr)

    def send_pkt(self, addr, **kwargs):
        kwargs.setdefault("version", 1)
        kwargs.setdefault("protocol", 1)
        self.sock.sendto(packet.make_packet(**kwargs), addr)

    def recv(self, timeout=2.0):
        self.sock.settimeout(timeout)
        data, addr = self.sock.recvfrom(2048)
        return parse(data), addr

    def try_recv(self, timeout=0.3):
        try:
            return self.recv(timeout)
        except _real_socket.timeout:
            return None, None

    def drain(self, timeout=0.2):
        """Consume lo que haya pendiente y lo devuelve."""
        vistos = []
        while True:
            p, _ = self.try_recv(timeout)
            if p is None:
                return vistos
            vistos.append(p)

    def close(self):
        with contextlib.suppress(OSError):
            self.sock.close()


def salvo(pred, politica):
    """Aplica `politica` salvo cuando `pred(ctx)` es verdadero (ahi deja pasar)."""
    return lambda ctx: PASS if pred(ctx) else politica(ctx)


def es_ack_de(numero):
    return lambda ctx: ctx.pkt["ACK"] and ctx.pkt["ack"] == numero
