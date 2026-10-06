"""Stop & Wait -v messages, including all related calculations.

send() and recv() only call methods from this module. Percentages, rates,
counters, and message formatting are handled here.
"""

from lib.logger.trace import MB, Trace
from lib.protocols.packet.packet import flag_names


TAG = "SW"


class SendTrace(Trace):
    """Describes a send() operation.
      `position` is the packet currently being sent."""

    def __init__(
        self,
        total_bytes,
        total_packets,
        payload_size,
        remote,
        first_seq,
    ):
        super().__init__(TAG)
        self.total_bytes = total_bytes
        self.total_packets = total_packets
        self.retransmissions = 0
        self.log(
            f"send: {total_bytes} bytes hacia {remote} en "
            f"{total_packets} paquetes de hasta {payload_size} B "
            f"(seq inicial {first_seq})"
        )

    @property
    def position(self):
        return f"{self.packets + 1}/{self.total_packets}"

    @property
    def balance(self):
        return f"{self.bytes} de {self.total_bytes} bytes enviados"

    def fin(self, seq):
        self.log(
            f"enviando el ultimo paquete ({self.position}) con FIN, "
            f"seq={seq}"
        )

    def stray(self, addr, expected_ack):
        self.log(
            f"descarto respuesta de {addr} mientras esperaba el "
            f"ACK {expected_ack}"
        )

    def bad_ack(self, got, expected):
        self.log(
            f"ACK fuera de lugar: llego {got}, esperaba {expected} "
            f"(paquete {self.position})"
        )

    def no_ack(self, flags_byte):
        self.log(
            f"respuesta sin ACK (flags={flag_names(flags_byte)}) "
            f"en el paquete {self.position}"
        )

    def old_data(self, seq, exp_seq):
        self.log(
            f"llego un dato viejo (seq={seq}, esperado={exp_seq}): "
            f"el otro no vio mi ACK, se lo reenvio"
        )

    def retransmit(self, expected_ack, intento, tope):
        self.retransmissions += 1
        self.log(
            f"timeout esperando el ACK {expected_ack} del paquete "
            f"{self.position}; retransmito ({intento}/{tope})"
        )

    def remote_error(self):
        self.log(
            f"el remoto respondio ERR en el paquete {self.position}; "
            f"se aborta con {self.balance}"
        )

    def remote_cancel(self):
        self.log(
            f"el remoto cancelo a proposito en el paquete {self.position}; "
            f"se aborta con {self.balance}"
        )

    def gave_up(self, seq, tope):
        self.log(
            f"se agotaron los {tope} reintentos en el paquete "
            f"{self.position} (seq={seq}); corto la transferencia "
            f"con {self.balance}"
        )

    def cancelled(self, retransmitiendo=False):
        donde = (
            "mientras retransmitia el paquete"
            if retransmitiendo
            else "en el paquete"
        )
        self.log(f"cancelado por el usuario {donde} {self.position}")

    def acked(self, payload_len):
        """Acknowledged packet.
          Adds to the total and periodically prints progress."""
        self.packets += 1
        self.bytes += payload_len
        if not self.due():
            return
        extra = (
            f" - {self.retransmissions} retransmisiones"
            if self.retransmissions
            else ""
        )
        self.log(
            f"enviados {self.packets}/{self.total_packets} paquetes "
            f"({100.0 * self.packets / self.total_packets:.1f}%) - "
            f"{self.bytes / MB:.1f}/{self.total_bytes / MB:.1f} MB - "
            f"{self.rate:.2f} MB/s{extra}"
        )

    def done(self):
        self.log(
            f"send completo: {self.total_bytes} bytes en "
            f"{self.total_packets} paquetes, "
            f"{self.elapsed:.2f}s ({self.rate:.2f} MB/s), "
            f"{self.retransmissions} retransmisiones"
        )


class RecvTrace(Trace):
    """Describes a recv() operation. The amount to be received is unknown,
    so there is no percentage.
    """

    def __init__(self, remote, expected_seq, timeout):
        super().__init__(TAG)
        self.remote = remote
        self.duplicates = 0
        self.idle_timeouts = 0
        self.log(
            f"recv: esperando datos de {remote} "
            f"(seq esperado {expected_seq}, timeout {timeout}s)"
        )

    def stray(self, addr, mismo_origen):
        motivo = "paquete invalido" if mismo_origen else "origen inesperado"
        self.log(f"descarto datagrama de {addr} ({motivo})")

    def remote_error(self):
        self.log(f"llego ERR del remoto tras {self.bytes} bytes; se aborta")

    def remote_cancel(self):
        self.log(
            f"el remoto cancelo a proposito tras {self.bytes} bytes; "
            f"se aborta"
        )

    def syn_ack_again(self):
        self.log("SYN-ACK retransmitido, reenvio el ACK del handshake")

    def duplicate(self, seq, esperado):
        self.duplicates += 1
        self.log(
            f"paquete duplicado seq={seq} (esperaba {esperado}), "
            f"reenvio el ACK {esperado}"
        )

    def ahead(self, seq, esperado):
        self.log(
            f"paquete adelantado seq={seq} (esperaba {esperado}), "
            f"lo ignoro"
        )

    def idle(self):
        self.idle_timeouts += 1
        self.log(
            f"timeout #{self.idle_timeouts} sin datos de {self.remote} "
            f"({self.bytes} bytes recibidos hasta ahora); sigo esperando"
        )

    def closed(self):
        self.log(
            f"el socket se cerro mientras esperaba datos "
            f"({self.bytes} bytes recibidos)"
        )

    def cancelled(self):
        self.log(
            f"recepcion cancelada por el usuario con {self.bytes} "
            f"bytes recibidos"
        )

    def stored(self, payload_len):
        """In-order packet.
          Adds to the total and periodically prints progress."""
        self.packets += 1
        self.bytes += payload_len
        self.idle_timeouts = 0
        if not self.due():
            return
        extra = f" - {self.duplicates} duplicados" if self.duplicates else ""
        self.log(
            f"recibidos {self.packets} paquetes / {self.bytes / MB:.1f} MB - "
            f"{self.rate:.2f} MB/s{extra}"
        )

    def done(self):
        self.log(
            f"FIN recibido: {self.packets} paquetes, {self.bytes} bytes "
            f"en {self.elapsed:.2f}s, {self.duplicates} duplicados"
        )
