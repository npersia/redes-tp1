import socket

from lib.protocols.base_transport import (
    RECV_BUFFER,
    VERSION,
    BaseTransport,
    ConnectionClosed,
    TransferCancelled,
)
import lib.protocols.packet.packet as packet
from lib.protocols.stop_and_wait.trace import RecvTrace, SendTrace

MAX_PAYLOAD_SIZE = 1400


class StopAndWait(BaseTransport):
    PROTOCOL_ID = 1
    TAG = "SW"

    def send(self, data: bytes) -> None:
        if self.is_closed or self.sock is None:
            raise ConnectionClosed(
                "the socket is not initialized or the connection is closed"
            )

        chunks = []

        for i in range(0, len(data), MAX_PAYLOAD_SIZE):
            chunks.append(data[i:i + MAX_PAYLOAD_SIZE])
        if not chunks:
            chunks = [b""]

        trace = SendTrace(
            len(data),
            len(chunks),
            MAX_PAYLOAD_SIZE,
            self.remote_address,
            self.sequence_number,
        )

        for i, chunk in enumerate(chunks):
            if self.cancel_requested.is_set():
                self.notify_abort()
                trace.cancelled()
                raise TransferCancelled(
                    f"Transferencia cancelada por el usuario: {trace.balance}."
                )

            last = i == len(chunks) - 1
            if last:
                flags = packet.FIN_MASK
            else:
                flags = 0
            payload_len = len(chunk)

            pkt = packet.make_packet(
                version=VERSION,
                protocol=self.PROTOCOL_ID,
                flags=flags,
                sequence_number=self.sequence_number,
                payload=chunk,
            )

            if payload_len > 0:
                expected_ack = self.sequence_number + payload_len
            else:
                expected_ack = self.sequence_number + 1

            if last:
                trace.fin(self.sequence_number)

            ret = 0
            ack_received = False

            while not ack_received and ret < self.max_retries:
                try:

                    self.sock.sendto(pkt, self.remote_address)
                    self.sock.settimeout(self.timeout)

                    resp, addr = self.sock.recvfrom(RECV_BUFFER)
                    if (
                        addr != self.remote_address
                        or not packet.is_valid(resp)
                    ):
                        trace.stray(addr, expected_ack)
                        continue

                    flags_byte = bytes([packet.get_header_flags(resp)])

                    if packet.get_flag_ERR(flags_byte):
                        if packet.get_flag_CANCEL(flags_byte):
                            trace.remote_cancel()
                            raise TransferCancelled(
                                "El otro extremo canceló la transferencia: "
                                f"{trace.balance}."
                            )
                        trace.remote_error()
                        raise ConnectionClosed(
                            "El remoto notificó un error con flag ERR."
                        )

                    if not packet.get_flag_ACK(flags_byte):
                        seq = packet.get_header_sequence_paquet(resp)
                        if seq < self.exp_sequence_number:
                            trace.old_data(seq, self.exp_sequence_number)
                            self.send_ack()
                        else:
                            trace.no_ack(flags_byte)
                        continue

                    ack_num = packet.get_header_ack(resp)
                    if ack_num == expected_ack:
                        ack_received = True
                        # Increment the sequence number by n bytes
                        self.sequence_number = expected_ack
                    else:
                        trace.bad_ack(ack_num, expected_ack)
                except socket.timeout:
                    ret += 1
                    trace.retransmit(expected_ack, ret, self.max_retries)
                    if self.cancel_requested.is_set():
                        self.notify_abort()
                        trace.cancelled(retransmitiendo=True)
                        raise TransferCancelled(
                            "Transferencia cancelada por el usuario: "
                            f"{trace.balance}."
                        )

            if not ack_received:
                trace.gave_up(self.sequence_number, self.max_retries)
                raise ConnectionClosed("Connexion lost, many transitions.")

            trace.acked(payload_len)

        trace.done()

    def recv(self) -> bytes:
        if self.is_closed or self.sock is None:
            raise ConnectionClosed(
                "the socket is not initialized or the connection is closed"
            )

        received_buffer = bytearray()
        self.sock.settimeout(self.timeout)

        trace = RecvTrace(
            self.remote_address,
            self.exp_sequence_number,
            self.timeout,
        )

        while True:
            if self.cancel_requested.is_set():
                self.notify_abort()
                trace.cancelled()
                raise TransferCancelled(
                    "Recepcion cancelada por el usuario: "
                    f"{trace.bytes} bytes recibidos."
                )

            try:
                data, addr = self.sock.recvfrom(RECV_BUFFER)
                if addr != self.remote_address or not packet.is_valid(data):
                    trace.stray(addr, addr == self.remote_address)
                    continue

                flags_byte = bytes([packet.get_header_flags(data)])

                if packet.get_flag_ERR(flags_byte):
                    if packet.get_flag_CANCEL(flags_byte):
                        trace.remote_cancel()
                        raise TransferCancelled(
                            f"El otro extremo canceló la transferencia: "
                            f"{trace.bytes} bytes recibidos."
                        )
                    trace.remote_error()
                    raise ConnectionClosed(
                        "Transferencia abortada por error remoto (ERR flag)."
                    )
                if (
                    packet.get_flag_SYN(flags_byte)
                    and packet.get_flag_ACK(flags_byte)
                ):
                    trace.syn_ack_again()
                    self.send_ack()
                    continue

                seq = packet.get_header_sequence_paquet(data)

                if seq == self.exp_sequence_number:
                    payload = packet.get_payload(data)
                    payload_len = len(payload)
                    received_buffer.extend(payload)

                    if payload_len > 0:
                        packet_bytes = payload_len
                    else:
                        packet_bytes = 1

                    self.exp_sequence_number += packet_bytes
                    self.send_ack()
                    trace.stored(payload_len)

                    if packet.get_flag_FIN(flags_byte):
                        trace.done()
                        return bytes(received_buffer)

                elif seq < self.exp_sequence_number:
                    trace.duplicate(seq, self.exp_sequence_number)
                    self.send_ack()

                else:
                    trace.ahead(seq, self.exp_sequence_number)

            except socket.timeout:
                trace.idle()
                continue
            except Exception as e:
                if self.is_closed:
                    trace.closed()
                    raise ConnectionClosed("Conetion closed.")
                raise e

    def send_ack(self) -> None:
        ack_pkt = packet.make_packet(
            version=VERSION,
            protocol=self.PROTOCOL_ID,
            flags=packet.ACK_MASK,
            sequence_number=self.sequence_number,
            ack=self.exp_sequence_number,
        )
        self.sock.sendto(ack_pkt, self.remote_address)
