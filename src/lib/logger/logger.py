import logging


LOGGER_NAME = "redes-tp1"
LOGGER_FORMAT = "%(message)s"

logger = logging.getLogger(LOGGER_NAME)


def configure(verbosity=0):
    level = logging.WARNING if verbosity < 0 else logging.DEBUG if verbosity > 0 else logging.INFO
    logging.basicConfig(level=level, format=LOGGER_FORMAT, force=True)
    logger.setLevel(level)


configure()