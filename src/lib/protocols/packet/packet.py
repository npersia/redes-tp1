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
            7: SYN    - initialization / negotiation
            6: FIN    - end of transfer
            5: ERR    - error in packet
            4: ACK    - acknowledge of packet
            3: CAN - cancel of transfer
            2 - 0: reserved for future use

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
x - 20 Bytes (IP header) - 8 Bytes (UDP header) - 12 Bytes (RDT header)
    = x - 40 Bytes
x - 40 Bytes = maximum size of payload that can be sent by the
application using this protocol.
"""

HEADER_SIZE = 12

SYN_MASK = 0b10000000
FIN_MASK = 0b01000000
ERR_MASK = 0b00100000
ACK_MASK = 0b00010000
CANCEL_MASK = 0b00001000


def is_valid(packet: bytes) -> bool:
    """Checks whether the datagram can be parsed as an RDT packet."""
    return (
        len(packet) >= HEADER_SIZE
        and HEADER_SIZE <= get_header_hlen(packet) <= len(packet)
    )


def get_header_version(packet: bytes) -> int:
    """Get the version of the RDT protocol from the packet header."""
    return (packet[0] >> 4) & 0x0F


def get_header_protocol(packet: bytes) -> int:
    """Get the protocol of the RDT protocol from the packet header."""
    return packet[0] & 0x0F


def get_header_flags(packet: bytes) -> int:
    """Get the flags of the RDT protocol from the packet header."""
    return packet[1]


def get_flag_SYN(flags: bytes) -> int:
    """ "Get the flag SYN from the flags of the packet"""
    return (flags[0] >> 7) & 1


def get_flag_FIN(flags: bytes) -> int:
    """ "Get the flag SYN from the flags of the packet"""
    return (flags[0] >> 6) & 1


def get_flag_ERR(flags: bytes) -> int:
    """ "Get the flag ERR from the flags of the packet"""
    return (flags[0] >> 5) & 1


def get_flag_ACK(flags: bytes) -> int:
    return (flags[0] >> 4) & 1


def get_flag_CANCEL(flags: bytes) -> int:
    """Get the flag CANCEL from the flags of the packet."""
    return (flags[0] >> 3) & 1


def flag_names(flags: bytes) -> str:
    """Return the flags that are set as readable text, for the logs."""
    set_flags = [
        name
        for name, is_set in (
            ("SYN", get_flag_SYN(flags)),
            ("FIN", get_flag_FIN(flags)),
            ("ERR", get_flag_ERR(flags)),
            ("ACK", get_flag_ACK(flags)),
            ("CANCEL", get_flag_CANCEL(flags)),
        )
        if is_set
    ]
    return "+".join(set_flags) if set_flags else "-"


def get_header_hlen(packet: bytes) -> int:
    return packet[2]


def get_header_options(packet: bytes) -> bytes:

    hlen = get_header_hlen(packet)

    if hlen > HEADER_SIZE:
        return packet[HEADER_SIZE:hlen]
    return b""  # No options


def get_header_sequence_paquet(packet: bytes) -> int:
    """Get the sequence paquet of the RDT protocol from the packet header."""
    return int.from_bytes(packet[4:8], "big")


def get_header_ack(packet: bytes) -> int:
    """Get the ack of the RDT protocol from the packet header."""
    return int.from_bytes(packet[8:12], "big")


def get_payload(packet: bytes) -> bytes:
    hlen = get_header_hlen(packet)
    return packet[hlen:]


def make_packet(
    version=0,
    protocol=0,
    flags=0,
    sequence_number=0,
    ack=0,
    options=b"",
    payload=b"",
) -> bytes:
    """Build the RDT packet"""
    total_hlen = HEADER_SIZE + len(options)
    # Force both values to use half a byte in the corresponding section
    version_bites = version & 0b00001111
    protocol_bites = protocol & 0b00001111

    # Pack both parameters into a single byte: version in the first 4 bits,
    # protocol in the last 4 bits
    v_p = (version_bites << 4) | protocol_bites

    header_init = bytes(
        [
            v_p,
            flags & 0b11111111,
            total_hlen & 0b11111111,
            0b00000000, 
        ]
    )

    sequence_number_bytes = sequence_number.to_bytes(4, byteorder="big")
    ack_bytes = ack.to_bytes(4, byteorder="big")

    return header_init + sequence_number_bytes + ack_bytes + options + payload
