import socket
import threading
from abc import ABC, abstractmethod

from lib.logger.logger import logger
import lib.protocols.packet.packet as packet
from lib.protocols.handshake_trace import ConnectTrace


# Valores por defecto del handshake. Los protocols sobre UDP los heredan tal
# cual; TCP los ignora porque no usa este handshake.
VERSION = 1
TIMEOUT = 0.10
MAX_RETRIES = 10
RECV_BUFFER = 2048  # muy por encima del MTU clasico de 1500
# cuantas veces repito el ERR al abortar, porque UDP lo puede perder
# y nadie lo confirma
ABORT_NOTICES = 3


class ConnectionClosed(Exception):
    """El otro extremo cortó la conexión antes de que terminara
    la transferencia."""


class TransferCancelled(ConnectionClosed):
    """
    Este extremo abortó la transferencia a pedido del usuario
    (Enter en la consola).
    """


class UnsupportedVersion(ConnectionClosed):
    """El otro extremo habla otra version del protocolo. Se detecta en el
    handshake: del lado cliente por la respuesta al SYN, del lado servidor
    por el SYN (el Listener lo rechaza con un ERR)."""

    def __init__(self, received, expected):
        super().__init__(
            f"Versión de protocolo no soportada: llegó {received}, "
            f"se esperaba {expected}."
        )
        self.received = received
        self.expected = expected


class BaseTransport(ABC):
    """Transporte confiable sobre datagramas.

    Concentra el lado cliente del handshake de 3 vias, que es identico para
    todos los protocolos salvo por el nibble `protocol` del header y el
    estado que cada uno necesita para transferir. El lado servidor lo hace
    Listener. Las subclases implementan `send` y `recv`, y `_init_peer`
    para preparar su propio estado.
    """

    # Nibble `protocol` del header. Lo define cada subclase.
    PROTOCOL_ID = 0
    TAG = "transporte"

    def __init__(
        self,
        host: str,
        port: int,
        sock: socket.socket = None,
        remote_address: tuple = None,
    ):
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
        # lo prende cancel() desde otro hilo
        self.cancel_requested = threading.Event()

    ###########################################################################
    # CANCELACION
    ###########################################################################

    def cancel(self) -> None:
        """
        Pide abortar la transferencia en curso. Se llama desde otro hilo.
        """
        logger.debug(
            f"[{self.TAG}] cancel(): cancelacion pedida desde otro hilo"
        )
        self.cancel_requested.set()

    def is_cancelled(self) -> bool:
        return self.cancel_requested.is_set()

    def notify_abort(self) -> None:
        """
        Le avisa al otro extremo que cortamos, para que no quede
        esperando para siempre.

        Van los dos bits prendidos, ERR y CANCEL:
        - ERR: le avisa al otro extremo que hubo un error y que no
          espere mas.
        - CANCEL: le avisa al otro extremo que el error fue deliberado.
        """
        if self.sock is None or self.remote_address is None:
            return  # sin peer el aviso iria a la nada

        err_packet = packet.make_packet(
            version=VERSION,
            protocol=self.PROTOCOL_ID,
            flags=packet.ERR_MASK | packet.CANCEL_MASK,
            sequence_number=self.sequence_number,
            ack=self.exp_sequence_number,
        )

        logger.debug(
            f"[{self.TAG}] aviso la cancelacion a {self.remote_address} con "
            f"{ABORT_NOTICES} paquetes de aborto (flags ERR+CANCEL)"
        )
        for _ in range(ABORT_NOTICES):
            try:
                self.sock.sendto(err_packet, self.remote_address)
            except OSError as error:
                logger.debug(
                    f"[{self.TAG}] no se pudo avisar el aborto: {error}"
                )
                return

    ###########################################################################
    # HANDSHAKE
    ###########################################################################

    def connect(self) -> None:
        """lado cliente, inicia la comunicacion."""

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(self.timeout)

        client_isn = self.sequence_number

        trace = ConnectTrace(
            self.TAG, (self.host, self.port), client_isn,
            self.timeout, self.max_retries
        )

        # SYN=1, seq = client_isn
        syn_packet = packet.make_packet(
            version=VERSION,
            protocol=self.PROTOCOL_ID,
            flags=packet.SYN_MASK,
            sequence_number=client_isn,
        )

        retries = 0
        while retries < self.max_retries:
            if self.cancel_requested.is_set():
                trace.cancelled()
                raise TransferCancelled(
                    "Conexion cancelada por el usuario durante el handshake."
                )
            try:
                self.sock.sendto(syn_packet, (self.host, self.port))
                data, server_address = self.sock.recvfrom(RECV_BUFFER)
                if not packet.is_valid(data):
                    trace.invalid(server_address)
                    continue

                # Cualquier respuesta con otra version corta, incluido el ERR
                # con que el Listener rechaza la nuestra: reintentar no sirve.
                version = packet.get_header_version(data)
                if version != VERSION:
                    trace.bad_version(server_address, version, VERSION)
                    raise UnsupportedVersion(
                        received=version, expected=VERSION
                    )

                flags = bytes([packet.get_header_flags(data)])

                # SYN=1, ACK=1, ack = client_isn+1
                if packet.get_flag_SYN(flags) and packet.get_flag_ACK(flags):
                    if packet.get_header_ack(data) == client_isn + 1:
                        self.remote_address = server_address
                        server_isn = packet.get_header_sequence_paquet(data)

                        trace.syn_ack(server_address, server_isn)

                        # SYN=0, seq=client_isn+1, ack = server_isn+1
                        ack_packet = packet.make_packet(
                            version=VERSION,
                            protocol=self.PROTOCOL_ID,
                            # la variable ya tiene el SYN = 0
                            flags=packet.ACK_MASK,
                            sequence_number=client_isn + 1,
                            ack=server_isn + 1,
                        )

                        self.sock.sendto(ack_packet, self.remote_address)
                        self._init_peer(client_isn + 1, server_isn + 1)
                        trace.established(
                            self.remote_address,
                            self.sequence_number,
                            self.exp_sequence_number,
                        )
                        return
                    trace.bad_syn_ack(packet.get_header_ack(data))
            except socket.timeout:
                retries += 1
                trace.retry(retries)

        trace.failed()
        raise ConnectionClosed(
            f"Timeout: can't connect to {self.host}:{self.port}."
        )

    def _init_peer(self, sequence_number, exp_sequence_number) -> None:
        """Pone la sesion en estado de poder enviar y recibir datos.

        El handshake ya esta hecho: aca cada protocolo arma el estado
        extra que necesita encima de los numeros de secuencia. Lo minimo
        que todos necesitan son los numeros y marcar la conexion abierta;
        el que tenga algo mas (SACK con su ventana) lo sobreescribe.
        """
        self.sequence_number = sequence_number
        self.exp_sequence_number = exp_sequence_number
        self.is_closed = False

    ###########################################################################
    # FIN DEL HANDSHAKE
    ###########################################################################

    @abstractmethod
    def send(self, data: bytes) -> None:
        """Recibe un buffer de cualquier tamaño, cliente y servidor no
        tienen idea de como el protocolo maneja el particionado"""
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
            return  # ya se solto el socket: no repito el aviso ni el cierre

        self.notify_abort()
        logger.debug(
            f"[{self.TAG}] shutdown(): avise el corte a {self.remote_address}"
        )
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

        logger.debug(
            f"[{self.TAG}] close(): cierro el socket local de "
            f"{self.remote_address}"
        )
        try:
            sock.close()
        except Exception as error:
            logger.debug(
                f"[{self.TAG}] close(): el socket ya venia mal ({error})"
            )
