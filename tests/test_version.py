"""Version del protocolo: se valida solo en el handshake.

- Cliente (connect): cualquier respuesta valida al SYN con una version
  distinta de VERSION aborta con UnsupportedVersion, sin mandar el ACK final
  ni seguir reintentando.
- Servidor (Listener.accept): un SYN con otra version se contesta con un ERR
  desde el socket de escucha y accept() sigue esperando otros clientes. No
  se abre socket efimero ni se manda SYN-ACK.

Una vez establecida la conexion, send/recv no miran la version.
"""

import unittest

from base import (
    HOST,
    ConnectionClosed,
    Hilo,
    SWTestCase,
    bt,
    listener,
    packet,
    sack,
    sw,
)


# Versiones que no son la del protocolo: los bordes del nibble y la siguiente.
VERSIONES_AJENAS = (0, 2, 15)


class TestExcepcion(unittest.TestCase):

    def test_la_version_vigente_es_1(self):
        self.assertEqual(bt.VERSION, 1)

    def test_unsupported_version_es_un_connectionclosed(self):
        # client.py y server.py ya atrapan ConnectionClosed: un cliente con
        # otra version no tiene que tirar abajo la aplicacion.
        self.assertTrue(issubclass(bt.UnsupportedVersion, ConnectionClosed))

    def test_guarda_la_version_recibida_y_la_esperada(self):
        error = bt.UnsupportedVersion(received=2, expected=1)
        self.assertEqual((error.received, error.expected), (2, 1))
        self.assertIn("2", str(error))
        self.assertIn("1", str(error))


class TestConnectVersion(SWTestCase):
    """Lado cliente: un peer crudo hace de servidor."""

    def _cliente_contra_peer(self):
        peer = self.peer()
        cliente = self.cliente(peer.addr[1])
        self.net.on_create = lambda s: setattr(s, "role", "cliente")
        hilo = Hilo(cliente.connect)
        hilo.start()
        return peer, cliente, hilo

    def _syns_enviados(self):
        return [p for p in self.net.sent("cliente") if p["SYN"]]

    def test_syn_ack_con_otra_version_lanza_unsupported_version(self):
        for version in VERSIONES_AJENAS:
            with self.subTest(version=version):
                peer, cliente, hilo = self._cliente_contra_peer()
                _, addr = peer.recv()

                peer.send_pkt(
                    addr,
                    version=version,
                    flags=packet.SYN_MASK | packet.ACK_MASK,
                    sequence_number=0,
                    ack=1,
                )

                with self.assertRaises(bt.UnsupportedVersion) as cm:
                    hilo.resultado_o_error()
                self.assertEqual(cm.exception.received, version)
                self.assertEqual(cm.exception.expected, bt.VERSION)
                self.assertTrue(cliente.is_closed)

    def test_no_manda_el_ack_final_ni_reintenta(self):
        peer, cliente, hilo = self._cliente_contra_peer()
        _, addr = peer.recv()

        peer.send_pkt(
            addr,
            version=2,
            flags=packet.SYN_MASK | packet.ACK_MASK,
            sequence_number=0,
            ack=1,
        )

        with self.assertRaises(bt.UnsupportedVersion):
            hilo.resultado_o_error()
        self.assertIsNone(
            peer.try_recv(self.timeout * 3)[0],
            "no tiene que contestar el SYN-ACK ni reenviar el SYN",
        )
        self.assertEqual(len(self._syns_enviados()), 1, self.volcado())

    def test_cualquier_respuesta_con_otra_version_aborta(self):
        # Incluye el ERR con que el Listener rechaza la version: el cliente
        # lo reconoce por la version, sin importar los flags.
        casos = {
            "SYN+ACK": packet.SYN_MASK | packet.ACK_MASK,
            "ERR": packet.ERR_MASK,
            "ACK": packet.ACK_MASK,
            "sin flags": 0,
        }
        for nombre, flags in casos.items():
            with self.subTest(flags=nombre):
                peer, cliente, hilo = self._cliente_contra_peer()
                _, addr = peer.recv()
                peer.send_pkt(
                    addr, version=2, flags=flags, sequence_number=0, ack=1
                )
                with self.assertRaises(bt.UnsupportedVersion):
                    hilo.resultado_o_error()

    def test_un_datagrama_invalido_con_otra_version_se_descarta(self):
        """is_valid va primero: basura que no es un paquete RDT no aborta,
        aunque el primer nibble no sea VERSION."""
        peer, cliente, hilo = self._cliente_contra_peer()
        _, addr = peer.recv()

        peer.send(b"\x21\x90", addr)  # truncado
        peer.send(b"\x21\x90\xff" + b"\x00" * 9, addr)  # hlen que no cierra

        syn2, addr2 = peer.recv(timeout=2.0)
        self.assertPaquete(syn2, SYN=1)
        peer.send_pkt(
            addr2,
            flags=packet.SYN_MASK | packet.ACK_MASK,
            sequence_number=0,
            ack=1,
        )
        peer.recv()
        hilo.resultado_o_error()
        self.assertFalse(cliente.is_closed)

    def test_la_version_correcta_sigue_conectando(self):
        peer, cliente, hilo = self._cliente_contra_peer()
        syn, addr = peer.recv()
        self.assertEqual(syn["version"], bt.VERSION)

        peer.send_pkt(
            addr,
            version=bt.VERSION,
            flags=packet.SYN_MASK | packet.ACK_MASK,
            sequence_number=0,
            ack=1,
        )
        ack, _ = peer.recv()
        self.assertPaquete(ack, ACK=1, SYN=0, version=bt.VERSION)
        hilo.resultado_o_error()
        self.assertFalse(cliente.is_closed)


