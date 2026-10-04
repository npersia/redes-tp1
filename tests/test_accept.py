"""Handshake del lado SERVIDOR: start_server() y accept()."""

import time
import unittest

from base import HOST, ConnectionClosed, Hilo, SWTestCase, packet, listener, sw
from netsim import drop_nth


class TestAccept(SWTestCase):

    def _servidor_escuchando(self):
        servidor = self.servidor()
        puerto = servidor.sock.getsockname()[1]
        hilo = Hilo(self.aceptar, servidor)
        hilo.start()
        return servidor, puerto, hilo

    # -- camino feliz ------------------------------------------------------

    def test_start_server_bindea_y_abre(self):
        servidor = self.servidor()
        self.assertFalse(servidor.is_closed)
        self.assertEqual(servidor.sock.getsockname()[0], HOST)
        self.assertGreater(servidor.sock.getsockname()[1], 0)

    def test_accept_sin_start_server_falla(self):
        s = listener.Listener(HOST, 9999)
        with self.assertRaises(RuntimeError):
            s.accept()

    def test_handshake_completo_devuelve_una_conexion_nueva(self):
        servidor, puerto, hilo = self._servidor_escuchando()
        peer = self.peer()

        peer.send_pkt((HOST, puerto), flags=packet.SYN_MASK, sequence_number=42)

        syn_ack, addr_srv = peer.recv()
        self.assertPaquete(syn_ack, SYN=1, ACK=1, seq=0, ack=43)
        self.assertNotEqual(
            addr_srv[1], puerto,
            "el SYN-ACK tiene que salir de un socket efimero, no del de escucha",
        )

        peer.send_pkt(addr_srv, flags=packet.ACK_MASK, sequence_number=43, ack=1)

        conexion = hilo.resultado_o_error()
        self._transportes.append(conexion)
        self.assertIsInstance(conexion, sw.StopAndWait)
        self.assertFalse(conexion.is_closed)
        self.assertEqual(conexion.sequence_number, 1, "seq = server_isn + 1")
        self.assertEqual(conexion.exp_sequence_number, 43, "exp = client_isn + 1")
        self.assertEqual(conexion.remote_address, peer.addr)
        self.assertIsNot(conexion.sock, servidor.sock)

    def test_el_socket_efimero_tiene_timeout(self):
        servidor, puerto, hilo = self._servidor_escuchando()
        peer = self.peer()
        peer.send_pkt((HOST, puerto), flags=packet.SYN_MASK, sequence_number=0)
        _, addr_srv = peer.recv()
        peer.send_pkt(addr_srv, flags=packet.ACK_MASK, sequence_number=1, ack=1)
        conexion = hilo.resultado_o_error()
        self._transportes.append(conexion)
        self.assertAlmostEqual(conexion.sock.gettimeout(), self.timeout, places=4)

    # -- eleccion de protocolo ----------------------------------------------

    def test_la_conexion_usa_el_protocolo_que_pide_el_syn(self):
        from lib.protocols.selective_ack.selective_ack import SelectiveAck

        for clase in (sw.StopAndWait, SelectiveAck):
            with self.subTest(protocolo=clase.TAG):
                servidor, puerto, hilo = self._servidor_escuchando()
                peer = self.peer()

                peer.send_pkt((HOST, puerto), flags=packet.SYN_MASK,
                              sequence_number=0, protocol=clase.PROTOCOL_ID)

                syn_ack, addr_srv = peer.recv()
                self.assertEqual(syn_ack["protocol"], clase.PROTOCOL_ID,
                                 "el SYN-ACK contesta con el protocolo del cliente")

                peer.send_pkt(addr_srv, flags=packet.ACK_MASK, sequence_number=1,
                              ack=1, protocol=clase.PROTOCOL_ID)

                conexion = hilo.resultado_o_error()
                self._transportes.append(conexion)
                self.assertIs(type(conexion), clase)

    def test_ignora_el_syn_con_un_protocolo_no_soportado(self):
        servidor, puerto, hilo = self._servidor_escuchando()
        peer = self.peer()

        # 3 es TCP, que no usa este handshake; 0 y 15 no son de nadie
        for protocolo in (0, 3, 15):
            peer.send_pkt((HOST, puerto), flags=packet.SYN_MASK,
                          sequence_number=0, protocol=protocolo)

        self.assertIsNone(peer.try_recv(0.3)[0], "no debe contestar nada")

    # -- trafico que hay que descartar -------------------------------------

    def test_ignora_paquetes_sin_syn_en_el_socket_de_escucha(self):
        servidor, puerto, hilo = self._servidor_escuchando()
        peer = self.peer()

        for flags in (0, packet.ACK_MASK, packet.FIN_MASK, packet.ERR_MASK):
            peer.send_pkt((HOST, puerto), flags=flags, sequence_number=5)

        self.assertIsNone(peer.try_recv(0.3)[0], "no debe contestar nada")
        self.assertTrue(hilo.is_alive(), "accept sigue esperando un SYN")

        peer.send_pkt((HOST, puerto), flags=packet.SYN_MASK, sequence_number=5)
        _, addr_srv = peer.recv()
        peer.send_pkt(addr_srv, flags=packet.ACK_MASK, sequence_number=6, ack=1)
        self._transportes.append(hilo.resultado_o_error())

    def test_ignora_el_ack_final_con_numero_equivocado(self):
        servidor, puerto, hilo = self._servidor_escuchando()
        peer = self.peer()
        peer.send_pkt((HOST, puerto), flags=packet.SYN_MASK, sequence_number=0)
        _, addr_srv = peer.recv()

        peer.send_pkt(addr_srv, flags=packet.ACK_MASK, sequence_number=1, ack=999)
        self.assertTrue(hilo.is_alive())

        # el servidor reintenta el SYN-ACK; esta vez contestamos bien
        syn_ack2, addr_srv2 = peer.recv(timeout=2.0)
        self.assertPaquete(syn_ack2, SYN=1, ACK=1)
        peer.send_pkt(addr_srv2, flags=packet.ACK_MASK, sequence_number=1, ack=1)
        self._transportes.append(hilo.resultado_o_error())

    def test_ignora_el_ack_final_si_trae_syn(self):
        """Un SYN retransmitido no debe confundirse con el ACK del paso 3."""
        servidor, puerto, hilo = self._servidor_escuchando()
        peer = self.peer()
        peer.send_pkt((HOST, puerto), flags=packet.SYN_MASK, sequence_number=0)
        _, addr_srv = peer.recv()

        peer.send_pkt(addr_srv, flags=packet.SYN_MASK | packet.ACK_MASK,
                      sequence_number=0, ack=1)
        self.assertTrue(hilo.is_alive(), "un paquete con SYN no cierra el handshake")

        syn_ack2, addr2 = peer.recv(timeout=2.0)
        self.assertPaquete(syn_ack2, SYN=1, ACK=1)
        peer.send_pkt(addr2, flags=packet.ACK_MASK, sequence_number=1, ack=1)
        self._transportes.append(hilo.resultado_o_error())

    def test_ignora_el_ack_que_viene_de_otra_direccion(self):
        servidor, puerto, hilo = self._servidor_escuchando()
        peer = self.peer()
        intruso = self.peer()

        peer.send_pkt((HOST, puerto), flags=packet.SYN_MASK, sequence_number=0)
        _, addr_srv = peer.recv()

        intruso.send_pkt(addr_srv, flags=packet.ACK_MASK, sequence_number=1, ack=1)
        self.assertTrue(hilo.is_alive(), "el ACK de un tercero no completa el handshake")

        peer.recv(timeout=2.0)   # retransmision del SYN-ACK
        peer.send_pkt(addr_srv, flags=packet.ACK_MASK, sequence_number=1, ack=1)
        self._transportes.append(hilo.resultado_o_error())

    # -- retransmision y timeouts -----------------------------------------

    def test_retransmite_el_syn_ack_si_se_pierde_el_ack_del_cliente(self):
        servidor, puerto, hilo = self._servidor_escuchando()
        peer = self.peer()
        peer.send_pkt((HOST, puerto), flags=packet.SYN_MASK, sequence_number=0)

        primero, addr_srv = peer.recv()
        t0 = time.monotonic()
        segundo, addr2 = peer.recv(timeout=2.0)
        self.assertGreaterEqual(time.monotonic() - t0, self.timeout * 0.8)
        self.assertEqual(primero["raw"], segundo["raw"], "la retransmision es identica")
        self.assertEqual(addr_srv, addr2, "sale siempre del mismo socket efimero")

        peer.send_pkt(addr2, flags=packet.ACK_MASK, sequence_number=1, ack=1)
        self._transportes.append(hilo.resultado_o_error())

    def test_si_el_cliente_desaparece_vuelve_a_escuchar_y_atiende_al_siguiente(self):
        servidor, puerto, hilo = self._servidor_escuchando()
        fantasma = self.peer()

        # SYN y despues silencio: el servidor agota RETRIES contra este cliente
        fantasma.send_pkt((HOST, puerto), flags=packet.SYN_MASK, sequence_number=0)
        vistos = fantasma.drain(timeout=self.timeout * 4)
        self.assertEqual(
            len(vistos), self.retries,
            f"esperaba {self.retries} SYN-ACK antes de abandonar",
        )
        self.assertTrue(hilo.is_alive(), "accept vuelve al loop externo, no muere")

        # y ahora si entra un cliente sano
        bueno = self.peer()
        bueno.send_pkt((HOST, puerto), flags=packet.SYN_MASK, sequence_number=77)
        _, addr = bueno.recv(timeout=2.0)
        bueno.send_pkt(addr, flags=packet.ACK_MASK, sequence_number=78, ack=1)
        conexion = hilo.resultado_o_error()
        self._transportes.append(conexion)
        self.assertEqual(conexion.exp_sequence_number, 78)

    def test_el_socket_efimero_del_cliente_abandonado_queda_abierto(self):
        """HALLAZGO (sin arreglar): si se agotan los reintentos, `client_sock`
        nunca se cierra.

        Cada SYN que no completa el handshake deja un descriptor colgado.
        Es un vector de agotamiento de file descriptors trivial de explotar.
        """
        servidor, puerto, hilo = self._servidor_escuchando()
        fantasma = self.peer()
        antes = len(self.net.sockets)

        fantasma.send_pkt((HOST, puerto), flags=packet.SYN_MASK, sequence_number=0)
        fantasma.drain(timeout=self.timeout * 4)

        creados = self.net.sockets[antes:]
        self.assertEqual(len(creados), 1, "se creo un socket efimero")
        self.assertFalse(
            creados[0].closed,
            "el socket efimero queda abierto tras agotar los reintentos",
        )

    def test_un_syn_duplicado_crea_una_segunda_conexion(self):
        """HALLAZGO (sin arreglar): no hay memoria de conexiones ya aceptadas.

        Un SYN retransmitido (porque se perdio el SYN-ACK) llega al socket
        de escucha despues de que la conexion ya existe, y accept() devuelve
        una SEGUNDA conexion para el mismo cliente, con su propio socket.
        """
        servidor, puerto, hilo = self._servidor_escuchando()
        peer = self.peer()

        peer.send_pkt((HOST, puerto), flags=packet.SYN_MASK, sequence_number=10)
        _, addr1 = peer.recv()
        peer.send_pkt(addr1, flags=packet.ACK_MASK, sequence_number=11, ack=1)
        conexion1 = hilo.resultado_o_error()
        self._transportes.append(conexion1)

        # el cliente creia perdido el SYN-ACK y retransmitio el SYN
        hilo2 = Hilo(self.aceptar, servidor)
        hilo2.start()
        peer.send_pkt((HOST, puerto), flags=packet.SYN_MASK, sequence_number=10)
        _, addr2 = peer.recv(timeout=2.0)
        peer.send_pkt(addr2, flags=packet.ACK_MASK, sequence_number=11, ack=1)
        conexion2 = hilo2.resultado_o_error()
        self._transportes.append(conexion2)

        self.assertNotEqual(
            conexion1.sock.getsockname(), conexion2.sock.getsockname(),
            "se abrieron dos conexiones distintas para el mismo cliente",
        )

    # -- robustez ----------------------------------------------------------

    def test_los_datagramas_truncados_se_descartan(self):
        """Antes disparaban IndexError en get_header_flags y tumbaban accept."""
        servidor, puerto, hilo = self._servidor_escuchando()
        peer = self.peer()

        for basura in (b"", b"\x00", b"\x11\x80", b"\x11\x80\x0c" + b"\x00" * 8):
            peer.send(basura, (HOST, puerto))

        self.assertIsNone(peer.try_recv(0.2)[0], "no contesta nada")
        self.assertTrue(hilo.is_alive(), "accept sobrevive a los datagramas basura")

        # y sigue funcionando
        peer.send_pkt((HOST, puerto), flags=packet.SYN_MASK, sequence_number=3)
        _, addr = peer.recv(timeout=2.0)
        peer.send_pkt(addr, flags=packet.ACK_MASK, sequence_number=4, ack=1)
        self._transportes.append(hilo.resultado_o_error())

    def test_un_hlen_que_no_cierra_se_descarta(self):
        """hlen mayor que el datagrama haria que get_payload devuelva basura."""
        servidor, puerto, hilo = self._servidor_escuchando()
        peer = self.peer()
        pkt = bytearray(packet.make_packet(flags=packet.SYN_MASK, payload=b"xy"))
        pkt[2] = 200                                  # hlen imposible
        peer.send(bytes(pkt), (HOST, puerto))

        self.assertIsNone(peer.try_recv(0.2)[0])
        self.assertTrue(hilo.is_alive())

    def test_el_socket_de_escucha_tiene_timeout(self):
        """Sin timeout, accept() bloquearia para siempre y el servidor
        no se podria apagar (ver TestDispatcher en test_integracion)."""
        servidor = self.servidor()
        self.assertAlmostEqual(servidor.sock.gettimeout(), self.timeout, places=4)

    def test_accept_devuelve_none_si_vence_el_timeout(self):
        """El llamador decide si sigue esperando (Dispatcher.start() en server.py)."""
        servidor = self.servidor()
        self.assertIsNone(servidor.accept())
        self.assertFalse(servidor.is_closed, "el timeout no cierra el servidor")

    def test_shutdown_corta_el_accept(self):
        """Con el timeout puesto, el shutdown se nota en el siguiente ciclo."""
        servidor, puerto, hilo = self._servidor_escuchando()
        time.sleep(0.05)
        servidor.shutdown()          # marca is_closed y cierra el socket

        self.assertTrue(hilo.esperar(2.0), "accept quedo colgado tras el shutdown")
        self.assertIsInstance(hilo.error, ConnectionClosed)

    def test_is_closed_frena_el_loop_de_accept(self):
        servidor, puerto, hilo = self._servidor_escuchando()
        time.sleep(0.05)
        servidor.is_closed = True                         # sin cerrar el socket

        self.assertTrue(hilo.esperar(2.0), "accept no reacciono al is_closed")
        self.assertIsInstance(hilo.error, ConnectionClosed)

    def test_accept_sobre_servidor_ya_cerrado(self):
        servidor = self.servidor()
        servidor.is_closed = True
        with self.assertRaises(ConnectionClosed):
            servidor.accept()

    def test_accept_despues_de_cerrar_es_connectionclosed(self):
        """Lo que pasa si el shutdown cae entre dos llamadas a accept().

        Es la carrera de test_shutdown_corta_el_accept sin depender del
        scheduler: close() suelta el socket (sock=None) y eso no tiene que
        confundirse con "nunca se inicio".
        """
        servidor = self.servidor()
        servidor.close()
        with self.assertRaises(ConnectionClosed):
            servidor.accept()

    def test_accept_despues_de_shutdown_es_connectionclosed(self):
        servidor = self.servidor()
        servidor.shutdown()
        with self.assertRaises(ConnectionClosed):
            servidor.accept()

    def test_sin_iniciar_sigue_siendo_runtimeerror_aunque_se_cierre(self):
        """Cerrar un listener que nunca escucho no lo convierte en 'cerrado'."""
        s = listener.Listener(HOST, 9999)
        s.close()
        with self.assertRaises(RuntimeError):
            s.accept()


