"""Los mensajes de -v de Selective ACK, con todas sus cuentas.

send() y recv() solo llaman metodos de aca. A diferencia de Stop & Wait hay
varios segmentos en vuelo, asi que el avance se mide por los bytes que el
otro extremo ya confirmo (send_base) y no por el paquete que se esta mandando.
"""

from lib.logger.trace import MB, Trace
from lib.protocols.selective_ack import ack_receiver, ack_sender


TAG = "SACK"


class SackSendTrace(Trace):
    """Narra un send(). Los segmentos sueltos no se loguean: solo el avance."""

    def __init__(self, total_bytes, total_segments, payload_size, cwnd, remote, first_seq):
        super().__init__(TAG)
        self.total_bytes = total_bytes
        self.total_segments = total_segments
        self.first_seq = first_seq
        self.timeouts = 0
        self.fast_retransmits = 0
        self.stale_acks = 0
        self.dup_acks = 0
        self.log(f"send: {total_bytes} bytes hacia {remote} en {total_segments} segmentos "
                 f"de hasta {payload_size} B, ventana de {cwnd} (seq inicial {first_seq})")

    @property
    def balance(self):
        return f"{self.bytes} de {self.total_bytes} bytes confirmados"

    @property
    def retransmissions(self):
        return self.timeouts + self.fast_retransmits

    def segment_sent(self):
        self.packets += 1

    def ack(self, status, ack, send_base, dup_acks, blocks):
        """Un ACK, segun lo que hizo el emisor con el."""
        if status == ack_sender.NEW_ACK:
            self._progress(send_base, blocks)
        elif status == ack_sender.STALE_ACK:
            self.stale_acks += 1
            self.log(f"ACK viejo ack={ack} (ya confirmado hasta {send_base}), lo ignoro")
        elif status == ack_sender.DUP_ACK:
            self.dup_acks += 1
            self.log(f"ACK duplicado ack={ack} ({dup_acks}/{ack_sender.DUP_ACKS_THRESHOLD} "
                     f"para el fast retransmit); SACK {blocks}")
        # FAST_RETRANSMIT lo cuenta retransmit(), que es donde se reenvia

    def _progress(self, send_base, blocks):
        """Actualiza lo confirmado y, cada tanto, imprime el avance."""
        # el FIN de un archivo vacio ocupa 1 de seq sin ser un byte de datos
        self.bytes = min(send_base - self.first_seq, self.total_bytes)
        if not self.due():
            return
        porcentaje = 100.0 * self.bytes / self.total_bytes if self.total_bytes else 100.0
        sack = f" - SACK {blocks}" if blocks else ""
        extra = f" - {self.retransmissions} retransmisiones" if self.retransmissions else ""
        self.log(f"confirmados {self.bytes / MB:.1f}/{self.total_bytes / MB:.1f} MB ({porcentaje:.1f}%) - "
                 f"{self.packets}/{self.total_segments} segmentos enviados - {self.rate:.2f} MB/s{sack}{extra}")

    def retransmit(self, seq, fast, intento, tope):
        if fast:
            self.fast_retransmits += 1
            motivo = "fast retransmit (ACKs duplicados)"
        else:
            self.timeouts += 1
            motivo = "timeout"
        self.log(f"{motivo}: retransmito seq={seq} ({intento}/{tope})")

    def stray(self, addr, mismo_origen):
        motivo = "paquete invalido" if mismo_origen else "origen inesperado"
        self.log(f"descarto datagrama de {addr} mientras esperaba ACKs ({motivo})")

    def remote_error(self):
        self.log(f"el remoto respondio ERR; se aborta con {self.balance}")

    def remote_cancel(self):
        self.log(f"el remoto cancelo a proposito; se aborta con {self.balance}")

    def cancelled(self):
        self.log(f"cancelado por el usuario con {self.balance}")

    def gave_up(self, seq, tope):
        self.log(f"se agotaron los {tope} reintentos en seq={seq}; corto la transferencia con {self.balance}")

    def closed(self):
        self.log(f"el socket se cerro mientras esperaba ACKs ({self.balance})")

    def done(self):
        self.log(f"send completo: {self.total_bytes} bytes en {self.total_segments} segmentos, "
                 f"{self.elapsed:.2f}s ({self.rate:.2f} MB/s), {self.timeouts} retransmisiones por timeout "
                 f"y {self.fast_retransmits} por fast retransmit, {self.dup_acks} ACKs duplicados "
                 f"y {self.stale_acks} viejos")


class SackRecvTrace(Trace):
    """Narra un recv(). No sabe cuanto va a recibir, asi que no hay porcentaje."""

    def __init__(self, remote, expected_seq, rwind):
        super().__init__(TAG)
        self.remote = remote
        self.buffered = 0 #llegaron adelantados y esperaron en el buffer
        self.duplicates = 0
        self.out_of_window = 0 #se descartaron por caer mas alla de la ventana
        self.log(f"recv: esperando datos de {remote} (seq esperado {expected_seq}, "
                 f"ventana de recepcion {rwind} B)")

    def stray(self, addr, mismo_origen):
        motivo = "paquete invalido" if mismo_origen else "origen inesperado"
        self.log(f"descarto datagrama de {addr} ({motivo})")

    def silence(self, limite):
        self.log(f"{self.remote} no mando nada valido en {limite:.1f}s; "
                 f"se abandona con {self.bytes} bytes recibidos")

    def remote_error(self):
        self.log(f"llego ERR del remoto tras {self.bytes} bytes; se aborta")

    def remote_cancel(self):
        self.log(f"el remoto cancelo a proposito tras {self.bytes} bytes; se aborta")

    def cancelled(self):
        self.log(f"recepcion cancelada por el usuario con {self.bytes} bytes recibidos")

    def syn_ack_again(self):
        self.log("SYN-ACK retransmitido, reenvio el ACK del handshake")

    def closed(self):
        self.log(f"el socket se cerro mientras esperaba datos ({self.bytes} bytes recibidos)")

    def segment(self, seq, status, delivered, rcv_next, blocks):
        """Un segmento de datos, segun lo que hizo el receptor con el."""
        self.packets += 1
        if status == ack_receiver.BUFFERED:
            self.buffered += 1
            self.log(f"seq={seq} adelantado (esperaba {rcv_next}): queda en el buffer; SACK {blocks}")
            return
        if status == ack_receiver.DUPLICATE:
            self.duplicates += 1
            self.log(f"seq={seq} duplicado (esperaba {rcv_next}), reenvio el ACK")
            return
        if status == ack_receiver.OUT_OF_WINDOW:
            self.out_of_window += 1
            self.log(f"seq={seq} fuera de la ventana de recepcion (esperaba {rcv_next}), lo descarto")
            return

        self.bytes += delivered
        if not self.due():
            return
        self.log(f"recibidos {self.packets} segmentos / {self.bytes / MB:.1f} MB entregados - "
                 f"{self.rate:.2f} MB/s{self._anomalies()}")

    def _anomalies(self):
        partes = [f"{n} {nombre}" for n, nombre in (
            (self.buffered, "adelantados"),
            (self.duplicates, "duplicados"),
            (self.out_of_window, "fuera de ventana"),
        ) if n]
        return " - " + ", ".join(partes) if partes else ""

    def done(self):
        self.log(f"FIN recibido: {self.packets} segmentos, {self.bytes} bytes en {self.elapsed:.2f}s, "
                 f"{self.buffered} adelantados, {self.duplicates} duplicados y "
                 f"{self.out_of_window} fuera de ventana")