class TestAcceptVersion(SWTestCase):
    """Lado servidor: un peer crudo hace de cliente."""

    def _servidor_escuchando(self):
        servidor = self.servidor()
        puerto = servidor.sock.getsockname()[1]
        hilo = Hilo(self.aceptar, servidor)
        hilo.start()
        return servidor, puerto, hilo

    def _completar_handshake(self, peer, puerto, isn=0):
        peer.send_pkt(
            (HOST, puerto), flags=packet.SYN_MASK, sequence_number=isn
        )
        syn_ack, addr_srv = peer.recv()
        self.assertPaquete(syn_ack, SYN=1, ACK=1)
        peer.send_pkt(
            addr_srv,
            flags=packet.ACK_MASK,
            sequence_number=isn + 1,
            ack=1,
        )

    def test_syn_con_otra_version_se_contesta_con_err(self):
        for version in VERSIONES_AJENAS:
            with self.subTest(version=version):
                servidor, puerto, hilo = self._servidor_escuchando()
                peer = self.peer()

                peer.send_pkt(
                    (HOST, puerto),
                    version=version,
                    flags=packet.SYN_MASK,
                    sequence_number=41,
                )

                err, addr_srv = peer.recv()
                self.assertPaquete(
                    err,
                    ERR=1,
                    SYN=0,
                    ACK=0,
                    CANCEL=0,
                    version=bt.VERSION,
                    ack=42,
                )
                self.assertEqual(
                    addr_srv[1],
                    puerto,
                    "el rechazo sale del socket de escucha, sin abrir un "
                    "socket efimero",
                )
                self.assertIsNone(
                    peer.try_recv(self.timeout * 3)[0],
                    "un solo ERR, sin SYN-ACK",
                )
                servidor.close()
                hilo.esperar()

    def test_el_err_lleva_el_protocolo_del_syn(self):
        for clase in (sw.StopAndWait, sack.SelectiveAck):
            with self.subTest(protocolo=clase.TAG):
                servidor, puerto, hilo = self._servidor_escuchando()
                peer = self.peer()
                peer.send_pkt(
                    (HOST, puerto),
                    version=2,
                    protocol=clase.PROTOCOL_ID,
                    flags=packet.SYN_MASK,
                    sequence_number=0,
                )
                err, _ = peer.recv()
                self.assertPaquete(err, ERR=1, protocol=clase.PROTOCOL_ID)
                servidor.close()
                hilo.esperar()

    def test_la_version_se_valida_antes_que_el_protocolo(self):
        """Con otra version el nibble de protocolo puede significar otra
        cosa: se rechaza por version, no se descarta en silencio como un
        protocolo no soportado."""
        servidor, puerto, hilo = self._servidor_escuchando()
        peer = self.peer()
        peer.send_pkt(
            (HOST, puerto),
            version=2,
            protocol=15,
            flags=packet.SYN_MASK,
            sequence_number=0,
        )
        err, _ = peer.recv()
        self.assertPaquete(err, ERR=1, SYN=0)

    def test_accept_sigue_escuchando_tras_el_rechazo(self):
        servidor, puerto, hilo = self._servidor_escuchando()
        rechazado = self.peer()
        rechazado.send_pkt(
            (HOST, puerto),
            version=2,
            flags=packet.SYN_MASK,
            sequence_number=0,
        )
        rechazado.recv()
        self.assertTrue(hilo.is_alive(), "accept no corta por el rechazo")

        bueno = self.peer()
        self._completar_handshake(bueno, puerto, isn=7)
        conexion = hilo.resultado_o_error()
        self._transportes.append(conexion)
        self.assertEqual(conexion.remote_address, bueno.addr)
        self.assertEqual(conexion.exp_sequence_number, 8)

    def test_paquetes_sin_syn_con_otra_version_se_ignoran(self):
        """Solo el SYN abre un handshake: el resto se descarta como hoy."""
        servidor, puerto, hilo = self._servidor_escuchando()
        peer = self.peer()
        for flags in (0, packet.ACK_MASK, packet.FIN_MASK, packet.ERR_MASK):
            peer.send_pkt(
                (HOST, puerto), version=2, flags=flags, sequence_number=5
            )
        self.assertIsNone(peer.try_recv(0.3)[0], "no debe contestar nada")
        self.assertTrue(hilo.is_alive())

        self._completar_handshake(peer, puerto)
        self._transportes.append(hilo.resultado_o_error())


