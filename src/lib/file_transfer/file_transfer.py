import os


def read_file(filepath):
    with open(filepath, "rb") as file:
        return file.read()


def write_file(filepath, content):
    directory = os.path.dirname(filepath)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(filepath, "wb") as file:
        file.write(content)


def send_file(transport, filepath):
    transport.send(read_file(filepath))


def send_request(transport, filename):
    transport.send(f"DOWNLOAD {filename}".encode("utf-8"))


def send_error(transport, message):
    transport.send(f"ERROR {message}".encode("utf-8"))


def receive_content(transport):
    return transport.recv()


def receive_file(transport, filepath):
    content = receive_content(transport)
    write_file(filepath, content)
    return len(content)