if __name__ == "__main__":
    unittest.main()


class TestAcceptRamasDeError(SWTestCase):
    """Ramas de error de accept() y shutdown()."""

    def test_un_error_de_socket_con_el_servidor_cerrado_da_connectionclosed(self):
        """Rama `except Exception` + `is_closed` -> ConnectionClosed traducido."""
        servidor = self.servidor()

        hilo = Hilo(self.aceptar, servidor)
        hilo.start()
        time.sleep(0.02)

        servidor.is_closed = True
        servidor.sock.close()                 # provoca OSError en el recvfrom

        self.assertTrue(hilo.esperar(2.0))
        self.assertIsInstance(hilo.error, ConnectionClosed)
        self.assertIn("accepting a connection", str(hilo.error))

    def test_un_error_inesperado_con_el_servidor_abierto_se_propaga(self):
        """Rama `except Exception` sin is_closed: el error no se traduce."""
        servidor = self.servidor()

        hilo = Hilo(self.aceptar, servidor)
        hilo.start()
        time.sleep(0.02)

        servidor.sock.close()                 # OSError con is_closed en False

        self.assertTrue(hilo.esperar(2.0))
        self.assertIsInstance(hilo.error, OSError)
        self.assertNotIsInstance(hilo.error, ConnectionClosed)

    def test_shutdown_traga_los_errores_al_cerrar_el_socket(self):
        """shutdown() hace dos cosas (avisar y cerrar) y ninguna debe propagar."""
        class SocketRoto:
            def sendto(self, data, addr):
                raise OSError("descriptor invalido")

            def close(self):
                raise OSError("descriptor invalido")

        t = sw.StopAndWait(HOST, 1)
        t.sock = SocketRoto()
        t.shutdown()                          # no debe propagar
        self.assertTrue(t.is_closed)
        self.assertIsNone(t.sock, "shutdown() tiene que soltar el socket")
