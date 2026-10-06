import socket
import time

from lib.protocols.base_transport import (
    RECV_BUFFER,
    VERSION,
    BaseTransport,
    ConnectionClosed,
    TransferCancelled,
)
import lib.protocols.packet.packet as packet
from lib.protocols.selective_ack.ack_receiver import ACKReceiver
from lib.protocols.selective_ack.ack_sender import ACKSender
from lib.protocols.selective_ack.sack_option import (
    MAX_BLOCKS,
    make_sack_option,
    parse_sack_option,
)
from lib.protocols.selective_ack.trace import (
    SackRecvTrace,
    SackSendTrace,
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

    # Puts the session in a state ready to send and receive data.
    def _init_peer(self, sequence_number, exp_sequence_number) -> None:
        self.sequence_number = sequence_number
        self.exp_sequence_number = exp_sequence_number
        self.sender = ACKSender(sequence_number, CWND, self.timeout)
        self.receiver = ACKReceiver(exp_sequence_number, RWIND)
        self.sock.settimeout(POLL_INTERVAL)
        self.is_closed = False

    # Send all the data, respecting the window, and wait for ACKs.
    def send(self, data: bytes) -> None:
        if self.is_closed or self.sock is None:
            raise ConnectionClosed(
                "the socket is not initialized or the connection is closed"
            )

        chunks = [
            data[i: i + MAX_PAYLOAD_SIZE]
            for i in range(0, len(data), MAX_PAYLOAD_SIZE)
        ]
        if not chunks:
            chunks = [b""]

        total = len(chunks)
        sent = 0

        self._send_trace = SackSendTrace(
            len(data),
            total,
            MAX_PAYLOAD_SIZE,
            CWND,
            self.remote_address,
            self.sender.send_base,
        )
        # The socket is taken once: a close() from another thread sets
        # self.sock to None, and this one fails with OSError instead.
        self._send_sock = self.sock

        while sent < total or not self.sender.is_idle:
            if self.cancel_requested.is_set():
                self.notify_abort()
                self._send_trace.cancelled()
                raise TransferCancelled(
                    "Transferencia cancelada por el usuario: "
                    f"{self._send_trace.balance}."
                )

            while sent < total and self.sender.has_room():
                flags = packet.FIN_MASK if sent == total - 1 else 0
                segment = self.sender.add(chunks[sent], flags)
                self._sendto(
                    self._send_sock,
                    self._data_packet(segment),
                    self._send_trace,
                )
                self._send_trace.segment_sent()
                sent += 1

            if self.sender.is_idle:
                break

            self._wait_event()

        self._send_trace.done()

    # Wait for an ACK or a timeout, whichever comes first.
    def _wait_event(self) -> None:
        data = None
        try:
            data, addr = self._send_sock.recvfrom(RECV_BUFFER)
        except socket.timeout:
            data = None
        except OSError as error:
            if self.is_closed:
                self._send_trace.closed()
                raise ConnectionClosed("Connection closed.")
            raise error

        if data is not None:
            if addr != self.remote_address or not packet.is_valid(data):
                self._send_trace.stray(addr, addr == self.remote_address)
            else:
                self._handle_ack(data)

        expired = self.sender.on_timeout()
        if expired is not None:
            self._retransmit(expired)

    # Process an incoming ACK: update the sender state, and retransmit when
    # the duplicate ACK threshold was reached.
    def _handle_ack(self, data: bytes) -> None:
        flags = self._flags(data)

        if packet.get_flag_ERR(flags):
            if packet.get_flag_CANCEL(flags):
                self._send_trace.remote_cancel()
                raise TransferCancelled(
                    "El otro extremo canceló la transferencia: "
                    f"{self._send_trace.balance}."
                )
            self._send_trace.remote_error()
            raise ConnectionClosed("Remote reported an error (ERR flag).")

        if not packet.get_flag_ACK(flags):
            return

        ack = packet.get_header_ack(data)
        blocks = parse_sack_option(packet.get_header_options(data))

        target, status = self.sender.handle_ack(ack, blocks)
        self._send_trace.ack(
            status,
            ack,
            self.sender.send_base,
            self.sender.dup_acks,
            blocks,
        )
        if target is not None:
            self._retransmit(target, fast=True)

    # Retransmit a segment due to timeout or fast retransmit.
    def _retransmit(self, segment, fast: bool = False) -> None:
        if segment.retries >= self.max_retries:
            self._send_trace.gave_up(segment.seq, self.max_retries)
            raise ConnectionClosed(f"Too many retries for seq={segment.seq}.")

        self._sendto(
            self._send_sock,
            self._data_packet(segment),
            self._send_trace,
        )
        segment.refresh(self.sender.timeout)
        self._send_trace.retransmit(
            segment.seq, fast, segment.retries, self.max_retries
        )

    # Receive the whole remote stream and reassemble it in order.
    def recv(self) -> bytes:
        if self.is_closed or self.sock is None:
            raise ConnectionClosed(
                "the socket is not initialized or the connection " "is closed"
            )

        received = bytearray()
        # seq and n_bytes of the segment carrying the FIN. It can arrive out of
        # order and get buffered, so remember it and recheck after every
        # segment instead of looking only at the one that just came in.
        fin_end = None

        trace = SackRecvTrace(
            self.remote_address, self.receiver.rcv_next, RWIND
        )

        # If the peer sends nothing valid for this long, it is gone: it is
        # what the sender takes to exhaust its retries on one segment, plus
        # a margin.
        # Every datagram from the peer (even a duplicate) restarts the count.
        # It only starts counting with the first data segment: before that the
        # sender may still be loading a big file into memory.
        silence_limit = self.timeout * (self.max_retries + 2)
        last_heard = time.monotonic()
        data_started = False
        sock = self.sock  # same as in send(): survives a concurrent close()

        while True:
            if self.cancel_requested.is_set():
                self.notify_abort()
                trace.cancelled()
                raise TransferCancelled(
                    "Recepcion cancelada por el usuario: "
                    f"{trace.bytes} bytes recibidos."
                )

            if data_started and time.monotonic() - last_heard > silence_limit:
                trace.silence(silence_limit)
                raise ConnectionClosed(
                    f"The remote sent nothing for {silence_limit:.1f}s."
                )

            try:
                data, addr = sock.recvfrom(RECV_BUFFER)
            except socket.timeout:
                if self.is_closed:
                    trace.closed()
                    raise ConnectionClosed("Connection closed.")
                continue
            except OSError as error:
                if self.is_closed:
                    trace.closed()
                    raise ConnectionClosed("Connection closed.")
                raise error

            if addr != self.remote_address or not packet.is_valid(data):
                trace.stray(addr, addr == self.remote_address)
                continue

            last_heard = time.monotonic()
            flags = self._flags(data)

            if packet.get_flag_ERR(flags):
                if packet.get_flag_CANCEL(flags):
                    trace.remote_cancel()
                    raise TransferCancelled(
                        "El otro extremo canceló la transferencia: "
                        f"{trace.bytes} bytes recibidos."
                    )
                trace.remote_error()
                raise ConnectionClosed(
                    "Transfer aborted by a remote error (ERR flag)."
                )

            # Our ACK was lost, so the peer is resending the SYN-ACK
            if packet.get_flag_SYN(flags) and packet.get_flag_ACK(flags):
                trace.syn_ack_again()
                self._sendto(sock, self._ack_packet(), trace)
                continue

            payload = packet.get_payload(data)
            is_fin = packet.get_flag_FIN(flags)

            if not payload and not is_fin:
                continue  # pure ACK, not a data segment

            data_started = True

            seq = packet.get_header_sequence_paquet(data)
            # A FIN with no payload (empty file) advances 1, same as SW
            n_bytes = len(payload) if payload else 1

            delivered, status = self.receiver.accept(seq, n_bytes, payload)
            received.extend(delivered)
            # notify_abort() (BaseTransport) puts this in the ack of the ERR
            self.exp_sequence_number = self.receiver.rcv_next
            self._sendto(sock, self._ack_packet(), trace)

            if is_fin and fin_end is None:
                fin_end = seq + n_bytes

            trace.segment(
                seq,
                status,
                len(delivered),
                self.receiver.rcv_next,
                self.receiver.blocks(),
            )

            # The FIN only ends the transfer once every byte before it has
            # been delivered: if the FIN segment got buffered, we only find
            # that out here.
            if fin_end is not None and fin_end <= self.receiver.rcv_next:
                trace.done()
                return bytes(received)

    def _sendto(self, sock, data: bytes, trace) -> None:
        """sendto() that turns the error of a closed connection into
        ConnectionClosed."""
        try:
            sock.sendto(data, self.remote_address)
        except OSError:
            if self.is_closed:
                trace.closed()
                raise ConnectionClosed("Connection closed.")
            raise

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
            payload=segment.payload,
        )

    def _ack_packet(self) -> bytes:
        """A cumulative ACK plus the SACK blocks the receiver holds."""
        return packet.make_packet(
            version=VERSION,
            protocol=self.PROTOCOL_ID,
            flags=packet.ACK_MASK,
            sequence_number=self.sequence_number,
            ack=self.receiver.rcv_next,
            options=make_sack_option(self.receiver.option_blocks(MAX_BLOCKS)),
        )
