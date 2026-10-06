"""Verbose messages for the three-way handshake, on both sides.

Listener.accept() uses ListenTrace and AcceptTrace; BaseTransport.connect()
uses ConnectTrace. Since the handshake consists of three packets, there is
no progress or rate tracking: the timer only measures how long it takes to
establish the connection.
"""

from lib.logger.trace import Trace


class ListenTrace(Trace):
    """Describes the listening socket:
      what arrives before a handshake starts."""

    def invalid(self, addr):
        self.log(f"accept: datagrama invalido de {addr}, lo descarto")

    def unsupported(self, addr, protocol_id):
        self.log(
            f"accept: SYN de {addr} con protocolo {protocol_id} no "
            f"soportado, lo descarto"
        )

    def bad_version(self, addr, version):
        self.log(
            f"accept: SYN de {addr} con version {version} no soportada, "
            f"lo rechazo con ERR"
        )

    def syn(self, addr, client_isn, protocol):
        self.log(
            f"accept: llego SYN de {addr} (isn cliente={client_isn}, "
            f"protocolo={protocol})"
        )

    def closed(self):
        self.log(
            "accept: el socket del servidor se cerro mientras esperaba "
            "conexiones"
        )


class AcceptTrace(Trace):
    """Describes the server-side handshake with a specific client."""

    def __init__(self, tag, client, ephemeral, server_isn, tope):
        super().__init__(tag)
        self.client = client
        self.tope = tope
        self.log(f"accept: socket efimero {ephemeral} dedicado a {client}")
        self.log(
            f"accept: envio SYN-ACK a {client} (isn servidor={server_isn})"
        )

    def stray(self, addr):
        self.log(f"accept: descarto respuesta de {addr} durante el handshake")

    def bad_ack(self, got, expected):
        self.log(
            f"accept: ACK final con ack={got}, esperaba {expected}; "
            f"lo descarto"
        )

    def retransmit(self, intento):
        self.log(
            f"accept: sin ACK final de {self.client}, reenvio SYN-ACK "
            f"({intento}/{self.tope})"
        )

    def established(self, seq, exp_seq):
        self.log(
            f"accept: conexion establecida con {self.client} en "
            f"{self.elapsed:.2f}s (seq={seq}, seq esperado={exp_seq})"
        )

    def gave_up(self):
        self.log(
            f"accept: handshake con {self.client} abandonado tras "
            f"{self.tope} intentos"
        )


class ConnectTrace(Trace):
    """Describes the client-side handshake."""

    def __init__(self, tag, server, client_isn, timeout, tope):
        super().__init__(tag)
        self.server = server
        self.client_isn = client_isn
        self.tope = tope
        self.log(
            f"handshake: envio SYN a {server[0]}:{server[1]} "
            f"(isn={client_isn}, timeout {timeout}s, hasta {tope} intentos)"
        )

    def cancelled(self):
        self.log("handshake cancelado por el usuario")

    def invalid(self, addr):
        self.log(f"handshake: datagrama invalido de {addr}, lo descarto")

    def bad_version(self, addr, version, expected):
        self.log(
            f"handshake: respuesta de {addr} con version {version}, "
            f"esperaba {expected}; abandono"
        )

    def syn_ack(self, addr, server_isn):
        self.log(
            f"handshake: SYN-ACK de {addr} (isn servidor={server_isn}); "
            f"respondo el ACK final"
        )

    def bad_syn_ack(self, got):
        self.log(
            f"handshake: SYN-ACK con ack={got}, esperaba "
            f"{self.client_isn + 1}; lo descarto"
        )

    def retry(self, intento):
        self.log(
            f"handshake: sin respuesta al SYN, reintento "
            f"{intento}/{self.tope}"
        )

    def established(self, remote, seq, exp_seq):
        self.log(
            f"conexion establecida con {remote} en {self.elapsed:.2f}s "
            f"(seq={seq}, seq esperado={exp_seq})"
        )

    def failed(self):
        self.log(
            f"handshake fallido: {self.tope} intentos sin respuesta de "
            f"{self.server[0]}:{self.server[1]}"
        )
