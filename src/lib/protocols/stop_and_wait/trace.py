"""Los mensajes de -v de Stop & Wait, con todas sus cuentas.

send() y recv() solo llaman metodos de aca. Los porcentajes, las tasas, los
contadores y el armado de los textos viven en este modulo.
"""

import time

from lib.logger.logger import logger
import lib.protocols.packet.packet as packet


MB = 1024 * 1024
PROGRESS_INTERVAL = 1.0 #segundos minimos entre dos logs de progreso, para no inundar la salida


def flag_names(flags_byte: bytes) -> str:
    """Devuelve los flags prendidos como texto legible."""
    prendidos = [
        nombre
        for nombre, esta in (
            ("SYN", packet.get_flag_SYN(flags_byte)),
            ("FIN", packet.get_flag_FIN(flags_byte)),
            ("ERR", packet.get_flag_ERR(flags_byte)),
            ("ACK", packet.get_flag_ACK(flags_byte)),
            ("CANCEL", packet.get_flag_CANCEL(flags_byte)),
        )
        if esta
    ]
    return "+".join(prendidos) if prendidos else "-"


class Trace:
    """Parte comun: el reloj, los bytes acumulados y el limitador de progreso."""

    def __init__(self):
        self.started = time.monotonic()
        self.last_log = self.started
        self.packets = 0
        self.bytes = 0

    @property
    def elapsed(self):
        return time.monotonic() - self.started

    @property
    def rate(self):
        elapsed = self.elapsed
        return self.bytes / elapsed / MB if elapsed > 0 else 0.0

    def due(self):
        """True si ya paso PROGRESS_INTERVAL desde el ultimo log de avance."""
        now = time.monotonic()
        if now - self.last_log < PROGRESS_INTERVAL:
            return False
        self.last_log = now
        return True


class SendTrace(Trace):
    """Narra un send(). `position` es el paquete que se esta mandando ahora."""

    def __init__(self, total_bytes, total_packets, payload_size, remote, first_seq):
        super().__init__()
        self.total_bytes = total_bytes
        self.total_packets = total_packets
        self.retransmissions = 0
        logger.debug(f"[SW] send: {total_bytes} bytes hacia {remote} en {total_packets} paquetes "
                     f"de hasta {payload_size} B (seq inicial {first_seq})")

    @property
    def position(self):
        return f"{self.packets + 1}/{self.total_packets}"

    @property
    def balance(self):
        return f"{self.bytes} de {self.total_bytes} bytes enviados"

    def fin(self, seq):
        logger.debug(f"[SW] enviando el ultimo paquete ({self.position}) con FIN, seq={seq}")

    def stray(self, addr, expected_ack):
        logger.debug(f"[SW] descarto respuesta de {addr} mientras esperaba el ACK {expected_ack}")

    def bad_ack(self, got, expected):
        logger.debug(f"[SW] ACK fuera de lugar: llego {got}, esperaba {expected} (paquete {self.position})")

    def no_ack(self, flags_byte):
        logger.debug(f"[SW] respuesta sin ACK (flags={flag_names(flags_byte)}) en el paquete {self.position}")

    def retransmit(self, expected_ack, intento, tope):
        self.retransmissions += 1
        logger.debug(f"[SW] timeout esperando el ACK {expected_ack} del paquete {self.position}; "
                     f"retransmito ({intento}/{tope})")

    def remote_error(self):
        logger.debug(f"[SW] el remoto respondio ERR en el paquete {self.position}; se aborta con {self.balance}")

    def remote_cancel(self):
        logger.debug(f"[SW] el remoto cancelo a proposito en el paquete {self.position}; "
                     f"se aborta con {self.balance}")

    def gave_up(self, seq, tope):
        logger.debug(f"[SW] se agotaron los {tope} reintentos en el paquete {self.position} (seq={seq}); "
                     f"corto la transferencia con {self.balance}")

    def cancelled(self, retransmitiendo=False):
        donde = "mientras retransmitia el paquete" if retransmitiendo else "en el paquete"
        logger.debug(f"[SW] cancelado por el usuario {donde} {self.position}")

    def acked(self, payload_len):
        """Un paquete confirmado. Suma al total y, cada tanto, imprime el avance."""
        self.packets += 1
        self.bytes += payload_len
        if not self.due():
            return
        extra = f" - {self.retransmissions} retransmisiones" if self.retransmissions else ""
        logger.debug(f"[SW] enviados {self.packets}/{self.total_packets} paquetes "
                     f"({100.0 * self.packets / self.total_packets:.1f}%) - "
                     f"{self.bytes / MB:.1f}/{self.total_bytes / MB:.1f} MB - {self.rate:.2f} MB/s{extra}")

    def done(self):
        logger.debug(f"[SW] send completo: {self.total_bytes} bytes en {self.total_packets} paquetes, "
                     f"{self.elapsed:.2f}s ({self.rate:.2f} MB/s), {self.retransmissions} retransmisiones")


class RecvTrace(Trace):
    """Narra un recv(). No sabe cuanto va a recibir, asi que no hay porcentaje."""

    def __init__(self, remote, expected_seq, timeout):
        super().__init__()
        self.remote = remote
        self.duplicates = 0
        self.idle_timeouts = 0 #timeouts seguidos sin recibir nada del otro extremo
        logger.debug(f"[SW] recv: esperando datos de {remote} "
                     f"(seq esperado {expected_seq}, timeout {timeout}s)")

    def stray(self, addr, mismo_origen):
        motivo = "paquete invalido" if mismo_origen else "origen inesperado"
        logger.debug(f"[SW] descarto datagrama de {addr} ({motivo})")

    def remote_error(self):
        logger.debug(f"[SW] llego ERR del remoto tras {self.bytes} bytes; se aborta")

    def remote_cancel(self):
        logger.debug(f"[SW] el remoto cancelo a proposito tras {self.bytes} bytes; se aborta")

    def syn_ack_again(self):
        logger.debug("[SW] SYN-ACK retransmitido, reenvio el ACK del handshake")

    def duplicate(self, seq, esperado):
        self.duplicates += 1
        logger.debug(f"[SW] paquete duplicado seq={seq} (esperaba {esperado}), reenvio el ACK {esperado}")

    def ahead(self, seq, esperado):
        logger.debug(f"[SW] paquete adelantado seq={seq} (esperaba {esperado}), lo ignoro")

    def idle(self):
        self.idle_timeouts += 1
        logger.debug(f"[SW] timeout #{self.idle_timeouts} sin datos de {self.remote} "
                     f"({self.bytes} bytes recibidos hasta ahora); sigo esperando")

    def closed(self):
        logger.debug(f"[SW] el socket se cerro mientras esperaba datos ({self.bytes} bytes recibidos)")

    def cancelled(self):
        logger.debug(f"[SW] recepcion cancelada por el usuario con {self.bytes} bytes recibidos")

    def stored(self, payload_len):
        """Un paquete aceptado en orden. Suma al total y, cada tanto, imprime el avance."""
        self.packets += 1
        self.bytes += payload_len
        self.idle_timeouts = 0
        if not self.due():
            return
        extra = f" - {self.duplicates} duplicados" if self.duplicates else ""
        logger.debug(f"[SW] recibidos {self.packets} paquetes / {self.bytes / MB:.1f} MB - "
                     f"{self.rate:.2f} MB/s{extra}")

    def done(self):
        logger.debug(f"[SW] FIN recibido: {self.packets} paquetes, {self.bytes} bytes "
                     f"en {self.elapsed:.2f}s, {self.duplicates} duplicados")
