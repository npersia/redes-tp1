import os
import time
from typing import NamedTuple

from lib.logger.logger import logger

UPLOAD_PREFIX = b"UPLOAD "
ERROR_PREFIX = b"ERROR "
OK = b"OK"


class UploadRequest(NamedTuple):
    filename: str
    size: int


class InvalidMessage(Exception):
    """El mensaje recibido no respeta el formato esperado."""


class UploadRejected(Exception):
    """El servidor no acepto el upload; el mensaje es el motivo que mando."""


def read_file(filepath):
    size = os.path.getsize(filepath)
    logger.debug(
        f"[archivo] leyendo '{filepath}' ({size} bytes) "
        f"entero a memoria..."
    )
    started = time.monotonic()
    with open(filepath, "rb") as file:
        content = file.read()
    logger.debug(
        f"[archivo] leidos {len(content)} bytes en "
        f"{time.monotonic() - started:.2f}s"
    )
    return content


def write_file(filepath, content):
    directory = os.path.dirname(filepath)
    if directory:
        os.makedirs(directory, exist_ok=True)
    logger.debug(
        f"[archivo] escribiendo {len(content)} bytes en "
        f"'{filepath}'..."
    )
    started = time.monotonic()
    with open(filepath, "wb") as file:
        file.write(content)
    logger.debug(
        f"[archivo] escritos {len(content)} bytes en "
        f"{time.monotonic() - started:.2f}s"
    )


def send_file(transport, filepath):
    transport.send(read_file(filepath))


def send_request(transport, filename):
    logger.debug(f"[archivo] pidiendo '{filename}' al servidor")
    transport.send(f"DOWNLOAD {filename}".encode("utf-8"))


def send_error(transport, message):
    logger.debug(f"[archivo] avisando error al otro extremo: {message}")
    transport.send(ERROR_PREFIX + message.encode("utf-8"))


def send_ok(transport):
    transport.send(OK)


def receive_content(transport):
    return transport.recv()


def receive_file(transport, filepath):
    content = receive_content(transport)
    write_file(filepath, content)
    return len(content)


def request_upload(transport, filename, size):
    """Pide subir el archivo: 'UPLOAD <tamanio> <nombre>'.

    Retorna si el servidor contesta OK; si no, levanta UploadRejected.
    """
    logger.debug(f"[archivo] pidiendo subir '{filename}' ({size} bytes)")
    transport.send(UPLOAD_PREFIX + f"{size} {filename}".encode("utf-8"))
    response = receive_content(transport)
    if response == OK:
        return
    if response.startswith(ERROR_PREFIX):
        raise UploadRejected(
            response[len(ERROR_PREFIX):].decode("utf-8", errors="replace")
        )
    raise UploadRejected("Respuesta inesperada del servidor.")


def is_upload_request(content):
    return content.startswith(UPLOAD_PREFIX)


def parse_upload_request(content):
    """'UPLOAD <tamanio> <nombre>' -> UploadRequest; si no, InvalidMessage."""
    try:
        size_text, filename = (
            content[len(UPLOAD_PREFIX):].decode("utf-8").split(" ", 1)
        )
    except (UnicodeDecodeError, ValueError):
        raise InvalidMessage("Formato de UPLOAD inválido.")
    if not (size_text.isascii() and size_text.isdigit()) or not filename:
        raise InvalidMessage("Formato de UPLOAD inválido.")
    return UploadRequest(filename=filename, size=int(size_text))
