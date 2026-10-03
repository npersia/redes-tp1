from lib.protocols.base_transport import BaseTransport
from lib.protocols.tcp.tcp_transport import TCPTransport
from lib.protocols.stop_and_wait.stop_wait import StopAndWait
from lib.protocols.selective_ack.selective_ack import SelectiveAck

class TransportFactory:
    _PROTOCOLS = {
        "tcp": TCPTransport,
        "sw": StopAndWait,
        "sack": SelectiveAck,
    }
    _PROTOCOLS_BY_ID = {
        1: StopAndWait,
        2: SelectiveAck,
        3: TCPTransport,
    }


    @classmethod
    def get_transport(cls, name: str, host: str, port: int) -> BaseTransport:
        clean_name = name.lower().strip()
        if clean_name not in cls._PROTOCOLS:
            raise ValueError(f"Protocolo '{name}' no soportado. Opciones: {list(cls._PROTOCOLS.keys())}")
        return cls._PROTOCOLS[clean_name](host, port)
    
    @classmethod
    def get_transport_by_id(cls, protocol_id: int, host: str, port: int) -> BaseTransport:
        if protocol_id not in cls._PROTOCOLS_BY_ID:
            raise ValueError(f"Protocolo ID '{protocol_id}' no soportado. Opciones: {list(cls._PROTOCOLS_BY_ID.keys())}")
        return cls._PROTOCOLS_BY_ID[protocol_id](host, port)

    @classmethod
    def get_class_by_id(cls, protocol_id: int):
        """La clase del protocolo que pide el header, o None si no hay ninguna."""
        return cls._PROTOCOLS_BY_ID.get(protocol_id)