class TestVersionDePuntaAPunta(SWTestCase):
    """Cliente y Listener reales con versiones distintas.

    connect() lee bt.VERSION y el Listener su propia copia
    (`from base_transport import VERSION`), asi que se puede desparejar
    cada lado por separado.
    """

    def _parchear(self, modulo, version):
        original = modulo.VERSION
        modulo.VERSION = version
        self.addCleanup(setattr, modulo, "VERSION", original)

    def _conectar(self, clase):
        servidor = self.servidor()
        puerto = servidor.sock.getsockname()[1]
        hilo = Hilo(self.aceptar, servidor, timeout=1.0)
        hilo.start()

        cliente = clase(HOST, puerto)
        self._transportes.append(cliente)
        self.net.on_create = lambda s: setattr(s, "role", "cliente")
        return servidor, puerto, hilo, cliente

    def test_cliente_con_otra_version_falla_al_primer_syn(self):
        self._parchear(bt, 2)
        for clase in (sw.StopAndWait, sack.SelectiveAck):
            with self.subTest(protocolo=clase.TAG):
                servidor, _, hilo, cliente = self._conectar(clase)

                with self.assertRaises(bt.UnsupportedVersion) as cm:
                    cliente.connect()
                self.assertEqual(cm.exception.received, 1)
                self.assertEqual(cm.exception.expected, 2)

                syns = [
                    p for p in self.net.sent("cliente") if p["SYN"]
                ]
                self.assertEqual(
                    len(syns), 1,
                    "falla por el ERR, no por agotar reintentos\n"
                    + self.volcado(),
                )
                self.assertTrue(hilo.is_alive(), "el servidor sigue")
                servidor.close()
                hilo.esperar()
                self.net.entries.clear()

    def test_servidor_con_otra_version_rechaza_al_cliente(self):
        self._parchear(listener, 2)
        servidor, _, hilo, cliente = self._conectar(sw.StopAndWait)

        with self.assertRaises(bt.UnsupportedVersion) as cm:
            cliente.connect()
        self.assertEqual(cm.exception.received, 2)
        self.assertEqual(cm.exception.expected, 1)
        self.assertTrue(hilo.is_alive(), "el servidor sigue")

    def test_mismas_versiones_conectan(self):
        servidor, _, hilo, cliente = self._conectar(sw.StopAndWait)
        cliente.connect()
        conexion = hilo.resultado_o_error()
        self._transportes.append(conexion)
        self.assertFalse(cliente.is_closed)
        self.assertFalse(conexion.is_closed)


if __name__ == "__main__":
    unittest.main()
