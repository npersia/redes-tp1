import socket

from lib.protocols.base_transport import BaseTransport


class TCPTransport(BaseTransport):
    def __init__(self, host: str, port: int, sock: socket.socket = None):
        self.host = host
        self.port = int(port)
        self.sock = sock

    def start_server(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.listen(5)

    def accept(self) -> BaseTransport:
        client_sock, _ = self.sock.accept()
        return TCPTransport(self.host, self.port, sock=client_sock)

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.host, self.port))

    def send(self, data: bytes) -> None:
        # En TCP sendall envía todo el buffer.
        # En StopWait/SACK (UDP), la clase iterará internamente `data` en bloques MSS de 1024 bytes.
        self.sock.sendall(data)
        self.sock.shutdown(socket.SHUT_WR) # Señaliza fin de datos en el stream TCP

    def recv(self) -> bytes:
        buffer = bytearray()
        while True:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            buffer.extend(chunk)
        return bytes(buffer)

    def close(self) -> None:
        if self.sock:
            self.sock.close()