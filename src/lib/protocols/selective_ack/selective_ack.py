import socket

from lib.logger.logger import logger
from lib.protocols.base_transport import BaseTransport, ConnectionClosed
import lib.protocols.packet.packet as packet
from lib.protocols.selective_ack.ack_receiver import ACKReceiver
from lib.protocols.selective_ack.ack_sender import ACKSender
from lib.protocols.selective_ack.sack_option import (
    MAX_BLOCKS,
    make_sack_option,
    parse_sack_option,
)


# CONSTANTS
TIMEOUT = 1.0
PROTOCOL_SELECTIVE_ACK = 2  # value in the protocol field of the header
VERSION = 1
MAX_RETRIES = 10
MAX_PAYLOAD_SIZE = 1400

CWND = 4
# in bytes
RWIND = CWND * MAX_PAYLOAD_SIZE

RECV_BUFFER = 2048
POLL_INTERVAL = 0.05  # socket poll granularity used by send()


class SelectiveAck(BaseTransport):

    def __init__(self, host: str, port: int, sock: socket.socket = None,
                 remote_address: tuple = None):
        self.host = host
        self.port = int(port)
        self.sock = sock
        self.remote_address = remote_address or (host, port)

        self.sequence_number = 0  # TODO should be random
        self.exp_sequence_number = 0  # TODO should be random
        self.is_closed = True
        self.timeout = TIMEOUT
        self.max_retries = MAX_RETRIES

        self.sender = None
        self.receiver = None

    ###########################################################################
    # Here starts the handshake, should be moved to BaseTransport
    ###########################################################################

    # Open the listening socket. The real accept() happens in accept().
    def start_server(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.host, self.port))
        self.is_closed = False

    def accept(self) -> "SelectiveAck":
        """Server side: wait for the SYN and open an ephemeral socket."""
        if not self.sock:
            raise RuntimeError("No server initialized.")

        while not self.is_closed:
            try:
                # 2048 is way above the classic 1500 MTU
                data, client_address = self.sock.recvfrom(RECV_BUFFER)
                flags = bytes([packet.get_header_flags(data)])

                if packet.get_flag_SYN(flags):
                    client_isn = packet.get_header_sequence_paquet(data)

                    # Ephemeral socket, used only with this client
                    client_sock = socket.socket(socket.AF_INET,
                                                socket.SOCK_DGRAM)
                    # 0 lets the OS pick a free port
                    client_sock.bind((self.host, 0))
                    client_sock.settimeout(self.timeout)

                    server_isn = 0

                    syn_packet = packet.make_packet(
                        version=VERSION,
                        protocol=PROTOCOL_SELECTIVE_ACK,
                        flags=packet.SYN_MASK | packet.ACK_MASK,
                        sequence_number=server_isn,
                        ack=client_isn + 1
                    )

                    retries = 0
                    while retries < self.max_retries:
                        client_sock.sendto(syn_packet, client_address)
                        try:
                            resp, addr = client_sock.recvfrom(RECV_BUFFER)
                            if addr != client_address:
                                continue

                            resp_flags = bytes(
                                [packet.get_header_flags(resp)])

                            if not packet.get_flag_SYN(resp_flags):
                                if (packet.get_header_ack(resp)
                                        == server_isn + 1):
                                    return self._make_peer(
                                        client_address, client_sock,
                                        server_isn + 1, client_isn + 1)
                        except socket.timeout:
                            retries += 1
            except socket.timeout:
                continue
            except Exception as e:
                if self.is_closed:
                    raise ConnectionClosed(
                        "server closed while accepting a connection.")
                raise e
        raise ConnectionClosed("Server closed.")

    def connect(self) -> None:
        """Client side: 3-way handshake."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(self.timeout)

        client_isn = self.sequence_number

        syn_packet = packet.make_packet(
            version=VERSION,
            protocol=PROTOCOL_SELECTIVE_ACK,
            flags=packet.SYN_MASK,
            sequence_number=client_isn
        )

        retries = 0
        while retries < self.max_retries:
            try:
                self.sock.sendto(syn_packet, (self.host, self.port))
                data, server_address = self.sock.recvfrom(RECV_BUFFER)
                flags = bytes([packet.get_header_flags(data)])

                if packet.get_flag_SYN(flags) and packet.get_flag_ACK(flags):
                    if packet.get_header_ack(data) == client_isn + 1:
                        self.remote_address = server_address
                        server_isn = packet.get_header_sequence_paquet(data)

                        ack_packet = packet.make_packet(
                            version=VERSION,
                            protocol=PROTOCOL_SELECTIVE_ACK,
                            flags=packet.ACK_MASK,  # SYN already consumed
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
        """Builds the other end of an already established connection."""
        peer = SelectiveAck(address[0], address[1], sock=sock,
                            remote_address=address)
        peer._init_peer(sequence_number, exp_sequence_number)
        return peer

    # Puts the session in a state ready to send and receive data.
    def _init_peer(self, sequence_number, exp_sequence_number) -> None:

        self.sequence_number = sequence_number
        self.exp_sequence_number = exp_sequence_number
        self.sender = ACKSender(sequence_number, CWND, self.timeout)
        self.receiver = ACKReceiver(exp_sequence_number, RWIND)
        self.sock.settimeout(POLL_INTERVAL)
        self.is_closed = False

    ###########################################################################
    # Here ends the handshake, should be moved to BaseTransport
    ###########################################################################

    # Send all the data, respecting the window, and wait for ACKs.
    def send(self, data: bytes) -> None:
        if self.is_closed or self.sock is None:
            raise ConnectionClosed(
                "the socket is not initialized or the connection is closed")

        chunks = [data[i:i + MAX_PAYLOAD_SIZE]
                  for i in range(0, len(data), MAX_PAYLOAD_SIZE)]
        if not chunks:
            chunks = [b""]

        total = len(chunks)
        sent = 0

        while sent < total or not self.sender.is_idle:
            while sent < total and self.sender.has_room():
                flags = packet.FIN_MASK if sent == total - 1 else 0
                segment = self.sender.add(chunks[sent], flags)
                self.sock.sendto(self._data_packet(segment),
                                 self.remote_address)
                logger.debug(
                    f"[SACK] Send seq={segment.seq} "
                    f"({len(segment.payload)} B) "
                    f"window={self.sender.in_flight}/{CWND}")
                sent += 1

            if self.sender.is_idle:
                break

            self._wait_event()

        logger.info(
            f"[SACK] Transfer sent: {len(data)} bytes, "
            f"{total} segments, send_base={self.sender.send_base}")

    # Wait for an ACK or a timeout, whichever comes first.
    def _wait_event(self) -> None:
        data = None
        try:
            data, addr = self.sock.recvfrom(RECV_BUFFER)
        except socket.timeout:
            data = None
        except OSError as error:
            if self.is_closed:
                raise ConnectionClosed("Connection closed.")
            raise error

        if data is not None and addr == self.remote_address:
            self._handle_ack(data)

        expired = self.sender.on_timeout()
        if expired is not None:
            self._retransmit(expired)

    # Process an incoming ACK: update the sender state, and retransmit when
    # the duplicate ACK threshold was reached.
    def _handle_ack(self, data: bytes) -> None:
        flags = self._flags(data)

        if packet.get_flag_ERR(flags):
            raise ConnectionClosed("Remote reported an error (ERR flag).")

        if not packet.get_flag_ACK(flags):
            return

        ack = packet.get_header_ack(data)
        blocks = parse_sack_option(packet.get_header_options(data))
        logger.debug(f"[SACK] ACK={ack} SACK={blocks}")

        target = self.sender.handle_ack(ack, blocks)
        if target is not None:
            self._retransmit(target, reason="fast retransmit")

    # Retransmit a segment due to timeout or fast retransmit.
    def _retransmit(self, segment, reason: str = "timeout") -> None:
        if segment.retries >= self.max_retries:
            raise ConnectionClosed(
                f"Too many retries for seq={segment.seq}.")

        self.sock.sendto(self._data_packet(segment), self.remote_address)
        segment.refresh(self.sender.timeout)
        logger.info(
            f"[SACK] Retransmission ({reason}) seq={segment.seq} "
            f"attempt={segment.retries}")

    # Receive the whole remote stream and reassemble it in order.
    def recv(self) -> bytes:
        if self.is_closed or self.sock is None:
            raise ConnectionClosed(
                "the socket is not initialized or the connection is closed")

        received = bytearray()
        # seq and n_bytes of the segment carrying the FIN. It can arrive out of
        # order and get buffered, so remember it and recheck after every
        # segment instead of looking only at the one that just came in.
        fin_end = None

        while True:
            try:
                data, addr = self.sock.recvfrom(RECV_BUFFER)
            except socket.timeout:
                if self.is_closed:
                    raise ConnectionClosed("Connection closed.")
                continue
            except OSError as error:
                if self.is_closed:
                    raise ConnectionClosed("Connection closed.")
                raise error

            if addr != self.remote_address:
                continue

            flags = self._flags(data)

            if packet.get_flag_ERR(flags):
                raise ConnectionClosed(
                    "Transfer aborted by a remote error (ERR flag).")

            # Our ACK was lost, so the peer is resending the SYN-ACK
            if packet.get_flag_SYN(flags) and packet.get_flag_ACK(flags):
                self.sock.sendto(self._ack_packet(), self.remote_address)
                continue

            payload = packet.get_payload(data)
            is_fin = packet.get_flag_FIN(flags)

            if not payload and not is_fin:
                continue  # pure ACK, not a data segment

            seq = packet.get_header_sequence_paquet(data)
            # A FIN with no payload (empty file) advances 1, same as SW
            n_bytes = len(payload) if payload else 1

            delivered = self.receiver.accept(seq, n_bytes, payload)
            received.extend(delivered)
            self.sock.sendto(self._ack_packet(), self.remote_address)

            if is_fin and fin_end is None:
                fin_end = seq + n_bytes

            logger.debug(
                f"[SACK] Received seq={seq} ({n_bytes} B) "
                f"delivered={len(delivered)} "
                f"rcv_next={self.receiver.rcv_next} "
                f"SACK={self.receiver.blocks()}")

            # The FIN only ends the transfer once every byte before it has
            # been delivered: if the FIN segment got buffered, we only find
            # that out here.
            if (fin_end is not None
                    and fin_end <= self.receiver.rcv_next):
                logger.info(
                    f"[SACK] Transfer received: {len(received)} bytes")
                return bytes(received)

    ###########################################################################
    # Here starts the shutdown, should be moved to BaseTransport
    ###########################################################################

    def shutdown(self) -> None:
        self.is_closed = True
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass

    def close(self) -> None:
        self.shutdown()

    ###########################################################################
    # Here ends the shutdown, should be moved to BaseTransport
    ###########################################################################

    ###########################################################################
    # Packet assembly
    ###########################################################################

    @staticmethod
    def _flags(data: bytes) -> bytes:
        return bytes([packet.get_header_flags(data)])

    def _data_packet(self, segment) -> bytes:
        """A data segment. Data packets carry no SACK options.

        They do carry the cumulative ACK, so a peer still waiting for the
        last handshake packet can finish the handshake on a data segment
        instead of resending its SYN-ACK forever.
        """
        return packet.make_packet(
            version=VERSION,
            protocol=PROTOCOL_SELECTIVE_ACK,
            flags=segment.flags | packet.ACK_MASK,
            sequence_number=segment.seq,
            ack=self.receiver.rcv_next,
            payload=segment.payload
        )

    def _ack_packet(self) -> bytes:
        """A cumulative ACK plus the SACK blocks the receiver holds."""
        return packet.make_packet(
            version=VERSION,
            protocol=PROTOCOL_SELECTIVE_ACK,
            flags=packet.ACK_MASK,
            sequence_number=self.sequence_number,
            ack=self.receiver.rcv_next,
            options=make_sack_option(
                self.receiver.option_blocks(MAX_BLOCKS))
        )
