"""
RDT Packet Header
=================

            0                   1                   2                   3
            0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
            +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
            |VERSION|PROTOCOL|  FLAGS     | hlen(bytes)    |  RESERVED      |
            +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
            |                   Sequence number                             |
            +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
            |                           ACK                                 |
            +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
            |                            OPTIONS                            |
            +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
            |                              PAYLOAD                          |
            +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

Minimun Header size: 12 bytes (96 bits)

Fields:
    VERSION:
        Protocol version.
        4 bits.

    PROTOCOL:
        RDT reliability protocol in use.
        4 bits.
        Examples:
            STOP_AND_WAIT
            SELECTIVE_ACK

    FLAGS:
        Control flags.
        8 bits.
        Examples:
            7 6 5 4 3 2 1 0
            7: SYN - initialization / negotiation
            6: FIN - end of transfer
            5: ERR - error in packet
            4 - 0: reserved for future use

    HLEN:
        Header length (in bytes).
        8 bits.
           
    SEQUENCE NUMBER:
        Sequence number indicates the order of the packet in the stream.
        32 bits.

    ACK:
        Sequence number being acknowledged.
        32 bits.
    
    OPTIONS:
        SACK - [x,y]

    PAYLOAD:
        The data being transmitted (application data).

Size of packet MTT (Maximum Transmission Unit)
example using MTT = x Bytes
x - 20 Bytes (IP header) - 8 Bytes (UDP header) - 12 Bytes (RDT header) = x - 40 Bytes
x - 40 Bytes = maximum size of payload that can be sent by the application using this protocol.
"""

# Estas funciones se basan en utilizar funciones de bytes
# Como AND, SHIFT, etc. Aprovechando que estamos trabajando con bytes.

SYN_MASK = 0b10000000
FIN_MASK = 0b01000000
ERR_MASK = 0b00100000


def get_header_version(packet: bytes) -> int:
    """Get the version of the RDT protocol from the packet header."""
    # toma el primer byte, hace un corrimiento a la derecha de 4 bits
    # y luego hace un AND con 0x0F para obtener los 4 bits más significativos.
    return (packet[0] >> 4) & 0x0F


def get_header_protocol(packet: bytes) -> int:
    """Get the protocol of the RDT protocol from the packet header."""
    # toma el primer byte luego hace un AND con 0x0F para obtener los 4 bits más significativos.
    return packet[0] & 0x0F


def get_header_flags(packet: bytes) -> int:
    """Get the flags of the RDT protocol from the packet header."""
    # Devuelve todos los bytes correspondientes a los flags, luego con
    # operaciones de and se puede obtener cada flag
    return packet[1]


def get_flag_SYN(flags: bytes) -> int:
    """"Get the flag SYN from the flags of the packet"""
    # x000 0000 ---> x y le hago el and con 1
    return (flags[0] >> 7) & 1


def get_flag_FIN(flags: bytes) -> int:
    """"Get the flag SYN from the flags of the packet"""
    # 0x00 0000 ---> x y le hago el and con 1
    # haciendo con mascara return (flags[0] & FIN_MASK)
    return (flags[0] >> 6) & 1


def get_flag_ERR(flags: bytes) -> int:
    """"Get the flag ERR from the flags of the packet"""
    # 0x00 0000 ---> x y le hago el and con 1
    # haciendo con mascara return (flags[0] & ERR_MASK)
    return (flags[0] >> 5) & 1


def get_header_hlen(packet: bytes) -> int:
    return packet[2]


def get_header_options(packet: bytes) -> bytes:

    hlen = get_header_hlen(packet)

    if hlen > 12:
        return packet[12:hlen]
    return b"" #No options


def get_header_sequence_paquet(packet: bytes) -> int:
    """Get the sequence paquet of the RDT protocol from the packet header."""
    return int.from_bytes(packet[4:8], "big")


def get_header_ack(packet: bytes) -> int:
    """Get the ack of the RDT protocol from the packet header."""
    return int.from_bytes(packet[8:12], "big")


def get_payload(packet: bytes) -> bytes:
    hlen = get_header_hlen(packet)
    return packet[hlen:]
