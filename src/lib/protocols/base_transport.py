import threading
from abc import ABC, abstractmethod


class ConnectionClosed(Exception):
    """El otro extremo cortó la conexión antes de que terminara la transferencia."""


class TransferCancelled(ConnectionClosed):
    """
    Este extremo abortó la transferencia a pedido del usuario (Enter en la consola).
    """


class BaseTransport(ABC):

    def cancel(self) -> None:
        """
        Pide abortar la transferencia en curso.
        """
        self.cancel_requested.set()

    def is_cancelled(self) -> bool:
        return self.cancel_requested.is_set()

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
        """
        Aborta la transferencia en curso de cualquier send/recv pendiente.
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """
        Libera el socket sin avisarle nada al otro extremo.

        Cierre normal: tambien se llama al terminar una transferencia exitosa,
        asi que no puede mandar un aviso de aborto.
        """
        pass
