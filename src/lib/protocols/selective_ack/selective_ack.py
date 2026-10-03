import socket

from lib.logger.logger import logger
from lib.protocols.base_transport import (
    RECV_BUFFER,
    TIMEOUT,
    VERSION,
    BaseTransport,
    ConnectionClosed,
)
import lib.protocols.packet.packet as packet
from lib.protocols.selective_ack.ack_receiver import ACKReceiver
from lib.protocols.selective_ack.ack_sender import ACKSender
from lib.protocols.selective_ack.sack_option import (
    MAX_BLOCKS,
    make_sack_option,
    parse_sack_option,
)


# CONSTANTS
MAX_PAYLOAD_SIZE = 1400

CWND = 4
# in bytes
RWIND = CWND * MAX_PAYLOAD_SIZE

POLL_INTERVAL = 0.05  # socket poll granularity used by send()


class SelectiveAck(BaseTransport):
    PROTOCOL_ID = 2
    TAG = "SACK"

    ###########################################################################
    # Here ends the handshake, it is already in BaseTransport
    ###########################################################################

    # Puts the session in a state ready to send and receive data.
    def _init_peer(self, sequence_number, exp_sequence_number) -> None:
        self.sequence_number = sequence_number
        self.exp_sequence_number = exp_sequence_number
        self.sender = ACKSender(sequence_number, CWND, self.timeout)
        self.receiver = ACKReceiver(exp_sequence_number, RWIND)
        self.sock.settimeout(POLL_INTERVAL)
        self.is_closed = False

    ###########################################################################
    # Here starts the data transfer
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
            protocol=self.PROTOCOL_ID,
            flags=segment.flags | packet.ACK_MASK,
            sequence_number=segment.seq,
            ack=self.receiver.rcv_next,
            payload=segment.payload
        )

    def _ack_packet(self) -> bytes:
        """A cumulative ACK plus the SACK blocks the receiver holds."""
        return packet.make_packet(
            version=VERSION,
            protocol=self.PROTOCOL_ID,
            flags=packet.ACK_MASK,
            sequence_number=self.sequence_number,
            ack=self.receiver.rcv_next,
            options=make_sack_option(
                self.receiver.option_blocks(MAX_BLOCKS))
        )
