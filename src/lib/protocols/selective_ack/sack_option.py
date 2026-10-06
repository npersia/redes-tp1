# Encoding of the SACK option (TLV) and helpers over [left, right) blocks.
# It goes in the `options` field of the 12-byte header.
#
# Format, section 3 of RFC 2018:
# https://www.rfc-editor.org/info/rfc2018/#section-3
#
#     +--------+
#     |  type  |  1B  = 0x01
#     +--------+
#     | length |  1B
#     +--------+
#     |  left  |  4B  } up to MAX_BLOCKS (left, right) pairs
#     +--------+      }
#     | right  |  4B  }
#     +--------+
#
# `length` counts the type and the length, so for N blocks it is: 2 + 8 * N.

SACK_TYPE = 0x01
OPTION_HEADER_SIZE = 2
BLOCK_SIZE = 8
MAX_BLOCKS = 4


# Builds the SACK option from a list of (left, right) blocks.
# With no blocks it returns b"" instead of a 2-byte option, so an ACK with
# no holes keeps hlen = 12 like Stop & Wait and we waste no bytes.
def make_sack_option(blocks):
    if not blocks:
        return b""

    truncated = list(blocks)[:MAX_BLOCKS]

    body = bytearray()
    for left, right in truncated:
        body += left.to_bytes(4, "big")
        body += right.to_bytes(4, "big")

    return bytes([SACK_TYPE, OPTION_HEADER_SIZE + len(body)]) + bytes(body)


# Walks the TLV option and returns the blocks in the order they arrived.
def parse_sack_option(options):
    blocks = []
    offset = 0
    total = len(options)

    while offset + OPTION_HEADER_SIZE <= total:
        option_type = options[offset]
        option_len = options[offset + 1]

        if option_len < OPTION_HEADER_SIZE or offset + option_len > total:
            break

        if option_type == SACK_TYPE:
            body = options[offset + OPTION_HEADER_SIZE: offset + option_len]
            for i in range(0, len(body) - BLOCK_SIZE + 1, BLOCK_SIZE):
                left = int.from_bytes(body[i: i + 4], "big")
                right = int.from_bytes(body[i + 4: i + 8], "big")
                if left < right:
                    blocks.append((left, right))

        offset += option_len

    return blocks


# Returns the SACK blocks with [left, right) added and merged.
# Overlapping or touching blocks get merged, so (120,135) + (135,141) ends
# up as a single (120,141). The result comes out sorted.
def add_block(blocks, left, right):
    if left >= right:
        return list(blocks)

    ordered = sorted(list(blocks) + [(left, right)])

    merged = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))

    return merged


# Drops or trims the blocks that ended up below `seq`.
# `seq` is the cumulative ACK: whatever sits below it is already confirmed,
# so there is no point announcing it. A block that starts earlier but ends
# later gets trimmed instead of dropped.
def discard_below(blocks, seq):
    trimmed = []
    for left, right in blocks:
        if right <= seq:
            continue
        trimmed.append((max(left, seq), right))
    return trimmed


# Returns True if [start, end) is fully contained in some block.
def covers(blocks, start, end):
    return any(left <= start and end <= right for left, right in blocks)
