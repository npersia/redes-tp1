"""Handshake del lado CLIENTE: StopAndWait.connect()."""

import socket
import time
import unittest

from base import HOST, ConnectionClosed, Hilo, SWTestCase, packet, bt, sw
from netsim import drop_nth


class TestConnect(SWTestCase):

    def _cliente_contra_peer(self, policy=None):
        """Arranca connect() contra un peer crudo que hace de servidor."""
        peer = self.peer()
        cliente = self.cliente(peer.addr[1])

        def marcar(s):
            s.role = "cliente"
            s.policy = policy

        self.net.on_create = marcar
        hilo = Hilo(cliente.connect)
        hilo.start()
        return peer, cliente, hilo

    def _responder_syn_ack(
        self, peer, addr, client_isn=0, server_isn=0, flags=None, ack=None
    ):
        peer.send_pkt(
            addr,
            flags=(
                packet.SYN_MASK | packet.ACK_MASK if flags is None else flags
            ),
            sequence_number=server_isn,
            ack=client_isn + 1 if ack is None else ack,
        )

    # -- camino feliz ------------------------------------------------------

    def test_handshake_completo_deja_la_conexion_abierta(self):
        peer, cliente, hilo = self._cliente_contra_peer()

        syn, addr = peer.recv()
        self.assertPaquete(syn, SYN=1, ACK=0, FIN=0, ERR=0, seq=0)
        self.assertEqual(syn["version"], bt.VERSION)
        self.assertEqual(syn["protocol"], sw.StopAndWait.PROTOCOL_ID)

        self._responder_syn_ack(peer, addr, client_isn=0, server_isn=100)

        ack, _ = peer.recv()
        self.assertPaquete(ack, SYN=0, ACK=1, seq=1, ack=101)

        hilo.resultado_o_error()
        self.assertFalse(cliente.is_closed)
        self.assertEqual(cliente.sequence_number, 1, "seq = client_isn + 1")
        self.assertEqual(
            cliente.exp_sequence_number,
            101,
            "exp = server_isn + 1"
        )
        self.assertEqual(cliente.remote_address, peer.addr)

    def test_adopta_el_puerto_efimero_desde_el_que_le_contestan(self):
        """El servidor real contesta desde otro socket: el cliente debe migrar.
        """
        peer_escucha = self.peer()
        peer_efimero = self.peer()
        cliente = self.cliente(peer_escucha.addr[1])

        self.net.on_create = lambda s: setattr(s, "role", "cliente")
        hilo = Hilo(cliente.connect)
        hilo.start()

        _, addr = peer_escucha.recv()
        self._responder_syn_ack(peer_efimero, addr)

        ack, _ = peer_efimero.recv()
        self.assertPaquete(ack, ACK=1, SYN=0)
        hilo.resultado_o_error()
        self.assertEqual(cliente.remote_address, peer_efimero.addr)

    def test_el_socket_del_cliente_queda_con_timeout(self):
        peer, cliente, hilo = self._cliente_contra_peer()
        _, addr = peer.recv()
        self._responder_syn_ack(peer, addr)
        peer.recv()
        hilo.resultado_o_error()
        self.assertAlmostEqual(
            cliente.sock.gettimeout(),
            self.timeout,
            places=4
        )

    # -- retransmision y timeouts -----------------------------------------

    def test_retransmite_el_syn_si_se_pierde(self):
        peer, cliente, hilo = self._cliente_contra_peer(policy=drop_nth(1))

        # el primer SYN se cae en la red; el segundo llega tras un timeout
        t0 = time.monotonic()
        syn, addr = peer.recv(timeout=2.0)
        transcurrido = time.monotonic() - t0
        self.assertPaquete(syn, SYN=1, seq=0)
        self.assertGreaterEqual(
            transcurrido,
            self.timeout * 0.8,
            "la retransmision tiene que esperar el timeout",
        )

        self._responder_syn_ack(peer, addr)
        peer.recv()
        hilo.resultado_o_error()
        self.assertFalse(cliente.is_closed)

    def test_retransmite_si_se_pierde_el_syn_ack(self):
        peer, cliente, hilo = self._cliente_contra_peer()

        syn1, addr = peer.recv()
        # no contestamos: el cliente debe volver a mandar el SYN
        syn2, addr2 = peer.recv(timeout=2.0)
        self.assertEqual(
            syn1["raw"],
            syn2["raw"],
            "el SYN retransmitido es identico"
        )
        self.assertEqual(addr, addr2, "usa el mismo socket/puerto")

        self._responder_syn_ack(peer, addr2)
        peer.recv()
        hilo.resultado_o_error()

    def test_sin_servidor_agota_reintentos_y_falla(self):
        cliente = self.cliente(_puerto_libre())
        self.net.on_create = lambda s: setattr(s, "role", "cliente")

        t0 = time.monotonic()
        with self.assertRaises(ConnectionClosed) as cm:
            cliente.connect()
        transcurrido = time.monotonic() - t0

        self.assertIn("Timeout", str(cm.exception))
        self.assertTrue(cliente.is_closed)
        self.assertEqual(
            len(self.net.sent("cliente")),
            self.retries,
            f"tiene que mandar exactamente RETRIES={self.retries} SYN",
        )
        self.assertGreaterEqual(
            transcurrido,
            self.timeout * self.retries * 0.8
        )

    # -- respuestas que hay que descartar ----------------------------------

    def test_ignora_syn_ack_con_numero_de_ack_incorrecto(self):
        peer, cliente, hilo = self._cliente_contra_peer()
        _, addr = peer.recv()

        self._responder_syn_ack(peer, addr, ack=999)  # ack equivocado
        # el cliente no acepta: retransmite el SYN
        syn2, addr2 = peer.recv(timeout=2.0)
        self.assertPaquete(syn2, SYN=1, ACK=0)

        self._responder_syn_ack(peer, addr2, ack=1)  # ahora bien
        peer.recv()
        hilo.resultado_o_error()
        self.assertFalse(cliente.is_closed)

    def test_ignora_ack_puro_sin_syn(self):
        peer, cliente, hilo = self._cliente_contra_peer()
        _, addr = peer.recv()
        peer.send_pkt(addr, flags=packet.ACK_MASK, sequence_number=0, ack=1)

        syn2, addr2 = peer.recv(timeout=2.0)
        self.assertPaquete(syn2, SYN=1)
        self._responder_syn_ack(peer, addr2)
        peer.recv()
        hilo.resultado_o_error()

    def test_ignora_syn_puro_sin_ack(self):
        peer, cliente, hilo = self._cliente_contra_peer()
        _, addr = peer.recv()
        peer.send_pkt(addr, flags=packet.SYN_MASK, sequence_number=0, ack=1)

        syn2, addr2 = peer.recv(timeout=2.0)
        self.assertPaquete(syn2, SYN=1)
        self._responder_syn_ack(peer, addr2)
        peer.recv()
        hilo.resultado_o_error()

    def test_descarta_respuestas_truncadas(self):
        """Antes disparaban IndexError en get_header_flags."""
        peer, cliente, hilo = self._cliente_contra_peer()
        _, addr = peer.recv()

        peer.send(b"", addr)
        peer.send(b"\x11\x90", addr)
        peer.send(b"\x11\x90\xff" + b"\x00" * 9, addr)  # hlen que no cierra
        self.assertTrue(hilo.is_alive(), "connect sobrevive a la basura")

        syn2, addr2 = peer.recv(timeout=2.0)
        self.assertPaquete(syn2, SYN=1)
        self._responder_syn_ack(peer, addr2)
        peer.recv()
        hilo.resultado_o_error()
        self.assertFalse(cliente.is_closed)

    def test_acepta_syn_ack_de_una_direccion_cualquiera(self):
        """HALLAZGO: connect() no valida el origen del SYN-ACK.

        Cualquier tercero que conozca el ISN puede secuestrar el handshake:
        el cliente adopta su direccion como remote_address.
        """
        peer_servidor = self.peer()
        intruso = self.peer()
        cliente = self.cliente(peer_servidor.addr[1])
        self.net.on_create = lambda s: setattr(s, "role", "cliente")

        hilo = Hilo(cliente.connect)
        hilo.start()

        _, addr = peer_servidor.recv()
        self._responder_syn_ack(
            intruso,
            addr,
            server_isn=7
        )  # responde el intruso

        ack, _ = intruso.recv()
        self.assertPaquete(ack, ACK=1)
        hilo.resultado_o_error()
        self.assertEqual(
            cliente.remote_address,
            intruso.addr,
            "el cliente quedo hablando con el intruso",
        )

    # -- HALLAZGO: los reintentos solo cuentan timeouts --------------------

    def test_ruido_constante_impide_que_connect_termine_nunca(self):
        """HALLAZGO (sin arreglar): `retries` solo se incrementa en
        `except socket.timeout`.

        Si llega trafico que no matchea, el while vuelve a empezar sin
        consumir reintentos y connect() se queda girando para siempre,
        retransmitiendo el SYN a maxima velocidad.
        """
        peer, cliente, hilo = self._cliente_contra_peer()

        presupuesto = self.timeout * self.retries * 3  # de sobra para fallar
        fin = time.monotonic() + presupuesto
        enviados = 0
        while time.monotonic() < fin:
            p, addr = peer.try_recv(0.05)
            if p is None:
                continue
            # respuesta que nunca sirve (SYN sin ACK)
            peer.send_pkt(
                addr,
                flags=packet.SYN_MASK,
                sequence_number=0,
                ack=0
            )
            enviados += 1

        self.assertTrue(
            hilo.is_alive(),
            "connect() deberia haber abandonado tras RETRIES; sigue vivo",
        )
        self.assertGreater(
            enviados,
            self.retries,
            f"retransmitio {enviados} SYN, mas que los {self.retries} "
            f"reintentos",
        )
        cliente.shutdown()  # desbloquea el hilo para el tearDown

    def test_reconectar_pisa_el_socket_previo_y_arrastra_el_seq(self):
        """HALLAZGO (sin arreglar): connect() usa el seq actual como ISN y no
        cierra el socket viejo.

        Tras una conexion, `sequence_number` quedo en client_isn+1. Un segundo
        connect() toma ese valor como nuevo ISN en vez de reiniciar, y ademas
        reemplaza `self.sock` sin cerrar el anterior (fuga de descriptor).
        """
        peer, cliente, hilo = self._cliente_contra_peer()
        _, addr = peer.recv()
        self._responder_syn_ack(peer, addr)
        peer.recv()
        hilo.resultado_o_error()
        primero = cliente.sock
        self.assertEqual(cliente.sequence_number, 1)

        peer2 = self.peer()
        cliente.host, cliente.port = HOST, peer2.addr[1]
        hilo2 = Hilo(cliente.connect)
        hilo2.start()

        syn2, addr2 = peer2.recv()
        self.assertEqual(
            syn2["seq"],
            1,
            "el ISN del segundo SYN no se reinicia"
        )
        self._responder_syn_ack(peer2, addr2, client_isn=syn2["seq"])
        peer2.recv()
        hilo2.resultado_o_error()

        self.assertIsNot(cliente.sock, primero)
        self.assertFalse(
            primero.closed,
            "el socket viejo nunca se cierra (fuga)"
        )


def _puerto_libre():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind((HOST, 0))
    p = s.getsockname()[1]
    s.close()
    return p


if __name__ == "__main__":
    unittest.main()
