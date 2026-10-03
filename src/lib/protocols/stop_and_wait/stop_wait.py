import socket

from lib.protocols.base_transport import (
    RECV_BUFFER,
    VERSION,
    BaseTransport,
    ConnectionClosed,
)
import lib.protocols.packet.packet as packet


# CONSTANTES
MAX_PAYLOAD_SIZE = 1400


# OJO QUE CAMBIO LA CABECERA PORQUE ES UDP, NECESITO SABER EL REMOTE ADDRESS PARA TRABAJAR. TCP ME LO DABA
class StopAndWait(BaseTransport):
    PROTOCOL_ID = 1

    ###################################################################################################################
    # ACA TERMINA EL HANDSHAKE, YA ESTA EN BASE_TRANSPORT
    ###################################################################################################################

    # SW no sobreescribe _init_peer: no necesita estado extra, con los numeros
    # de secuencia y la sesion abierta alcanza.

    ###################################################################################################################
    # ACA EMPIEZA LA TRANSFERENCIA
    ###################################################################################################################

    def send(self, data: bytes) -> None:
        if self.is_closed or self.sock is None:
            raise ConnectionClosed("the socket is not initialized or the connection is closed")

        chunks = []

        for i in range(0, len(data), MAX_PAYLOAD_SIZE):
            chunks.append(data[i:i + MAX_PAYLOAD_SIZE])
        if not chunks:
            chunks = [b""]

        for i, chunk in enumerate(chunks):
            last = (i == len(chunks) - 1)
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
                payload=chunk
            )

            if payload_len > 0:
                expected_ack = self.sequence_number + payload_len
            else:
                expected_ack = self.sequence_number + 1

            ret = 0
            ack_received = False

            while not ack_received and ret < self.max_retries:
                try:

                    self.sock.sendto(pkt, self.remote_address)
                    self.sock.settimeout(self.timeout)

                    resp, addr = self.sock.recvfrom(RECV_BUFFER)
                    if addr != self.remote_address:
                        continue

                    flags_byte = bytes([packet.get_header_flags(resp)])

                    if packet.get_flag_ERR(flags_byte):
                        raise ConnectionClosed("El remoto notificó un error con flag ERR.")

                    if packet.get_flag_ACK(flags_byte):
                        ack_num = packet.get_header_ack(resp)
                        if ack_num == expected_ack:
                            ack_received = True
                            self.sequence_number = expected_ack  # se incrementa el seq number en n bytes
                except socket.timeout:
                    ret += 1

            if not ack_received:
                raise ConnectionClosed("Connexion lost, many transitions.")



    def recv(self) -> bytes:
        if self.is_closed or self.sock is None:
            raise ConnectionClosed("the socket is not initialized or the connection is closed")

        received_buffer = bytearray()
        self.sock.settimeout(self.timeout)

        while True:
            try:
                data, addr = self.sock.recvfrom(RECV_BUFFER)
                if addr != self.remote_address:
                    continue

                flags_byte = bytes([packet.get_header_flags(data)])

                if packet.get_flag_ERR(flags_byte):
                    raise ConnectionClosed("Transferencia abortada por error remoto (ERR flag).")

                # Si retransmiten el SYN-ACK del handshake
                if packet.get_flag_SYN(flags_byte) and packet.get_flag_ACK(flags_byte):
                    ack_pkt = packet.make_packet(
                        version=VERSION,
                        protocol=self.PROTOCOL_ID,
                        flags=packet.ACK_MASK,
                        sequence_number=self.sequence_number,
                        ack=self.exp_sequence_number #es el seq number que se esperaria
                    )
                    self.sock.sendto(ack_pkt, self.remote_address)
                    continue

                seq = packet.get_header_sequence_paquet(data)

                # Llegó el paquete esperado
                if seq == self.exp_sequence_number:
                    payload = packet.get_payload(data)
                    payload_len = len(payload)
                    received_buffer.extend(payload)

                    # Avanzamos el apuntador de recepción la cantidad de bytes recibidos
                    if payload_len > 0:
                        packet_bytes = payload_len
                    else:
                        packet_bytes = 1

                    self.exp_sequence_number += packet_bytes

                    ack_pkt = packet.make_packet(
                        version=VERSION,
                        protocol=self.PROTOCOL_ID,
                        flags=packet.ACK_MASK,
                        sequence_number=self.sequence_number,
                        ack=self.exp_sequence_number
                    )
                    self.sock.sendto(ack_pkt, self.remote_address)

                    if packet.get_flag_FIN(flags_byte):
                        return bytes(received_buffer)

                # el paquete esta duplicado o vencido
                elif seq < self.exp_sequence_number:
                    ack_pkt = packet.make_packet(
                        version=VERSION,
                        protocol=self.PROTOCOL_ID,
                        flags=packet.ACK_MASK,
                        sequence_number=self.sequence_number,
                        ack=self.exp_sequence_number
                    )
                    self.sock.sendto(ack_pkt, self.remote_address)

            except socket.timeout:
                continue
            except Exception as e:
                if self.is_closed:
                    raise ConnectionClosed("Conetion closed.")
                raise e

