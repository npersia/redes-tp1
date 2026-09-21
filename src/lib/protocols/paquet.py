"""
RDT Packet Header
=================

            0                   1                   2                   3
            0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
            +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
            |VERSION|PROTOCOL|    CHECKSUM                 |  FLAGS         |
            +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
            |          SEQUENCE NUMBER        |          ACK                |
            +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
            |     CWND      |                      Reserved                 |
            +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
            |                              PAYLOAD                          |
            +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

Header size: 12 bytes (96 bits)

Fields:
    VERSION:
        Protocol version.
        4 bits.

    PROTOCOL:
        RDT reliability protocol in use.
        4 bits. (prevee un posible protocolo nuevo, es escalable a futuro).
        Examples:
            STOP_AND_WAIT
            SELECTIVE_ACK

    CHECKSUM:
        Checksum used to detect corrupted RDT packets.
        16 bits.

    FLAGS:
        Control flags.
        8 bits.
        Examples:
            7 6 5 4 3 2 1 0
            7: SYN - initialization / negotiation
            6: FIN - end of transfer
            ...

    SEQUENCE NUMBER:
        Sequence number identifica el numero de paquete, numero rotativo.
        16 bits.

    ACK:
        Sequence number being acknowledged.
        16 bits.

    CWND:
        Current congestion/control window information.
        Representa el numero de paquetes que puede recibir (temas de memoria).
        8 bits.

    PAYLOAD:
        El resto de espacio del paquete son los datos que envia la aplicacion.

Tamaño del paquete: depende el MTT (Maximum Transmission Unit)
ejemplo usando MTT = x Bytes
x - 20 Bytes (IP header) - 8 Bytes (UDP header) - 12 Bytes (RDT header) = x - 40 Bytes
x - 40 Bytes = tamaño maximo de payload que puede enviar la app usadando este protocolo.
"""

# Estas funciones se basan en utilizar funciones de bytes
# Como AND, SHIFT, etc. Aprovechando que estamos trabajando con bytes.

HEADER_SIZE = 12

def get_header_version(packet: bytes) -> int:
    """Get the version of the RDT protocol from the packet header."""
    # toma el primer byte, hace un corrimiento a la derecha de 4 bits
    # y luego hace un AND con 0x0F para obtener los 4 bits más significativos.
    return (packet[0] >> 4) & 0x0F


def get_header_protocol(packet: bytes) -> int:
    """Get the protocol of the RDT protocol from the packet header."""
    # toma el primer byte luego hace un AND con 0x0F para obtener los 4 bits más significativos.
    return packet[0] & 0x0F


def get_header_checksum(packet: bytes) -> int:
    """Get the checksum of the RDT protocol from the packet header."""
    return int.from_bytes(packet[1:3], "big")


def get_header_flags(packet: bytes) -> int:
    """Get the flags of the RDT protocol from the packet header."""
    # Devuelve todos los bytes correspondientes a los flags, luego con
    # operaciones de and se puede obtener cada flag
    return packet[3]


SYN_MASK = 0b10000000
FIN_MASK = 0b01000000


def get_flag_SYN(flags: bytes) -> int:
    """"Get the flag SYN from the flags of the packet"""
    # x000 0000 ---> x y le hago el and con 1
    return (flags[0] >> 7) & 1


def get_flag_FIN(flags: bytes) -> int:
    """"Get the flag SYN from the flags of the packet"""
    # 0x00 0000 ---> x y le hago el and con 1
    # haciendo con mascara return (flags[0] & 0b10000000)
    return (flags[0] >> 6) & 1


def get_header_sequence_paquet(packet: bytes) -> int:
    """Get the sequence paquet of the RDT protocol from the packet header."""
    return int.from_bytes(packet[4:6], "big")


def get_header_ack(packet: bytes) -> int:
    """Get the ack of the RDT protocol from the packet header."""
    return int.from_bytes(packet[6:8], "big")


def get_header_cwind(packet: bytes) -> int:
    """Get the cwind of the RDT protocol from the packet header."""
    return packet[8]


def get_paquet_payload(packet: bytes) -> bytes:
    """"Get the payload of the RDT protocol from the packet"""
    if len(packet) < HEADER_SIZE:
        raise ValueError("Paquete menor al tamaño del header")
    return packet[HEADER_SIZE:]
