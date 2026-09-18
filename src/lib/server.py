import os
import threading
import socket

from lib.cli.server_cli import parse_arguments
from lib.configuration.server_config import get_output_filepath, load_config
from lib.file_transfer.file_transfer import receive_content, send_error, send_file, write_file
from lib.logger.logger import configure, logger
from lib.protocols.base_transport import ConnectionClosed
from lib.protocols.factory import TransportFactory

class Dispatcher:
    def __init__(self):
        self.threads = []
        self.stopping = threading.Event()
        self.lock = threading.Lock()

    def start(self, shutdown_event, transport, output_filepath):
        try:
            while not shutdown_event.is_set():
                try:
                    connection = transport.accept()
                except socket.timeout:
                    continue

                thread = threading.Thread(
                    target=self._worker,
                    args=(connection, output_filepath, handle_connection)
                )
                thread.connection = connection

                with self.lock:
                    self.threads.append(thread)

                thread.start()
        finally:
            transport.close()
            self._stop()

    def _worker(self, connection, output_filepath, handler):
        try:
            handler(connection, output_filepath, self.stopping)
        except (ConnectionClosed, OSError) as error:
            if self.stopping.is_set():
                logger.info("[Servidor] Transferencia cancelada por cierre del servidor.")
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

        for thread in active_threads:
            thread.connection.shutdown() #Esto todavía no sucede

        for thread in active_threads:
            thread.join()


def create_transport(arguments):
    return TransportFactory.get_transport(
        arguments.protocol,
        arguments.host,
        arguments.port,
    )


def is_download_request(content):
    return content.startswith(b"DOWNLOAD ")


def get_requested_filepath(content, output_filepath):
    filename = content[len(b"DOWNLOAD "):].decode("utf-8")
    filename = os.path.basename(filename)
    return os.path.join(os.path.dirname(output_filepath), filename)


def send_requested_file(connection, content, output_filepath):
    requested_filepath = get_requested_filepath(content, output_filepath)
    logger.info(f"[Servidor] Enviando '{requested_filepath}'...")
    if os.path.exists(requested_filepath):
        send_file(connection, requested_filepath)
        return
    logger.error(f"[Servidor] El archivo '{requested_filepath}' no existe.")
    send_error(connection, f"El archivo '{requested_filepath}' no existe.")


def save_received_file(content, output_filepath):
    logger.info(f"[Servidor] Cliente conectado. Guardando en '{output_filepath}'...")
    write_file(output_filepath, content)
    logger.info(f"[Servidor] Archivo guardado correctamente ({len(content)} bytes).")


def handle_connection(connection, output_filepath, stopping):
    try: 
        received_content = receive_content(connection)
        if stopping.is_set():
            logger.info("[Servidor] Transferencia cancelada, no se guarda el archivo.")
            return
        if is_download_request(received_content):
            send_requested_file( connection, received_content, output_filepath )
        else:
            save_received_file( received_content, output_filepath )
    finally: 
        connection.close()


def run_server(arguments, output_filepath,shutdown_event):
    transport = create_transport(arguments)
    transport.start_server()
    logger.info(f"[Servidor] Esperando recibir archivo vía {arguments.protocol}...")

    dispatcher = Dispatcher()
    dispatcher.start(shutdown_event, transport, output_filepath)


def main():
    config = load_config()
    arguments = parse_arguments(config)
    configure(arguments.verbosity)
    output_filepath = get_output_filepath(arguments, config)
    shutdown_event = threading.Event()
    server_thread = threading.Thread(
        target=run_server,
        args=(arguments, output_filepath, shutdown_event),
        daemon=True
    )
    server_thread.start()
    input("Presione Enter para detener el servidor...\n")
    shutdown_event.set()
    logger.info("[Servidor] Deteniendo el servidor...")
    server_thread.join()
    logger.info("[Servidor] Servidor detenido.")