"""Los mensajes de -v del handshake de 3 vias, de los dos lados.

Listener.accept() usa ListenTrace y AcceptTrace; BaseTransport.connect() usa
ConnectTrace. Como el handshake son tres paquetes no hay progreso ni tasas:
lo que aporta el reloj es cuanto tardo en establecerse la conexion.
"""

from lib.logger.trace import Trace


class ListenTrace(Trace):
    """Narra el socket de escucha: lo que llega antes de arrancar un handshake."""

    def invalid(self, addr):
        self.log(f"accept: datagrama invalido de {addr}, lo descarto")

    def unsupported(self, addr, protocol_id):
        self.log(f"accept: SYN de {addr} con protocolo {protocol_id} no soportado, lo descarto")

    def syn(self, addr, client_isn, protocol):
        self.log(f"accept: llego SYN de {addr} (isn cliente={client_isn}, protocolo={protocol})")

    def closed(self):
        self.log("accept: el socket del servidor se cerro mientras esperaba conexiones")


class AcceptTrace(Trace):
    """Narra el handshake del lado servidor con un cliente puntual."""

    def __init__(self, tag, client, ephemeral, server_isn, tope):
        super().__init__(tag)
        self.client = client
        self.tope = tope
        self.log(f"accept: socket efimero {ephemeral} dedicado a {client}")
        self.log(f"accept: envio SYN-ACK a {client} (isn servidor={server_isn})")

    def stray(self, addr):
        self.log(f"accept: descarto respuesta de {addr} durante el handshake")

    def bad_ack(self, got, expected):
        self.log(f"accept: ACK final con ack={got}, esperaba {expected}; lo descarto")

    def retransmit(self, intento):
        self.log(f"accept: sin ACK final de {self.client}, reenvio SYN-ACK ({intento}/{self.tope})")

    def established(self, seq, exp_seq):
        self.log(f"accept: conexion establecida con {self.client} en {self.elapsed:.2f}s "
                 f"(seq={seq}, seq esperado={exp_seq})")

    def gave_up(self):
        self.log(f"accept: handshake con {self.client} abandonado tras {self.tope} intentos")


class ConnectTrace(Trace):
    """Narra el handshake del lado cliente."""

    def __init__(self, tag, server, client_isn, timeout, tope):
        super().__init__(tag)
        self.server = server
        self.client_isn = client_isn
        self.tope = tope
        self.log(f"handshake: envio SYN a {server[0]}:{server[1]} (isn={client_isn}, "
                 f"timeout {timeout}s, hasta {tope} intentos)")

    def cancelled(self):
        self.log("handshake cancelado por el usuario")

    def invalid(self, addr):
        self.log(f"handshake: datagrama invalido de {addr}, lo descarto")

    def syn_ack(self, addr, server_isn):
        self.log(f"handshake: SYN-ACK de {addr} (isn servidor={server_isn}); respondo el ACK final")

    def bad_syn_ack(self, got):
        self.log(f"handshake: SYN-ACK con ack={got}, esperaba {self.client_isn + 1}; lo descarto")

    def retry(self, intento):
        self.log(f"handshake: sin respuesta al SYN, reintento {intento}/{self.tope}")

    def established(self, remote, seq, exp_seq):
        self.log(f"conexion establecida con {remote} en {self.elapsed:.2f}s "
                 f"(seq={seq}, seq esperado={exp_seq})")

    def failed(self):
        self.log(f"handshake fallido: {self.tope} intentos sin respuesta de "
                 f"{self.server[0]}:{self.server[1]}")
