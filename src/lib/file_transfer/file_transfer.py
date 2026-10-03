import os
import time

from lib.logger.logger import logger


def read_file(filepath):
    size = os.path.getsize(filepath)
    logger.debug(f"[archivo] leyendo '{filepath}' ({size} bytes) entero a memoria...")
    started = time.monotonic()
    with open(filepath, "rb") as file:
        content = file.read()
    logger.debug(f"[archivo] leidos {len(content)} bytes en {time.monotonic() - started:.2f}s")
    return content


def write_file(filepath, content):
    directory = os.path.dirname(filepath)
    if directory:
        os.makedirs(directory, exist_ok=True)
    logger.debug(f"[archivo] escribiendo {len(content)} bytes en '{filepath}'...")
    started = time.monotonic()
    with open(filepath, "wb") as file:
        file.write(content)
    logger.debug(f"[archivo] escritos {len(content)} bytes en {time.monotonic() - started:.2f}s")


def send_file(transport, filepath):
    transport.send(read_file(filepath))


def send_request(transport, filename):
    logger.debug(f"[archivo] pidiendo '{filename}' al servidor")
    transport.send(f"DOWNLOAD {filename}".encode("utf-8"))


def send_error(transport, message):
    logger.debug(f"[archivo] avisando error al otro extremo: {message}")
    transport.send(f"ERROR {message}".encode("utf-8"))


def receive_content(transport):
    return transport.recv()


def receive_file(transport, filepath):
    content = receive_content(transport)
    write_file(filepath, content)
    return len(content)

def send_upload(transport, filepath, filename):
    header = f"UPLOAD {filename}\n".encode("utf-8")
    file_bytes = read_file(filepath)
    transport.send(header + file_bytes)
