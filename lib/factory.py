from lib.base_transport import BaseTransport
from lib.tcp_transport import TCPTransport

class TransportFactory:
    _PROTOCOLS = {
        "tcp": TCPTransport,
        # "sw": StopAndWaitTransport,
        # "sack": SACKTransport,
    }

    @classmethod
    def get_transport(cls, name: str, host: str, port: int) -> BaseTransport:
        clean_name = name.lower().strip()
        if clean_name not in cls._PROTOCOLS:
            raise ValueError(f"Protocolo '{name}' no soportado. Opciones: {list(cls._PROTOCOLS.keys())}")
        return cls._PROTOCOLS[clean_name](host, port)