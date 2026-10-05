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
        # En TCP sendall envía todo el buffer.
        # En StopWait/SACK (UDP), la clase iterará internamente `data`
        # en bloques MSS de 1024 bytes.
        self.sock.sendall(data)
        # Señaliza fin de datos en el stream TCP
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
        Aborta lo que este bloqueado. El aviso al otro extremo lo manda
        el kernel.
        """
        if self.sock is None:
            return

        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            # nunca se conecto, ya estaba cerrado, o es el socket de
            # escucha
            pass
        self.close()

    def close(self) -> None:
        """
        Libera el socket. Idempotente.
        """
        sock, self.sock = self.sock, None
        if sock is None:
            return

        try:
            sock.close()
        except OSError:
            pass
