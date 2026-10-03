import socket
import threading
from abc import ABC, abstractmethod

from lib.logger.logger import logger
import lib.protocols.packet.packet as packet


# Valores por defecto del handshake. Los protocols sobre UDP los heredan tal
# cual; TCP los ignora porque no usa este handshake.
VERSION = 1
TIMEOUT = 1.0
MAX_RETRIES = 10
RECV_BUFFER = 2048  # muy por encima del MTU clasico de 1500
ABORT_NOTICES = 3 #cuantas veces repito el ERR al abortar, porque UDP lo puede perder y nadie lo confirma


class ConnectionClosed(Exception):
    """El otro extremo cortó la conexión antes de que terminara la transferencia."""


class TransferCancelled(ConnectionClosed):
    """
    Este extremo abortó la transferencia a pedido del usuario (Enter en la consola).
    """


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
        self.cancel_requested = threading.Event() #lo prende cancel() desde otro hilo
        self.is_listener = False #el socket de escucha no tiene un peer al que avisarle

    ###########################################################################
    # CANCELACION
    ###########################################################################

    def cancel(self) -> None:
        """
        Pide abortar la transferencia en curso. Se llama desde otro hilo.
        """
        logger.debug(f"[{self.TAG}] cancel(): cancelacion pedida desde otro hilo")
        self.cancel_requested.set()

    def is_cancelled(self) -> bool:
        return self.cancel_requested.is_set()

    def notify_abort(self) -> None:
        """
        Le avisa al otro extremo que cortamos, para que no quede esperando para siempre.

        Van los dos bits prendidos, ERR y CANCEL:
        - ERR: le avisa al otro extremo que hubo un error y que no espere mas.
        - CANCEL: le avisa al otro extremo que el error fue deliberado.
        """
        if self.sock is None or self.remote_address is None or self.is_listener:
            return #sin peer (o siendo el socket de escucha) el aviso iria a la nada

        err_packet = packet.make_packet(
            version=VERSION,
            protocol=self.PROTOCOL_ID,
            flags=packet.ERR_MASK | packet.CANCEL_MASK,
            sequence_number=self.sequence_number,
            ack=self.exp_sequence_number
        )

        logger.debug(f"[{self.TAG}] aviso la cancelacion a {self.remote_address} con "
                     f"{ABORT_NOTICES} paquetes de aborto (flags ERR+CANCEL)")
        for _ in range(ABORT_NOTICES):
            try:
                self.sock.sendto(err_packet, self.remote_address)
            except OSError as error:
                logger.debug(f"[{self.TAG}] no se pudo avisar el aborto: {error}")
                return

    ###########################################################################
    # HANDSHAKE
    ###########################################################################

    # Prefijo de las trazas de debug. Lo define cada subclase.
    TAG = "transporte"

    def start_server(self) -> None:
        """Abre el socket de escucha y lo deja listo para aceptar conexiones."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.host, self.port))
        self.sock.settimeout(self.timeout) #sin esto accept() bloquea para siempre y el servidor no se puede apagar
        self.is_closed = False
        self.is_listener = True #remote_address apunta a si mismo: no hay a quien avisarle

    def accept(self) -> 'BaseTransport':
        """lado servidor, espera el SYN y crea un socket efimero."""
        if not self.sock:
            raise RuntimeError("No server initialized.")

        while not self.is_closed:
            try:
                data, client_address = self.sock.recvfrom(RECV_BUFFER)
                if not packet.is_valid(data):
                    logger.debug(f"[{self.TAG}] accept: datagrama invalido de {client_address}, lo descarto")
                    continue
                flags = bytes([packet.get_header_flags(data)])

                if packet.get_flag_SYN(flags):
                    client_isn = packet.get_header_sequence_paquet(data)
                    logger.debug(f"[{self.TAG}] accept: llego SYN de {client_address} (isn cliente={client_isn})")
                    peer = self._accept_syn(data, client_address, client_isn)
                    if peer is not None:
                        return peer
            except socket.timeout:
                raise #que decida el llamador si sigue esperando (ver Dispatcher en server.py)
            except Exception as e:
                if self.is_closed:
                    logger.debug(f"[{self.TAG}] accept: el socket del servidor se cerro mientras esperaba conexiones")
                    raise ConnectionClosed(
                        "server closed while accepting a connection.")
                raise e
        raise ConnectionClosed("Server closed.")

    def _accept_syn(self, data, client_address, client_isn):
        """Completa el handshake de un SYN ya leido de la red.

        Va aparte de accept() para no tener que releer el SYN. Devuelve None si
        el cliente no completo el handshake, y accept() sigue esperando.
        """
        #aca creo un socket efimero para la comunicacion
        client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client_sock.bind((self.host, 0))  # el 0 hace que el SO asigne un puerto libre
        #algun timeout hay que poner para que no se quede esperando por siempre,
        #tambien para poder mandar de nuevo
        client_sock.settimeout(self.timeout)

        logger.debug(f"[{self.TAG}] accept: socket efimero {client_sock.getsockname()} dedicado a {client_address}")

        server_isn = 0  # TODO podria o deberia ser random, pero lo dejo en 0 para que sea mas facil

        #respondo con SYN=1, ACK=1, seq = server_isn, ack = client_isn+1
        syn_packet = packet.make_packet(
            version=VERSION,
            protocol=self.PROTOCOL_ID,
            flags=packet.SYN_MASK | packet.ACK_MASK,
            sequence_number=server_isn,
            ack=client_isn + 1
        )

        logger.debug(f"[{self.TAG}] accept: envio SYN-ACK a {client_address} (isn servidor={server_isn})")

        retries = 0
        while retries < self.max_retries:
            client_sock.sendto(syn_packet, client_address)
            try:
                #espero ACK del cliente y SYN=0
                resp, addr = client_sock.recvfrom(RECV_BUFFER)
                if addr != client_address or not packet.is_valid(resp):
                    logger.debug(f"[{self.TAG}] accept: descarto respuesta de {addr} durante el handshake")
                    continue

                resp_flags = bytes([packet.get_header_flags(resp)])

                if not packet.get_flag_SYN(resp_flags):
                    if packet.get_header_ack(resp) == server_isn + 1:
                        peer = self._make_peer(
                            client_address, client_sock,
                            server_isn + 1, client_isn + 1)
                        logger.debug(f"[{self.TAG}] accept: conexion establecida con {client_address} "
                                     f"(seq={peer.sequence_number}, "
                                     f"seq esperado={peer.exp_sequence_number})")
                        return peer
                    logger.debug(f"[{self.TAG}] accept: ACK final con ack={packet.get_header_ack(resp)}, "
                                 f"esperaba {server_isn + 1}; lo descarto")
            except socket.timeout:
                retries += 1
                logger.debug(f"[{self.TAG}] accept: sin ACK final de {client_address}, "
                             f"reenvio SYN-ACK ({retries}/{self.max_retries})")

        logger.debug(f"[{self.TAG}] accept: handshake con {client_address} abandonado tras {self.max_retries} intentos")
        return None

    def connect(self) -> None:
        """lado cliente, inicia la comunicacion."""

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(self.timeout)

        client_isn = self.sequence_number

        logger.debug(f"[{self.TAG}] handshake: envio SYN a {self.host}:{self.port} (isn={client_isn}, "
                     f"timeout {self.timeout}s, hasta {self.max_retries} intentos)")

        # SYN=1, seq = client_isn
        syn_packet = packet.make_packet(
            version=VERSION,
            protocol=self.PROTOCOL_ID,
            flags=packet.SYN_MASK,
            sequence_number=client_isn
        )

        retries = 0
        while retries < self.max_retries:
            if self.cancel_requested.is_set():
                logger.debug(f"[{self.TAG}] handshake cancelado por el usuario")
                raise TransferCancelled("Conexion cancelada por el usuario durante el handshake.")
            try:
                self.sock.sendto(syn_packet, (self.host, self.port))
                data, server_address = self.sock.recvfrom(RECV_BUFFER)
                if not packet.is_valid(data):
                    logger.debug(f"[{self.TAG}] handshake: datagrama invalido de {server_address}, lo descarto")
                    continue
                flags = bytes([packet.get_header_flags(data)])

                # SYN=1, ACK=1, ack = client_isn+1
                if packet.get_flag_SYN(flags) and packet.get_flag_ACK(flags):
                    if packet.get_header_ack(data) == client_isn + 1:
                        self.remote_address = server_address
                        server_isn = packet.get_header_sequence_paquet(data)

                        logger.debug(f"[{self.TAG}] handshake: SYN-ACK de {server_address} (isn servidor={server_isn}); "
                                     f"respondo el ACK final")

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
                        logger.debug(f"[{self.TAG}] conexion establecida con {self.remote_address} "
                                     f"(seq={self.sequence_number}, seq esperado={self.exp_sequence_number})")
                        return
                    logger.debug(f"[{self.TAG}] handshake: SYN-ACK con ack={packet.get_header_ack(data)}, "
                                 f"esperaba {client_isn + 1}; lo descarto")
            except socket.timeout:
                retries += 1
                logger.debug(f"[{self.TAG}] handshake: sin respuesta al SYN, reintento {retries}/{self.max_retries}")

        logger.debug(f"[{self.TAG}] handshake fallido: {self.max_retries} intentos sin respuesta de {self.host}:{self.port}")
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
    # CIERRE
    ###########################################################################

    def shutdown(self) -> None:
        """
        Aborta: le avisa al otro extremo y corta lo que este bloqueado.
        """
        if self.sock is None:
            self.is_closed = True
            return #ya se solto el socket: no repito el aviso ni el cierre

        self.notify_abort()
        logger.debug(f"[{self.TAG}] shutdown(): avise el corte a {self.remote_address}")
        self.close()

    def close(self) -> None:
        """
        Libera el socket sin avisarle nada al otro extremo.

        Cierre normal: tambien se llama al terminar una transferencia exitosa,
        asi que no puede mandar un aviso de aborto.
        """
        self.is_closed = True
        sock, self.sock = self.sock, None
        if sock is None:
            return

        logger.debug(f"[{self.TAG}] close(): cierro el socket local de {self.remote_address}")
        try:
            sock.close()
        except Exception as error:
            logger.debug(f"[{self.TAG}] close(): el socket ya venia mal ({error})")
