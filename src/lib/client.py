import os

from lib.file_transfer.file_transfer import receive_content, send_file, send_request, write_file
from lib.logger.logger import configure, logger
from lib.protocols.factory import TransportFactory


def create_transport(arguments):
    return TransportFactory.get_transport(
        arguments.protocol,
        arguments.host,
        arguments.port,
    )


def upload(arguments):
    configure(arguments.verbosity)
    if not os.path.exists(arguments.src):
        logger.error(f"[Cliente] El archivo '{arguments.src}' no existe.")
        return

    transport = create_transport(arguments)
    try:
        transport.connect()
        logger.info(f"[Cliente] Transmitiendo '{arguments.src}'...")
        send_file(transport, arguments.src)
        logger.info("[Cliente] Transferencia completada.")
    finally:
        transport.close()


def download(arguments):
    configure(arguments.verbosity)
    transport = create_transport(arguments)
    try:
        transport.connect()
        send_request(transport, arguments.name or os.path.basename(arguments.dst))
        logger.info("[Cliente] Esperando archivo del servidor...")
        received_content = receive_content(transport)
        if received_content.startswith(b"ERROR "):
            logger.error(f"[Cliente] {received_content[6:].decode('utf-8')}")
            return
        write_file(arguments.dst, received_content)
        received_bytes = len(received_content)
        logger.info(f"[Cliente] Archivo descargado ({received_bytes} bytes).")
    finally:
        transport.close()