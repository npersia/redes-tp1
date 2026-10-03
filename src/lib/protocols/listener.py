import socket

from lib.logger.logger import logger
import lib.protocols.base_transport as base_transport
from lib.protocols.base_transport import RECV_BUFFER, VERSION, ConnectionClosed
from lib.protocols.factory import TransportFactory
from lib.protocols.handshake_trace import AcceptTrace, ListenTrace
import lib.protocols.packet.packet as packet


class Listener:
    """Socket de escucha del servidor.

    No tiene protocolo propio: espera SYNs y cada cliente elige el suyo en el
    header. Por cada SYN completa el handshake de 3 vias desde un socket
    y devuelve la conexion armada con la clase de ese protocolo.
    """

    TAG = "listener"

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = int(port)
        self.sock = None
        self.is_closed = True
        # Distingue "nunca escucho" de "ya se cerro": en los dos sock es None.
        self.started = False
        # se leen del modulo al crear el listener, igual que en BaseTransport
        self.timeout = base_transport.TIMEOUT
        self.max_retries = base_transport.MAX_RETRIES
        self.trace = ListenTrace(self.TAG)

    def start_server(self) -> None:
        """Abre el socket de escucha y lo deja listo para aceptar conexiones."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.host, self.port))
        self.sock.settimeout(self.timeout) #sin esto accept() bloquea para siempre y el servidor no se puede apagar
        self.is_closed = False
        self.started = True

    def accept(self) -> base_transport.BaseTransport:
        """Espera el SYN de un cliente y devuelve la conexion ya establecida."""
        if not self.started:
            raise RuntimeError("No server initialized.")

        # Se toma una sola vez: un close() de otro hilo deja self.sock en None,
        # y este queda cerrado (recvfrom da OSError -> ConnectionClosed).
        sock = self.sock

        while not self.is_closed:
            try:
                data, client_address = sock.recvfrom(RECV_BUFFER)
                if not packet.is_valid(data):
                    self.trace.invalid(client_address)
                    continue
                flags = bytes([packet.get_header_flags(data)])

                if packet.get_flag_SYN(flags):
                    client_isn = packet.get_header_sequence_paquet(data)
                    protocol_id = packet.get_header_protocol(data)
                    peer_class = self._transport_for(protocol_id)
                    if peer_class is None:
                        self.trace.unsupported(client_address, protocol_id)
                        continue
                    self.trace.syn(client_address, client_isn, peer_class.TAG)
                    peer = self._accept_syn(client_address, client_isn, peer_class)
                    if peer is not None:
                        return peer
            except socket.timeout:
                return None #todavía no llego nada
            except Exception as e:
                if self.is_closed:
                    self.trace.closed()
                    raise ConnectionClosed(
                        "server closed while accepting a connection.")
                raise e
        raise ConnectionClosed("Server closed.")

    @staticmethod
    def _transport_for(protocol_id):
        """La clase que atiende la conexion, segun el protocolo que pide el SYN.

        Solo sirven las clases que usan este handshake, que son las que declaran
        ese PROTOCOL_ID en el header (TCP no lo declara).
        """
        peer_class = TransportFactory.get_class_by_id(protocol_id)
        if peer_class is None or peer_class.PROTOCOL_ID != protocol_id:
            return None
        return peer_class

    def _accept_syn(self, client_address, client_isn, peer_class):
        """Completa el handshake de un SYN ya leido de la red.

        Va aparte de accept() para no tener que releer el SYN. Devuelve None si
        el cliente no completo el handshake, y accept() sigue esperando.
        """
        #aca creo un socket efimero para la comunicacion
        client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client_sock.bind((self.host, 0))  # el 0 hace que el SO asigne un puerto libre
        #algun timeout hay que poner para que no se quede esperando por siempre,
        #tambien para poder mandar de nuevo
        client_sock.settimeout(self.timeout)

        server_isn = 0  # TODO podria o deberia ser random, pero lo dejo en 0 para que sea mas facil

        #respondo con SYN=1, ACK=1, seq = server_isn, ack = client_isn+1
        syn_packet = packet.make_packet(
            version=VERSION,
            protocol=peer_class.PROTOCOL_ID,
            flags=packet.SYN_MASK | packet.ACK_MASK,
            sequence_number=server_isn,
            ack=client_isn + 1
        )

        trace = AcceptTrace(self.TAG, client_address, client_sock.getsockname(),
                            server_isn, self.max_retries)

        retries = 0
        while retries < self.max_retries:
            client_sock.sendto(syn_packet, client_address)
            try:
                #espero ACK del cliente y SYN=0
                resp, addr = client_sock.recvfrom(RECV_BUFFER)
                if addr != client_address or not packet.is_valid(resp):
                    trace.stray(addr)
                    continue

                resp_flags = bytes([packet.get_header_flags(resp)])

                if not packet.get_flag_SYN(resp_flags):
                    if packet.get_header_ack(resp) == server_isn + 1:
                        peer = peer_class(client_address[0], client_address[1],
                                          sock=client_sock, remote_address=client_address)
                        peer._init_peer(server_isn + 1, client_isn + 1)
                        trace.established(peer.sequence_number, peer.exp_sequence_number)
                        return peer
                    trace.bad_ack(packet.get_header_ack(resp), server_isn + 1)
            except socket.timeout:
                retries += 1
                trace.retransmit(retries)

        trace.gave_up()
        return None

    def shutdown(self) -> None:
        """
        Deja de escuchar. No hay peer al que avisarle: las conexiones ya
        aceptadas tienen su propio socket y se cortan por separado.
        """
        self.close()

    def close(self) -> None:
        self.is_closed = True
        sock, self.sock = self.sock, None
        if sock is None:
            return

        logger.debug(f"[{self.TAG}] close(): dejo de escuchar en {self.host}:{self.port}")
        try:
            sock.close()
        except Exception as error:
            logger.debug(f"[{self.TAG}] close(): el socket ya venia mal ({error})")
