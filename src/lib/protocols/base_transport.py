import socket
from abc import ABC, abstractmethod

import lib.protocols.packet.packet as packet


# Valores por defecto del handshake. Los protocols sobre UDP los heredan tal
# cual; TCP los ignora porque no usa este handshake.
VERSION = 1
TIMEOUT = 1.0
MAX_RETRIES = 10
RECV_BUFFER = 2048  # muy por encima del MTU clasico de 1500


class ConnectionClosed(Exception):
    """El otro extremo cortó la conexión antes de que terminara la transferencia."""


class BaseTransport(ABC):
    """Transporte confiable sobre datagramas.

    Concentra el handshake de 3 vias, que es identico para todos los
    protocolos salvo por el nibble `protocol` del header y el estado que cada
    uno necesita para transferir. Las subclases implementan `send` y `recv`,
    y `_init_peer` para preparar su propio estado.
    """

    # Nibble `protocol` del header. Lo define cada subclase.
    PROTOCOL_ID = 0

    def __init__(self, host: str, port: int, sock: socket.socket = None,
                 remote_address: tuple = None):
        self.host = host
        self.port = int(port)
        self.sock = sock
        self.remote_address = remote_address or (host, port)

        self.sequence_number = 0  # TODO deberia ser un random
        self.exp_sequence_number = 0  # TODO deberia ser un random
        # Antes de empezar considero que no hay conexion, entonces esta cerrado
        self.is_closed = True
        self.timeout = TIMEOUT
        self.max_retries = MAX_RETRIES

    ###########################################################################
    # HANDSHAKE
    ###########################################################################

    def start_server(self) -> None:
        """Abre el socket de escucha y lo deja listo para aceptar conexiones."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.host, self.port))
        self.is_closed = False

    def accept(self) -> 'BaseTransport':
        """lado servidor, espera el SYN y crea un socket efimero."""
        if not self.sock:
            raise RuntimeError("No server initialized.")

        while not self.is_closed:
            try:
                data, client_address = self.sock.recvfrom(RECV_BUFFER)
                flags = bytes([packet.get_header_flags(data)])

                if packet.get_flag_SYN(flags):
                    client_isn = packet.get_header_sequence_paquet(data)
                    return self._accept_syn(data, client_address, client_isn)
            except socket.timeout:
                continue
            except Exception as e:
                if self.is_closed:
                    raise ConnectionClosed(
                        "server closed while accepting a connection.")
                raise e
        raise ConnectionClosed("Server closed.")

    def _accept_syn(self, data, client_address, client_isn):
        """Completa el handshake de un SYN ya leido de la red.

        Va aparte de accept() para no tener que releer el SYN.
        """
        #aca creo un socket efimero para la comunicacion
        client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client_sock.bind((self.host, 0))  # el 0 hace que el SO asigne un puerto libre
        #algun timeout hay que poner para que no se quede esperando por siempre,
        #tambien para poder mandar de nuevo
        client_sock.settimeout(self.timeout)

        server_isn = 0  # TODO podria o deberia ser random, pero lo dejo en 0 para que sea mas facil

        #respondo con SYN=1, ACK=1, seq = server_isn, ack = client_isn+1
        syn_packet = packet.make_packet(
            version=VERSION,
            protocol=self.PROTOCOL_ID,
            flags=packet.SYN_MASK | packet.ACK_MASK,
            sequence_number=server_isn,
            ack=client_isn + 1
        )

        retries = 0
        while retries < self.max_retries:
            client_sock.sendto(syn_packet, client_address)
            try:
                #espero ACK del cliente y SYN=0
                resp, addr = client_sock.recvfrom(RECV_BUFFER)
                if addr != client_address:
                    continue

                resp_flags = bytes([packet.get_header_flags(resp)])

                if not packet.get_flag_SYN(resp_flags):
                    if packet.get_header_ack(resp) == server_isn + 1:
                        return self._make_peer(
                            client_address, client_sock,
                            server_isn + 1, client_isn + 1)
            except socket.timeout:
                retries += 1

        raise ConnectionClosed("Timeout: handshake not completed.")

    def connect(self) -> None:
        """lado cliente, inicia la comunicacion."""

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(self.timeout)

        client_isn = self.sequence_number

        # SYN=1, seq = client_isn
        syn_packet = packet.make_packet(
            version=VERSION,
            protocol=self.PROTOCOL_ID,
            flags=packet.SYN_MASK,
            sequence_number=client_isn
        )

        retries = 0
        while retries < self.max_retries:
            try:
                self.sock.sendto(syn_packet, (self.host, self.port))
                data, server_address = self.sock.recvfrom(RECV_BUFFER)
                flags = bytes([packet.get_header_flags(data)])

                # SYN=1, ACK=1, ack = client_isn+1
                if packet.get_flag_SYN(flags) and packet.get_flag_ACK(flags):
                    if packet.get_header_ack(data) == client_isn + 1:
                        self.remote_address = server_address
                        server_isn = packet.get_header_sequence_paquet(data)

                        # SYN=0, seq=client_isn+1, ack = server_isn+1
                        ack_packet = packet.make_packet(
                            version=VERSION,
                            protocol=self.PROTOCOL_ID,
                            flags=packet.ACK_MASK,  # la variable ya tiene el SYN = 0
                            sequence_number=client_isn + 1,
                            ack=server_isn + 1
                        )

                        self.sock.sendto(ack_packet, self.remote_address)
                        self._init_peer(client_isn + 1, server_isn + 1)
                        return
            except socket.timeout:
                retries += 1

        raise ConnectionClosed(
            f"Timeout: can't connect to {self.host}:{self.port}.")

    def _make_peer(self, address, sock, sequence_number, exp_sequence_number):
        """Construye el otro extremo de una conexion ya establecida."""
        peer = type(self)(address[0], address[1], sock=sock,
                          remote_address=address)
        peer._init_peer(sequence_number, exp_sequence_number)
        return peer

    def _init_peer(self, sequence_number, exp_sequence_number) -> None:
        """Pone la sesion en estado de poder enviar y recibir datos.

        El handshake ya esta hecho: aca cada protocolo arma el estado extra que
        necesita encima de los numeros de secuencia. Lo minimo que todos
        necesitan son los numeros y marcar la conexion abierta; el que tenga
        algo mas (SACK con su ventana) lo sobreescribe.
        """
        self.sequence_number = sequence_number
        self.exp_sequence_number = exp_sequence_number
        self.is_closed = False

    ###########################################################################
    # FIN DEL HANDSHAKE
    ###########################################################################

    @abstractmethod
    def send(self, data: bytes) -> None:
        """Recibe un buffer de cualquier tamaño,
        cliente y servidor no tienen idea de como el protocolo maneja el particionado"""
        pass

    @abstractmethod
    def recv(self) -> bytes:
        """Rearma las partes de un buffer y lo entrega transparente"""
        pass

    ###########################################################################
    # SHUTDOWN
    ###########################################################################

    def shutdown(self) -> None:
        """Aborta la transferencia en curso de cualquier send/recv pendiente."""
        self.is_closed = True
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass

    def close(self) -> None:
        self.shutdown()