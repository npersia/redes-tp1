import socket
import threading
from abc import ABC, abstractmethod

from lib.logger.logger import logger
import lib.protocols.packet.packet as packet
from lib.protocols.handshake_trace import ConnectTrace


VERSION = 1
TIMEOUT = 0.10
MAX_RETRIES = 10
RECV_BUFFER = 2048
ABORT_NOTICES = 3


class ConnectionClosed(Exception):
    """The peer closed the connection before the transfer was completed."""


class TransferCancelled(ConnectionClosed):
    """
    This end aborted the transfer at the user's request
    (Enter pressed in the console).
    """


class UnsupportedVersion(ConnectionClosed):
    """The peer uses a different protocol version. It is detected during the
    handshake: on the client side, from the response to the SYN; on the server
    side, from the SYN (the Listener rejects it with an ERR).
    """

    def __init__(self, received, expected):
        super().__init__(
            f"Versión de protocolo no soportada: llegó {received}, "
            f"se esperaba {expected}."
        )
        self.received = received
        self.expected = expected


class BaseTransport(ABC):
    """Reliable transport over datagrams.

    Handles the client side of the three-way handshake, which is identical for
    all protocols except for the `protocol` nibble in the header and the state
    required by each protocol for data transfer. The server side is handled by
    Listener. Subclasses implement `send` and `recv`, as well as `_init_peer`
    to initialize their own state.
    """

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

        self.sequence_number = 0
        self.exp_sequence_number = 0
        self.is_closed = True
        self.timeout = TIMEOUT
        self.max_retries = MAX_RETRIES
        self.cancel_requested = threading.Event()

    def cancel(self) -> None:
        """
        Requests that the current transfer
          be aborted. Called from another thread.
        """
        logger.debug(
            f"[{self.TAG}] cancel(): cancelacion pedida desde otro hilo"
        )
        self.cancel_requested.set()

    def is_cancelled(self) -> bool:
        return self.cancel_requested.is_set()

    def notify_abort(self) -> None:
        """
        Notifies the peer that we have terminated the transfer, so it does not
        remain waiting indefinitely.

        Both bits are set: ERR and CANCEL:
        - ERR: notifies the peer that an error occurred and that it should stop
        waiting.
        - CANCEL: notifies the peer that the error was intentional.
        """
        if self.sock is None or self.remote_address is None:
            return

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

    def connect(self) -> None:
        """Client side; initiates communication."""

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(self.timeout)

        client_isn = self.sequence_number

        trace = ConnectTrace(
            self.TAG,
            (self.host, self.port),
            client_isn,
            self.timeout,
            self.max_retries,
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
        """
        Puts the session in a state where it can send and receive data.

        The handshake is already complete:
        each protocol initializes the additional
        state it needs on top of the sequence numbers.
          At a minimum, all protocols
        need the sequence numbers and
        must mark the connection as open; protocols
        with additional state (such as SACK with its window)
          override this method.
        """
        self.sequence_number = sequence_number
        self.exp_sequence_number = exp_sequence_number
        self.is_closed = False

    @abstractmethod
    def send(self, data: bytes) -> None:
        """Receives a buffer of any size;
          the client and server do not need to know
        how the protocol handles packet segmentation.
        """
        pass

    @abstractmethod
    def recv(self) -> bytes:
        """Reassembles the parts of a buffer and returns it transparently."""
        pass

    def shutdown(self) -> None:
        """
        Aborts the transfer:
        notifies the peer and terminates whatever is blocked.
        """
        if self.sock is None:
            self.is_closed = True
            return

        self.notify_abort()
        logger.debug(
            f"[{self.TAG}] shutdown(): avise el corte a {self.remote_address}"
        )
        self.close()

    def close(self) -> None:
        """
        Closes the socket without notifying the peer.

        Normal closure: this is also called after a successful transfer, so it
        cannot send an abort notification.
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
