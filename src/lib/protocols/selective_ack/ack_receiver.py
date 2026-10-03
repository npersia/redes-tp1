from lib.protocols.selective_ack.sack_option import add_block, discard_below

# What accept() did with a segment, so the caller can report it.
DELIVERED = "delivered"          # it was the next one: its bytes (and maybe more) were delivered
BUFFERED = "buffered"            # it came ahead of a hole: kept until the hole is filled
DUPLICATE = "duplicate"          # we already had it, delivered or buffered
OUT_OF_WINDOW = "out_of_window"  # too far ahead for the receive window: dropped


class ACKReceiver:

    def __init__(self, initial_seq, rwind):
        # rcv_next is the next sequence number we expect, and also what goes
        # in the ack field of the ACK we send.
        self.rcv_next = initial_seq
        self.rwind = rwind
        # seq -> (n_bytes, payload) of the out-of-order segments.
        self.out_of_order = {}

    def _is_duplicate(self, seq, n_bytes):
        return seq + n_bytes <= self.rcv_next

    def _is_out_of_window(self, seq):
        return seq >= self.rcv_next + self.rwind

    # Process a segment and return the bytes that became contiguous, plus what
    # was done with it (one of the constants above).
    # n_bytes is how far it advances the sequence number: the payload bytes,
    # or 1 if the segment only carries a FIN. A FIN always consumes one
    # sequence number, like in TCP, even with no payload.
    def accept(self, seq, n_bytes, payload):
        if self._is_duplicate(seq, n_bytes):
            return b"", DUPLICATE
        if self._is_out_of_window(seq):
            return b"", OUT_OF_WINDOW

        if seq < self.rcv_next:
            # Partial overlap, keep only the part we are missing. n_bytes is
            # trimmed too, because it is what moves rcv_next.
            cut = self.rcv_next - seq
            payload = payload[cut:]
            n_bytes -= cut
            seq = self.rcv_next

        if seq > self.rcv_next:
            # Hole ahead, buffer it until the missing piece arrives.
            if seq in self.out_of_order:
                return b"", DUPLICATE
            self.out_of_order[seq] = (n_bytes, payload)
            return b"", BUFFERED

        delivered = bytearray(payload)
        self.rcv_next = seq + n_bytes

        # Hole filled: deliver the bytes of the segments that were waiting
        # right after it, and then the ones after those.
        while self.rcv_next in self.out_of_order:
            pending_n_bytes, pending_payload = self.out_of_order.pop(
                self.rcv_next)
            delivered.extend(pending_payload)
            self.rcv_next += pending_n_bytes

        return bytes(delivered), DELIVERED

    # The current SACK blocks, sorted and without overlaps.
    def blocks(self):
        blocks = []
        for seq in sorted(self.out_of_order):
            n_bytes, _ = self.out_of_order[seq]
            blocks = add_block(blocks, seq, seq + n_bytes)
        return discard_below(blocks, self.rcv_next)

    # The first `max_blocks` blocks, which is what fits in the SACK TLV
    # (Type-Length-Value).
    def option_blocks(self, max_blocks):
        return self.blocks()[:max_blocks]
