"""Base class for -v logs.

Each protocol has its own logs (for example, stop_and_wait/trace.py):
protocol code only calls named methods, while messages, counters, and rates
are handled by the log. This module contains the shared functionality:
the prefix, timer, accumulated bytes, and progress limiter. It is
protocol-agnostic.
"""

import time

from lib.logger.logger import logger


MB = 1024 * 1024
PROGRESS_INTERVAL = 1.0


class Trace:

    def __init__(self, tag):
        self.tag = tag
        self.started = time.monotonic()
        self.last_log = self.started
        self.packets = 0
        self.bytes = 0

    def log(self, message):
        logger.debug(f"[{self.tag}] {message}")

    @property
    def elapsed(self):
        return time.monotonic() - self.started

    @property
    def rate(self):
        elapsed = self.elapsed
        return self.bytes / elapsed / MB if elapsed > 0 else 0.0

    def due(self):
        now = time.monotonic()
        if now - self.last_log < PROGRESS_INTERVAL:
            return False
        self.last_log = now
        return True
