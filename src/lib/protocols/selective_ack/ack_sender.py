import time

from lib.protocols.selective_ack.sack_option import covers

DUP_ACKS_THRESHOLD = 3  # Duplicate ACKs needed before retransmitting.


class SentSegment:

    def __init__(self, seq, payload, flags, timeout):
        self.seq = seq
        self.payload = payload
        self.flags = flags
        self.deadline = time.monotonic() + timeout
        self.retries = 0
        self.sacked = False

    # The first sequence number after this segment.
    @property
    def end(self):
        return self.seq + len(self.payload)

    # Restarts the timer and counts one more attempt.
    def refresh(self, timeout):
        self.deadline = time.monotonic() + timeout
        self.retries += 1


class ACKSender:

    def __init__(self, initial_seq, cwnd, timeout):
        # send_base is the cumulative ACK: we have everything up to here.
        self.send_base = initial_seq
        self.next_seq = initial_seq
        self.cwnd = cwnd
        self.timeout = timeout
        # The in-flight segments, always ordered by seq.
        self.window = []
        # How many identical ACKs we got, for the fast retransmit.
        self.dup_acks = 0

    @property
    def in_flight(self):
        return len(self.window)

    @property
    def is_idle(self):
        return not self.window

    def has_room(self):
        return len(self.window) < self.cwnd

    # Registers a segment we just sent, and returns it.
    def add(self, payload, flags):
        segment = SentSegment(self.next_seq, payload, flags, self.timeout)
        self.window.append(segment)
        # A segment with no payload (a FIN for an empty file) advances 1.
        self.next_seq = segment.end if payload else segment.seq + 1
        return segment

    def is_acked(self, segment):
        return segment.end <= self.send_base

    # First unacknowledged, non-SACKed segment: the one to retransmit.
    def hole(self):
        for segment in self.window:
            if segment.sacked or self.is_acked(segment):
                continue
            return segment
        return None

    # Processes an ACK. Returns the segment to retransmit, or None.
    def handle_ack(self, ack, blocks):
        for segment in self.window:
            if covers(blocks, segment.seq, segment.end):
                segment.sacked = True

        if ack > self.send_base:
            # New ACK: move send_base forward and drop the confirmed prefix.
            self.send_base = ack
            self.dup_acks = 0
            self.window = [s for s in self.window if not self.is_acked(s)]
            return None

        if ack < self.send_base:
            # Stale ACK, ignored. It does not count as a duplicate.
            return None

        if not self.window:
            return None

        # Same ACK as before: the receiver is telling us about a hole.
        self.dup_acks += 1
        if self.dup_acks < DUP_ACKS_THRESHOLD:
            return None

        # Reset the counter so we do not resend the same hole again.
        self.dup_acks = 0
        return self.hole()

    # Returns the segment to retransmit if the oldest timer expired.
    def on_timeout(self):
        if not self.window:
            return None

        if self.window[0].deadline > time.monotonic():
            return None

        # The SACK marks went stale, drop them and decide again.
        for segment in self.window:
            segment.sacked = False

        return self.hole()
