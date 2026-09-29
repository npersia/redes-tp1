import socket
import threading

from lib.logger.logger import logger
from lib.protocols.base_transport import BaseTransport, ConnectionClosed, TransferCancelled
import lib.protocols.packet.packet as packet
from lib.protocols.stop_and_wait.trace import RecvTrace, SendTrace


# CONSTANTES
TIMEOUT = 1.0
PROTOCOL_STOP_AND_WAIT = 1 #TODO tiene que irse a base_transport esta constante o al factory
VERSION = 1
RETRIES = 10
MAX_PAYLOAD_SIZE = 1400
ABORT_NOTICES = 3 #cuantas veces repito el ERR al abortar, porque UDP lo puede perder y nadie lo confirma


# OJO QUE CAMBIO LA CABECERA PORQUE ES UDP, NECESITO SABER EL REMOTE ADDRESS PARA TRABAJAR. TCP ME LO DABA
class StopAndWait(BaseTransport):
    def __init__(self, host: str, port: int, sock: socket.socket | None = None, remote_address: tuple[str,int] = None):
        self.host = host
        self.port = int(port)
        self.sock = sock
        self.remote_address = remote_address or (host,port)

        self.sequence_number = 0 #TODO deberia ser un random
        self.is_closed = True #antes de empezar considero que no hay conexion, entonces esta cerrado
        self.timeout = TIMEOUT #TODO PODRIA ESTAR EN UN ARCHIVO APARTE
        self.retries = RETRIES
        self.exp_sequence_number = 0 #TODO deberia ser un random
        self.cancel_requested = threading.Event() #lo prende cancel() desde otro hilo
        self.is_listener = False #el socket de escucha no tiene un peer al que avisarle


    ###################################################################################################################
    # ACA EMPIEZA LA CANCELACION
    ###################################################################################################################
    def cancel(self) -> None:
        """
        Pide abortar la transferencia en curso. Se llama desde otro hilo.
        """
        logger.debug("[SW] cancel(): cancelacion pedida desde otro hilo")
        self.cancel_requested.set()

    def notify_abort(self) -> None:
        """
        Le avisa al otro extremo que cortamos, para que no quede esperando para siempre.

        Van los dos bits prendidos, ERR y CANCEL:
        - ERR: le avisa al otro extremo que hubo un error y que no espere mas.
        - CANCEL: le avisa al otro extremo que el error fue deliberado.
        """
        if self.sock is None or self.remote_address is None or self.is_listener:
            return #sin peer (o siendo el socket de escucha) el aviso iria a la nada

        err_packet = packet.make_packet(
            version=VERSION,
            protocol=PROTOCOL_STOP_AND_WAIT,
            flags=packet.ERR_MASK | packet.CANCEL_MASK,
            sequence_number=self.sequence_number,
            ack=self.exp_sequence_number
        )

        logger.debug(f"[SW] aviso la cancelacion a {self.remote_address} con "
                     f"{ABORT_NOTICES} paquetes de aborto (flags ERR+CANCEL)")
        for _ in range(ABORT_NOTICES):
            try:
                self.sock.sendto(err_packet, self.remote_address)
            except OSError as error:
                logger.debug(f"[SW] no se pudo avisar el aborto: {error}")
                return


    ###################################################################################################################
    # ACA EMPIEZA EL HANDSHAKE, HAY QUE MOVERLO A BASE_TRANSPORT
    ###################################################################################################################
    def start_server(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.host,self.port))
        self.sock.settimeout(self.timeout) #sin esto accept() bloquea para siempre y el servidor no se puede apagar
        self.is_closed = False
        self.is_listener = True #remote_address apunta a si mismo: no hay a quien avisarle

    def accept(self) -> "StopAndWait":
        """lado servidor, espera el SYN y crea un socket efimero. """
        if not self.sock:
            raise RuntimeError("No server initialized.")

        while not self.is_closed:
            try:
                # paso 1: recibe el SYN inicial
                data, client_address = self.sock.recvfrom(2048) #uso 2048 porq es mucho mas grande que los 1500 clasicos
                if not packet.is_valid(data):
                    logger.debug(f"[SW] accept: datagrama invalido de {client_address}, lo descarto")
                    continue
                flags = bytes([packet.get_header_flags(data)])

                if packet.get_flag_SYN(flags):
                    client_isn = packet.get_header_sequence_paquet(data)
                    logger.debug(f"[SW] accept: llego SYN de {client_address} (isn cliente={client_isn})")

                    #aca creo un socket efimero para la comunicacion
                    client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    client_sock.bind((self.host, 0)) # el port 0 hace que el SO asigne un puerto libre
                    client_sock.settimeout(self.timeout) #algun timeout hay que poner para que no se quede esperando por siempre, tambien para poder mandar de nuevo

                    logger.debug(f"[SW] accept: socket efimero {client_sock.getsockname()} dedicado a {client_address}")

                    server_isn = 0 # TODO podria o deberia ser random, pero lo dejo en 0 para que sea mas facil

                    #paso 2: respondo con en SYN=1, ACK=1 seq = server_isn, ack = client_isn+1
                    syn_packet = packet.make_packet(
                        version=VERSION,
                        protocol=PROTOCOL_STOP_AND_WAIT,
                        flags=packet.SYN_MASK|packet.ACK_MASK,
                        sequence_number=server_isn,
                        ack=client_isn+1
                    )

                    logger.debug(f"[SW] accept: envio SYN-ACK a {client_address} (isn servidor={server_isn})")

                    retries = 0
                    while retries < self.retries:
                        client_sock.sendto(syn_packet, client_address)
                        try:

                            #paso 3: espero ACK del cliente y SYN=0
                            resp, addr = client_sock.recvfrom(2048) #igual que mas arriba, pongo un numero mas grande que 1500
                            if addr != client_address or not packet.is_valid(resp):
                                logger.debug(f"[SW] accept: descarto respuesta de {addr} durante el handshake")
                                continue

                            resp_flags = bytes([packet.get_header_flags(resp)])

                            if not packet.get_flag_SYN(resp_flags):
                                if packet.get_header_ack(resp) == server_isn + 1:
                                    client_transport = StopAndWait(
                                        host=client_address[0],
                                        port=client_address[1],
                                        sock=client_sock,
                                        remote_address=client_address
                                    )
                                    client_transport.sequence_number = server_isn + 1
                                    client_transport.exp_sequence_number = client_isn + 1 #lo seteo para no generar problemas despues
                                    client_transport.is_closed = False
                                    logger.debug(f"[SW] accept: conexion establecida con {client_address} "
                                                 f"(seq={client_transport.sequence_number}, "
                                                 f"seq esperado={client_transport.exp_sequence_number})")
                                    return client_transport
                                logger.debug(f"[SW] accept: ACK final con ack={packet.get_header_ack(resp)}, "
                                             f"esperaba {server_isn + 1}; lo descarto")
                        except socket.timeout:
                            retries +=1
                            logger.debug(f"[SW] accept: sin ACK final de {client_address}, "
                                         f"reenvio SYN-ACK ({retries}/{self.retries})")
                    logger.debug(f"[SW] accept: handshake con {client_address} abandonado tras {self.retries} intentos")
            except socket.timeout:
                raise #que decida el llamador si sigue esperando (ver Dispatcher en server.py)
            except Exception as e:
                if self.is_closed:
                    logger.debug("[SW] accept: el socket del servidor se cerro mientras esperaba conexiones")
                    raise ConnectionClosed("server closed in acepte connection.")
                raise e
        raise ConnectionClosed("Server closed.")





    def connect(self) -> None:
        """lado cliente, inicia la comunicacion."""

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(self.timeout)

        client_isn = self.sequence_number

        logger.debug(f"[SW] handshake: envio SYN a {self.host}:{self.port} (isn={client_isn}, "
                     f"timeout {self.timeout}s, hasta {self.retries} intentos)")

        #paso 1: SYN=1, seq = client_isn

        syn_packet = packet.make_packet(
            version=VERSION,
            protocol=PROTOCOL_STOP_AND_WAIT,
            flags=packet.SYN_MASK,
            sequence_number=client_isn
        )

        retries = 0
        while retries < self.retries:
            if self.cancel_requested.is_set():
                logger.debug("[SW] handshake cancelado por el usuario")
                raise TransferCancelled("Conexion cancelada por el usuario durante el handshake.")
            try:
                self.sock.sendto(syn_packet, (self.host, self.port))
                data, server_address = self.sock.recvfrom(2048)
                if not packet.is_valid(data):
                    logger.debug(f"[SW] handshake: datagrama invalido de {server_address}, lo descarto")
                    continue
                flags = bytes([packet.get_header_flags(data)])

                #paso 2: SYN=1, ACK=1, ack = client_isn+1
                if packet.get_flag_SYN(flags) and packet.get_flag_ACK(flags):
                    if packet.get_header_ack(data) == client_isn + 1:
                        self.remote_address = server_address
                        server_isn = packet.get_header_sequence_paquet(data)

                        logger.debug(f"[SW] handshake: SYN-ACK de {server_address} (isn servidor={server_isn}); "
                                     f"respondo el ACK final")

                        self.sequence_number = client_isn + 1
                        self.exp_sequence_number = server_isn + 1 # lo seteo para que no quede sin asignar en el momento de la transmision de datos


                        #paso 3: SYN=0 seq=client_isn+1, ack = server_isn+1
                        ack_packet = packet.make_packet(
                            version=VERSION,
                            protocol=PROTOCOL_STOP_AND_WAIT,
                            flags=packet.ACK_MASK, # la variable ya tiene el SYN = 0,
                            sequence_number=self.sequence_number,
                            ack=server_isn + 1
                        )

                        self.sock.sendto(ack_packet, self.remote_address)
                        self.is_closed = False
                        logger.debug(f"[SW] conexion establecida con {self.remote_address} "
                                     f"(seq={self.sequence_number}, seq esperado={self.exp_sequence_number})")
                        return
                    logger.debug(f"[SW] handshake: SYN-ACK con ack={packet.get_header_ack(data)}, "
                                 f"esperaba {client_isn + 1}; lo descarto")
            except socket.timeout:
                retries += 1
                logger.debug(f"[SW] handshake: sin respuesta al SYN, reintento {retries}/{self.retries}")


        logger.debug(f"[SW] handshake fallido: {self.retries} intentos sin respuesta de {self.host}:{self.port}")
        raise ConnectionClosed(f"Timeout: can't conect to {self.host}:{self.port}.")






    ###################################################################################################################
    # ACA TERMINA EL HANDSHAKE, HAY QUE MOVERLO A BASE_TRANSPORT
    ###################################################################################################################


    def send(self, data: bytes) -> None:
        if self.is_closed or self.sock is None:
            raise ConnectionClosed("the socket is not initialized or the connection is closed")

        chunks = []

        for i in range(0, len(data), MAX_PAYLOAD_SIZE):
            chunks.append(data[i:i + MAX_PAYLOAD_SIZE])
        if not chunks:
            chunks = [b""]

        trace = SendTrace(len(data), len(chunks), MAX_PAYLOAD_SIZE,
                          self.remote_address, self.sequence_number)

        for i, chunk in enumerate(chunks):
            if self.cancel_requested.is_set():
                self.notify_abort()
                trace.cancelled()
                raise TransferCancelled(f"Transferencia cancelada por el usuario: {trace.balance}.")

            last = (i == len(chunks) - 1)
            if last:
                flags = packet.FIN_MASK
            else:
                flags = 0
            payload_len = len(chunk)


            pkt = packet.make_packet(
                version=VERSION,
                protocol=PROTOCOL_STOP_AND_WAIT,
                flags=flags,
                sequence_number=self.sequence_number,
                payload=chunk
            )

            if payload_len > 0:
                expected_ack = self.sequence_number + payload_len
            else:
                expected_ack = self.sequence_number + 1

            if last:
                trace.fin(self.sequence_number)

            ret = 0
            ack_received = False

            while not ack_received and ret < self.retries:
                try:

                    self.sock.sendto(pkt, self.remote_address)
                    self.sock.settimeout(self.timeout)

                    resp, addr = self.sock.recvfrom(2048)
                    if addr != self.remote_address or not packet.is_valid(resp):
                        trace.stray(addr, expected_ack)
                        continue

                    flags_byte = bytes([packet.get_header_flags(resp)])

                    if packet.get_flag_ERR(flags_byte):
                        if packet.get_flag_CANCEL(flags_byte):
                            trace.remote_cancel()
                            raise TransferCancelled(
                                f"El otro extremo canceló la transferencia: {trace.balance}.")
                        trace.remote_error()
                        raise ConnectionClosed("El remoto notificó un error con flag ERR.")

                    if not packet.get_flag_ACK(flags_byte):
                        trace.no_ack(flags_byte)
                        continue

                    ack_num = packet.get_header_ack(resp)
                    if ack_num == expected_ack:
                        ack_received = True
                        self.sequence_number = expected_ack  # se incrementa el seq number en n bytes
                    else:
                        trace.bad_ack(ack_num, expected_ack)
                except socket.timeout:
                    ret += 1
                    trace.retransmit(expected_ack, ret, self.retries)
                    if self.cancel_requested.is_set():
                        self.notify_abort()
                        trace.cancelled(retransmitiendo=True)
                        raise TransferCancelled(f"Transferencia cancelada por el usuario: {trace.balance}.")

            if not ack_received:
                trace.gave_up(self.sequence_number, self.retries)
                raise ConnectionClosed("Connexion lost, many transitions.")

            trace.acked(payload_len)

        trace.done()


    def recv(self) -> bytes:
        if self.is_closed or self.sock is None:
            raise ConnectionClosed("the socket is not initialized or the connection is closed")

        received_buffer = bytearray()
        self.sock.settimeout(self.timeout)

        trace = RecvTrace(self.remote_address, self.exp_sequence_number, self.timeout)

        while True:
            if self.cancel_requested.is_set():
                self.notify_abort()
                trace.cancelled()
                raise TransferCancelled(f"Recepcion cancelada por el usuario: {trace.bytes} bytes recibidos.")

            try:
                data, addr = self.sock.recvfrom(2048)
                if addr != self.remote_address or not packet.is_valid(data):
                    trace.stray(addr, addr == self.remote_address)
                    continue

                flags_byte = bytes([packet.get_header_flags(data)])

                if packet.get_flag_ERR(flags_byte):
                    if packet.get_flag_CANCEL(flags_byte):
                        trace.remote_cancel()
                        raise TransferCancelled(
                            f"El otro extremo canceló la transferencia: "
                            f"{trace.bytes} bytes recibidos.")
                    trace.remote_error()
                    raise ConnectionClosed("Transferencia abortada por error remoto (ERR flag).")

                # Si retransmiten el SYN-ACK del handshake
                if packet.get_flag_SYN(flags_byte) and packet.get_flag_ACK(flags_byte):
                    trace.syn_ack_again()
                    self.send_ack()
                    continue

                seq = packet.get_header_sequence_paquet(data)

                # Llegó el paquete esperado
                if seq == self.exp_sequence_number:
                    payload = packet.get_payload(data)
                    payload_len = len(payload)
                    received_buffer.extend(payload)

                    # Avanzamos el apuntador de recepción la cantidad de bytes recibidos
                    if payload_len > 0:
                        packet_bytes = payload_len
                    else:
                        packet_bytes = 1

                    self.exp_sequence_number += packet_bytes
                    self.send_ack()
                    trace.stored(payload_len)

                    if packet.get_flag_FIN(flags_byte):
                        trace.done()
                        return bytes(received_buffer)

                # el paquete esta duplicado o vencido
                elif seq < self.exp_sequence_number:
                    trace.duplicate(seq, self.exp_sequence_number)
                    self.send_ack()

                else:
                    trace.ahead(seq, self.exp_sequence_number)

            except socket.timeout:
                trace.idle()
                continue
            except Exception as e:
                if self.is_closed:
                    trace.closed()
                    raise ConnectionClosed("Conetion closed.")
                raise e

    def send_ack(self) -> None:
        ack_pkt = packet.make_packet(
            version=VERSION,
            protocol=PROTOCOL_STOP_AND_WAIT,
            flags=packet.ACK_MASK,
            sequence_number=self.sequence_number,
            ack=self.exp_sequence_number
        )
        self.sock.sendto(ack_pkt, self.remote_address)

    ###################################################################################################################
    # ACA EMPIEZA EL CIERRE, HAY QUE MOVERLO A BASE_TRANSPORT
    ###################################################################################################################

    def shutdown(self) -> None:
        """
        Aborta: le avisa al otro extremo y corta lo que este bloqueado.
        """
        if self.sock is None:
            self.is_closed = True
            return #ya se solto el socket: no repito el aviso ni el cierre

        self.notify_abort()
        logger.debug(f"[SW] shutdown(): avise el corte a {self.remote_address}")
        self.close()

    def close(self) -> None:
        """
        Libera el socket sin avisarle nada al otro extremo.
        """
        self.is_closed = True
        sock, self.sock = self.sock, None
        if sock is None:
            return

        logger.debug(f"[SW] close(): cierro el socket local de {self.remote_address}")
        try:
            sock.close()
        except Exception as error:
            logger.debug(f"[SW] close(): el socket ya venia mal ({error})")


    ###################################################################################################################
    # ACA TERMINA EL CIERRE, HAY QUE MOVERLO A BASE_TRANSPORT
    ###################################################################################################################