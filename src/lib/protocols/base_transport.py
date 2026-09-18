from abc import ABC, abstractmethod


class ConnectionClosed(Exception):
    """El otro extremo cortó la conexión antes de que terminara la transferencia."""


class BaseTransport(ABC):

    @abstractmethod
    def start_server(self) -> None:
        pass

    @abstractmethod
    def accept(self) -> 'BaseTransport':
        pass

    @abstractmethod
    def connect(self) -> None:
        pass

    @abstractmethod
    def send(self, data: bytes) -> None:
        """Recibe un buffer de cualquier tamaño,
        cliente y servidor no tienen idea de como el protocolo maneja el particionado"""
        pass

    @abstractmethod
    def recv(self) -> bytes:
        """Rearma las partes de un buffer y lo entrega transparente"""
        pass

    @abstractmethod
    def shutdown(self) -> None:
        """Aborta la transferencia en curso de cualquier send/recv pendiente."""
        pass

    @abstractmethod
    def close(self) -> None:
        pass