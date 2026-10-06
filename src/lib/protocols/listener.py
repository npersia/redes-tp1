import socket

from lib.logger.logger import logger
import lib.protocols.base_transport as base_transport
from lib.protocols.base_transport import (
    RECV_BUFFER,
    VERSION,
    ConnectionClosed,
)
from lib.protocols.factory import TransportFactory
from lib.protocols.handshake_trace import AcceptTrace, ListenTrace
import lib.protocols.packet.packet as packet


class Listener:
    """Server listening socket.

    It does not have its own protocol: it waits for SYNs, and each client
    selects its protocol in the header. For each SYN, it completes the
    three-way handshake from a socket and returns the established connection
    using the class for that protocol.
    """

    TAG = "listener"

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = int(port)
        self.sock = None
        self.is_closed = True
        self.started = False
        self.timeout = base_transport.TIMEOUT
        self.max_retries = base_transport.MAX_RETRIES
        self.trace = ListenTrace(self.TAG)

    def start_server(self) -> None:
        """Opens the listening socket and prepares it to accept connections."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.host, self.port))
        self.sock.settimeout(self.timeout)
        self.is_closed = False
        self.started = True

    def accept(self) -> base_transport.BaseTransport:
        """Waits for a client's SYN and returns the established connection."""
        if not self.started:
            raise RuntimeError("No server initialized.")

        sock = self.sock

        while not self.is_closed:
            try:
                data, client_address = sock.recvfrom(RECV_BUFFER)
                if not packet.is_valid(data):
                    self.trace.invalid(client_address)
                    continue
                flags = bytes([packet.get_header_flags(data)])

                if packet.get_flag_SYN(flags):
                    client_isn = packet.get_header_sequence_paquet(data)
                    protocol_id = packet.get_header_protocol(data)
                    version = packet.get_header_version(data)
                    if version != VERSION:
                        self.trace.bad_version(client_address, version)
                        self._reject_version(
                            sock,
                            client_address,
                            client_isn,
                            protocol_id,
                        )
                        continue
                    peer_class = self._transport_for(protocol_id)
                    if peer_class is None:
                        self.trace.unsupported(client_address, protocol_id)
                        continue
                    self.trace.syn(client_address, client_isn, peer_class.TAG)
                    peer = self._accept_syn(
                        client_address, client_isn, peer_class
                    )
                    if peer is not None:
                        return peer
            except socket.timeout:
                return None
            except Exception as e:
                if self.is_closed:
                    self.trace.closed()
                    raise ConnectionClosed(
                        "server closed while accepting a connection."
                    )
                raise e
        raise ConnectionClosed("Server closed.")

    @staticmethod
    def _transport_for(protocol_id):
        """Connection handler class,
          based on the protocol requested by the SYN.
        Only classes that use this handshake are supported: they declare the
        corresponding PROTOCOL_ID in the header (TCP does not).
        """
        peer_class = TransportFactory.get_class_by_id(protocol_id)
        if peer_class is None or peer_class.PROTOCOL_ID != protocol_id:
            return None
        return peer_class

    @staticmethod
    def _reject_version(sock, client_address, client_isn, protocol_id):
        """Notifies the client with
        an ERR that its version does not match ours.
        It is sent from the listening socket: there is no connection, so no
        ephemeral socket is opened. It includes our VERSION, which allows the
        client to detect the mismatch, and its connect() terminates without
        retrying.
        """
        err_packet = packet.make_packet(
            version=VERSION,
            protocol=protocol_id,
            flags=packet.ERR_MASK,
            ack=client_isn + 1,
        )
        sock.sendto(err_packet, client_address)

    def _accept_syn(self, client_address, client_isn, peer_class):
        """Completes the handshake for a SYN already read from the network.
        Kept separate from accept() so the SYN does not have to be read again.
        Returns None if the client does not complete the handshake,
          and accept()
        continues waiting.
        """
        client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client_sock.bind((self.host, 0))
        client_sock.settimeout(self.timeout)

        server_isn = 0

        # Respond with SYN=1, ACK=1, seq=server_isn, ack=client_isn+1
        syn_packet = packet.make_packet(
            version=VERSION,
            protocol=peer_class.PROTOCOL_ID,
            flags=packet.SYN_MASK | packet.ACK_MASK,
            sequence_number=server_isn,
            ack=client_isn + 1,
        )

        trace = AcceptTrace(
            self.TAG,
            client_address,
            client_sock.getsockname(),
            server_isn,
            self.max_retries,
        )

        retries = 0
        while retries < self.max_retries:
            client_sock.sendto(syn_packet, client_address)
            try:
                # Wait for the client's ACK with SYN=0
                resp, addr = client_sock.recvfrom(RECV_BUFFER)
                if addr != client_address or not packet.is_valid(resp):
                    trace.stray(addr)
                    continue

                resp_flags = bytes([packet.get_header_flags(resp)])

                if not packet.get_flag_SYN(resp_flags):
                    if packet.get_header_ack(resp) == server_isn + 1:
                        peer = peer_class(
                            client_address[0],
                            client_address[1],
                            sock=client_sock,
                            remote_address=client_address,
                        )
                        peer._init_peer(server_isn + 1, client_isn + 1)
                        trace.established(
                            peer.sequence_number,
                            peer.exp_sequence_number,
                        )
                        return peer
                    trace.bad_ack(packet.get_header_ack(resp), server_isn + 1)
            except socket.timeout:
                retries += 1
                trace.retransmit(retries)

        trace.gave_up()
        return None

    def shutdown(self) -> None:
        """
        Stops listening. There is no peer to notify:
          already accepted connections
        have their own sockets and are closed separately.
        """
        self.close()

    def close(self) -> None:
        self.is_closed = True
        sock, self.sock = self.sock, None
        if sock is None:
            return

        logger.debug(
            f"[{self.TAG}] close(): dejo de escuchar en "
            f"{self.host}:{self.port}"
        )
        try:
            sock.close()
        except Exception as error:
            logger.debug(
                f"[{self.TAG}] close(): el socket ya venia mal ({error})"
            )
