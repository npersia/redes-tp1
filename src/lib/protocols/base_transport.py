from abc import ABC, abstractmethod


class BaseTransport(ABC):

    @abstractmethod
    def start_server(self) -> None:
        pass

    @abstractmethod
    def accept(self) -> "BaseTransport":
        pass

    @abstractmethod
    def connect(self) -> None:
        pass

    @abstractmethod
    def send(self, data: bytes) -> None:
        pass

    @abstractmethod
    def recv(self) -> bytes:
        pass

    @abstractmethod
    def close(self) -> None:
        pass