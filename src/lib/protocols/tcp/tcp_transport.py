import socket
import threading

from lib.protocols.base_transport import BaseTransport


class TCPTransport(BaseTransport):
    def __init__(self, host: str, port: int, sock: socket.socket = None):
        self.host = host
        self.port = int(port)
        self.sock = sock
        self.cancel_requested = threading.Event()

    def start_server(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.listen(5)
        self.sock.settimeout(0.5)

    def accept(self) -> BaseTransport:
        client_sock, _ = self.sock.accept()
        return TCPTransport(self.host, self.port, sock=client_sock)

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.host, self.port))

    def send(self, data: bytes) -> None:
        self.sock.sendall(data)
        self.sock.shutdown(socket.SHUT_WR)

    def recv(self) -> bytes:
        buffer = bytearray()
        while True:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            buffer.extend(chunk)
        return bytes(buffer)

    def shutdown(self) -> None:
        """
        Aborts whatever is blocked. The kernel sends the notification
        to the other end.
        """
        if self.sock is None:
            return

        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.close()

    def close(self) -> None:
        """
        Closes the socket. Idempotent.
        """
        sock, self.sock = self.sock, None
        if sock is None:
            return

        try:
            sock.close()
        except OSError:
            pass
