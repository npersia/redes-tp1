import os

from lib.cli.server_cli import parse_arguments
from lib.configuration.server_config import get_output_filepath, load_config
from lib.file_transfer.file_transfer import receive_content, send_error, send_file, write_file
from lib.logger.logger import configure, logger
from lib.protocols.factory import TransportFactory


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


def handle_connection(connection, output_filepath):
    received_content = receive_content(connection)
    if is_download_request(received_content):
        send_requested_file(connection, received_content, output_filepath)
    else:
        save_received_file(received_content, output_filepath)
    connection.close()


def run_server(arguments, output_filepath):
    transport = create_transport(arguments)
    transport.start_server()
    logger.info(f"[Servidor] Esperando recibir archivo vía {arguments.protocol}...")
    try:
        connection = transport.accept()
        handle_connection(connection, output_filepath)
    finally:
        transport.close()


def main():
    config = load_config()
    arguments = parse_arguments(config)
    configure(arguments.verbosity)
    output_filepath = get_output_filepath(arguments, config)
    run_server(arguments, output_filepath)