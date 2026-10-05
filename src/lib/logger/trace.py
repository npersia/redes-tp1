"""Base de los logs de -v.

Cada protocolo tiene sus propios logs (por ejemplo
stop_and_wait/trace.py): el codigo del protocolo solo llama metodos con nombre
y los textos, contadores y tasas viven en el log. Aca esta lo que todos
comparten: el prefijo, el reloj, los bytes acumulados y el limitador de
progreso. No sabe nada de protocolos.
"""

import time

from lib.logger.logger import logger


MB = 1024 * 1024
PROGRESS_INTERVAL = (
    1.0  
)


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
