import os
import select
import sys
import threading

from lib.file_transfer.file_transfer import (UploadRejected, receive_content, request_upload, send_file,
                                             send_request, write_file)
from lib.logger.logger import configure, logger
from lib.protocols.base_transport import ConnectionClosed, TransferCancelled
from lib.protocols.factory import TransportFactory


def create_transport(arguments):
    logger.debug(f"[Cliente] protocolo={arguments.protocol} destino={arguments.host}:{arguments.port}")
    return TransportFactory.get_transport(
        arguments.protocol,
        arguments.host,
        arguments.port,
    )


#cada cuanto el vigilante se despierta a mirar si la transferencia ya termino
POLL_INTERVAL = 0.2


def watch_for_enter(transport, finished):
    """
    Arranca el hilo que espera un Enter para abortar la transferencia en curso.
    """
    def wait_for_enter():
        if sys.stdin is None or sys.stdin.closed:
            return #sin consola no hay nada que vigilar

        while not finished.is_set():
            try:
                listo, _, _ = select.select([sys.stdin], [], [], POLL_INTERVAL)
            except (OSError, ValueError):
                return #stdin no es seleccionable (cerrado o redirigido a algo raro)

            if not listo:
                continue

            if sys.stdin.readline() == "":
                return #EOF: stdin no es una consola, no va a llegar ningun Enter

            if finished.is_set():
                return #la transferencia ya habia terminado, el Enter llego tarde

            logger.info("[Cliente] Cancelando la transferencia...")
            transport.cancel()
            return

    thread = threading.Thread(target=wait_for_enter, daemon=True)
    thread.start()
    return thread


def upload(arguments):
    configure(arguments.verbosity)
    if not os.path.exists(arguments.src):
        logger.error(f"[Cliente] El archivo '{arguments.src}' no existe.")
        return

    transport = create_transport(arguments)
    finished = threading.Event()
    watcher = watch_for_enter(transport, finished)
    try:
        transport.connect()
        logger.info(f"[Cliente] Transmitiendo '{arguments.src}'... (Enter para cancelar)")
        filename = arguments.name or os.path.basename(arguments.src)
        request_upload(transport, filename, os.path.getsize(arguments.src))
        send_file(transport, arguments.src)
        logger.info("[Cliente] Transferencia completada.")
    except (TransferCancelled, UploadRejected) as error:
        logger.error(f"[Cliente] {error}")
    except ConnectionClosed:
        logger.error("[Cliente] El servidor cerró la conexión. La transferencia fue cancelada.")
    finally:
        finished.set()
        watcher.join(timeout=2 * POLL_INTERVAL)
        transport.close()


def download(arguments):
    configure(arguments.verbosity)
    transport = create_transport(arguments)
    finished = threading.Event()
    watcher = watch_for_enter(transport, finished)
    try:
        transport.connect()
        send_request(transport, arguments.name or os.path.basename(arguments.dst))
        logger.info("[Cliente] Esperando archivo del servidor... (Enter para cancelar)")
        received_content = receive_content(transport)
        if received_content.startswith(b"ERROR "):
            logger.error(f"[Cliente] {received_content[6:].decode('utf-8')}")
            return
        write_file(arguments.dst, received_content)
        received_bytes = len(received_content)
        logger.info(f"[Cliente] Archivo descargado ({received_bytes} bytes).")
    except TransferCancelled as cancelled:
        logger.error(f"[Cliente] {cancelled}")
    except ConnectionClosed:
        logger.error("[Cliente] El servidor cerró la conexión. La descarga fue cancelada.")
    finally:
        finished.set()
        watcher.join(timeout=2 * POLL_INTERVAL)
        transport.close()
