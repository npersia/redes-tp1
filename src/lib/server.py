import os
import threading

from lib.cli.server_cli import parse_arguments
from lib.configuration.server_config import get_storage_dir, load_config
from lib.file_transfer.file_transfer import (
    InvalidMessage,
    is_upload_request,
    parse_upload_request,
    receive_content,
    send_error,
    send_file,
    send_ok,
    write_file,
)
from lib.logger.logger import configure, logger
from lib.protocols.base_transport import ConnectionClosed, TransferCancelled
from lib.protocols.listener import Listener

# Tamanio maximo de archivo que el servidor acepta en un upload (2 GB).
MAX_FILE_SIZE = 1 * 1024**3


class Dispatcher:
    def __init__(self):
        self.threads = []
        self.stopping = threading.Event()
        self.lock = threading.Lock()

    def start(self, shutdown_event, transport, storage_dir):
        try:
            while not shutdown_event.is_set():
                try:
                    connection = transport.accept()
                except ConnectionClosed:
                    break

                if connection is None:
                    continue

                thread = threading.Thread(
                    target=self._worker,
                    args=(connection, storage_dir, handle_connection),
                )
                thread.connection = connection

                with self.lock:
                    self.threads.append(thread)

                thread.start()
                logger.debug(
                    f"[Servidor] hilo {thread.name} atendiendo a "
                    f"{connection.remote_address}; "
                    f"{len(self.threads)} conexion(es) activa(s)"
                )
        finally:
            transport.close()
            self._stop()

    def _worker(self, connection, storage_dir, handler):
        try:
            handler(connection, storage_dir, self.stopping)
        except TransferCancelled as cancelled:
            logger.info(f"[Servidor] {cancelled}")
        except (ConnectionClosed, OSError) as error:
            if self.stopping.is_set():
                logger.info(
                    "[Servidor] Transferencia cancelada por "
                    "cierre del servidor."
                )
            else:
                logger.error(f"[Servidor] Error en la conexión: {error}")
        finally:
            with self.lock:
                if threading.current_thread() in self.threads:
                    self.threads.remove(threading.current_thread())

    def _stop(self):
        self.stopping.set()

        with self.lock:
            active_threads = list(self.threads)

        logger.debug(
            f"[Servidor] cerrando: {len(active_threads)} "
            f"transferencia(s) en curso a abortar"
        )
        for thread in active_threads:
            # avisa ERR+CANCEL y recien despues cierra
            thread.connection.shutdown()

        for thread in active_threads:
            thread.join()


def is_download_request(content):
    return content.startswith(b"DOWNLOAD ")


def get_requested_filepath(content, storage_dir):
    filename = content[len(b"DOWNLOAD "):].decode("utf-8")
    filename = os.path.basename(filename)
    return os.path.join(storage_dir, filename)


def send_requested_file(connection, content, storage_dir):
    requested_filepath = get_requested_filepath(content, storage_dir)
    logger.info(f"[Servidor] Enviando '{requested_filepath}'...")
    if os.path.exists(requested_filepath):
        send_file(connection, requested_filepath)
        return
    logger.error(f"[Servidor] El archivo '{requested_filepath}' no existe.")
    send_error(connection, f"El archivo '{requested_filepath}' no existe.")


def receive_uploaded_file(connection, content, storage_dir, stopping):
    try:
        request = parse_upload_request(content)
    except InvalidMessage as error:
        logger.error(f"[Servidor] {error}")
        send_error(connection, str(error))
        return
    if request.size > MAX_FILE_SIZE:
        logger.error(
            f"[Servidor] Rechazo '{request.filename}': "
            f"{request.size} bytes supera el límite de {MAX_FILE_SIZE}."
        )
        send_error(
            connection,
            f"El archivo ({request.size} bytes) supera "
            f"el límite de {MAX_FILE_SIZE} bytes.",
        )
        return
    send_ok(connection)

    file_bytes = receive_content(connection)
    if stopping.is_set():
        logger.info(
            "[Servidor] Transferencia cancelada, "
            "no se guarda el archivo."
        )
        return
    if len(file_bytes) != request.size:
        logger.error(
            f"[Servidor] '{request.filename}': se declararon "
            f"{request.size} bytes y llegaron {len(file_bytes)}; "
            f"no se guarda."
        )
        return
    filename = os.path.basename(request.filename)
    target_filepath = os.path.join(storage_dir, filename)
    logger.info(f"[Servidor] Guardando en '{target_filepath}'...")
    write_file(target_filepath, file_bytes)
    logger.info(
        f"[Servidor] Archivo '{filename}' guardado "
        f"correctamente ({len(file_bytes)} bytes)."
    )


def handle_connection(connection, storage_dir, stopping):
    try:
        received_content = receive_content(connection)
        if stopping.is_set():
            logger.info(
                "[Servidor] Transferencia cancelada, "
                "no se guarda el archivo."
            )
            return
        if is_download_request(received_content):
            send_requested_file(connection, received_content, storage_dir)
        elif is_upload_request(received_content):
            receive_uploaded_file(
                connection, received_content, storage_dir, stopping
            )
        else:
            logger.error("[Servidor] Petición no reconocida.")
    finally:
        connection.close()


def run_server(arguments, storage_dir, shutdown_event):
    transport = Listener(arguments.host, arguments.port)
    transport.start_server()
    logger.info(
        f"[Servidor] Esperando recibir archivos en "
        f"{arguments.host}:{arguments.port}..."
    )
    dispatcher = Dispatcher()
    dispatcher.start(shutdown_event, transport, storage_dir)


def main():
    config = load_config()
    arguments = parse_arguments(config)
    configure(arguments.verbosity)
    storage_dir = get_storage_dir(arguments)
    shutdown_event = threading.Event()
    server_thread = threading.Thread(
        target=run_server,
        args=(arguments, storage_dir, shutdown_event),
        daemon=True,
    )
    server_thread.start()
    input("Presione Enter para detener el servidor...\n")
    shutdown_event.set()
    logger.info("[Servidor] Deteniendo el servidor...")
    server_thread.join()
    logger.info("[Servidor] Servidor detenido.")
