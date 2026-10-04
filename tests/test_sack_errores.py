"""Selective ACK: errores remotos, trafico ajeno, datagramas invalidos,
silencio, bordes del receptor y cierre.

Casi todo contra un peer crudo (SACKTestCase.transporte_a_mano) para controlar
exactamente que llega y que sale.
"""

import time
import unittest

from base import (
    ConnectionClosed,
    Hilo,
    SACKTestCase,
    TransferCancelled,
    bt,
    packet,
    sack,
    )

PROTO = sack.SelectiveAck.PROTOCOL_ID


class ConPeer(SACKTestCase):

    def mandar(self, peer, t, **kwargs):
        """Paquete RDT del peer hacia el transporte `t`."""
        kwargs.setdefault("protocol", PROTO)
        peer.send_pkt(t.sock.getsockname(), **kwargs)

    def dato(self, peer, t, seq, payload, fin=False):
        flags = packet.ACK_MASK | (packet.FIN_MASK if fin else 0)
        self.mandar(peer, t, flags=flags, sequence_number=seq, payload=payload)

    def ack(self, peer, t, ack, bloques=()):
        self.mandar(
            peer,
            t,
            flags=packet.ACK_MASK,
            sequence_number=1,
            ack=ack,
            options=sack.make_sack_option(list(bloques)),
            )

    def recibir_en_hilo(self, t):
        hilo = Hilo(t.recv)
        hilo.start()
        time.sleep(0.02)
        return hilo

    def bloques(self, p):
        return sack.parse_sack_option(packet.get_header_options(p["raw"]))


class TestErrorRemoto(ConPeer):

    def test_err_durante_send(self):
        t, peer = self.transporte_a_mano(seq=1000)
        enviando = Hilo(t.send, b"x" * 10)
        enviando.start()
        peer.recv()
        self.mandar(peer, t, flags=packet.ERR_MASK, sequence_number=1)
        with self.assertRaises(ConnectionClosed) as cm:
            enviando.resultado_o_error()
        self.assertNotIsInstance(cm.exception, TransferCancelled)
        self.assertIn("ERR", str(cm.exception))

    def test_err_durante_recv(self):
        t, peer = self.transporte_a_mano(exp=2000)
        recibiendo = self.recibir_en_hilo(t)
        self.mandar(peer, t, flags=packet.ERR_MASK, sequence_number=2000)
        with self.assertRaises(ConnectionClosed) as cm:
            recibiendo.resultado_o_error()
        self.assertNotIsInstance(cm.exception, TransferCancelled)
        self.assertIn("ERR", str(cm.exception))

    def test_err_con_ack_durante_send_tambien_corta(self):
        """El ERR se mira antes que el ACK."""
        t, peer = self.transporte_a_mano(seq=1000)
        enviando = Hilo(t.send, b"x" * 10)
        enviando.start()
        peer.recv()
        self.mandar(
            peer,
            t,
            flags=packet.ERR_MASK | packet.ACK_MASK,
            sequence_number=1,
            ack=1011,
            )
        with self.assertRaises(ConnectionClosed):
            enviando.resultado_o_error()


class TestTraficoAjeno(ConPeer):

    def test_recv_ignora_datos_de_un_tercero(self):
        t, peer = self.transporte_a_mano(exp=2000)
        intruso = self.peer()
        recibiendo = self.recibir_en_hilo(t)

        self.dato(intruso, t, 2000, b"falso", fin=True)
        self.assertIsNone(
            intruso.try_recv(0.1)[0],
            "al tercero no se le contesta"
        )
        self.dato(peer, t, 2000, b"bueno", fin=True)
        self.assertEqual(recibiendo.resultado_o_error(), b"bueno")

    def test_send_ignora_acks_de_un_tercero(self):
        t, peer = self.transporte_a_mano(seq=1000)
        intruso = self.peer()
        enviando = Hilo(t.send, b"x" * 10)
        enviando.start()
        peer.recv()

        self.ack(intruso, t, 1011)
        time.sleep(0.02)
        self.assertTrue(
            enviando.is_alive(),
            "el ACK del tercero no confirma nada"
        )
        self.ack(peer, t, 1011)
        enviando.resultado_o_error()

    def test_send_ignora_paquetes_sin_ack(self):
        t, peer = self.transporte_a_mano(seq=1000)
        enviando = Hilo(t.send, b"x" * 10)
        enviando.start()
        peer.recv()

        self.mandar(peer, t, flags=0, sequence_number=1, ack=1011)
        time.sleep(0.02)
        self.assertTrue(enviando.is_alive())
        self.ack(peer, t, 1011)
        enviando.resultado_o_error()


# Datagramas que no se pueden interpretar como paquete RDT: mas cortos que el
# header, o con un hlen que no cierra con el largo real.
BASURA = (
    b"",
    b"\x00",
    b"\x12\x10",  # 2 bytes, con el flag ACK
    bytes([0x12, packet.ACK_MASK, 40]) + b"\0" * 9,  # hlen=40 en 12 bytes
    bytes([0x12, packet.ACK_MASK, 5]) + b"\0" * 9,  # hlen menor que el header
    )


class TestDatagramasInvalidos(ConPeer):
    """Se descartan sin romper nada, igual que en SW (packet.is_valid)."""

    def test_recv_los_descarta_y_sigue(self):
        t, peer = self.transporte_a_mano(exp=2000)
        recibiendo = self.recibir_en_hilo(t)
        for basura in BASURA:
            peer.send(basura, t.sock.getsockname())
        self.assertIsNone(
            peer.try_recv(0.1)[0],
            "a la basura no se le contesta"
        )
        self.dato(peer, t, 2000, b"sigue vivo", fin=True)
        self.assertEqual(recibiendo.resultado_o_error(), b"sigue vivo")

    def test_send_los_descarta_y_sigue(self):
        t, peer = self.transporte_a_mano(seq=1000)
        enviando = Hilo(t.send, b"x" * 10)
        enviando.start()
        peer.recv()
        for basura in BASURA:
            peer.send(basura, t.sock.getsockname())
        time.sleep(0.02)
        self.assertTrue(enviando.is_alive(), "la basura no confirma nada")
        self.ack(peer, t, 1011)
        enviando.resultado_o_error()


class TestReceptorSinRespuesta(ConPeer):
    """recv() se rinde si el otro extremo no manda nada valido por un rato.

    El limite es timeout * (max_retries + 2): lo que tarda el emisor en agotar
    los reintentos de un segmento, mas un timeout de margen. Mientras el emisor
    siga reintentando, cada reenvio le llega y reinicia la cuenta.

    La cuenta arranca con el primer segmento de datos: antes de eso el emisor
    puede estar leyendo un archivo grande a memoria, y eso no es silencio.
    """

    MARGEN = 1.0  # holgura del scheduler para el limite superior
    # Mas reintentos que el resto para estirar el limite a 0.6 s: los tests que
    # mantienen viva la conexion mandan cada limite/6, y asi una demora del
    # scheduler de ~0.5 s en el hilo del test no la deja morir por error.
    retries = 10

    def limite(self):
        return self.timeout * (self.retries + 2)

    def test_se_rinde_tras_el_silencio(self):
        t, peer = self.transporte_a_mano(exp=2000)
        recibiendo = self.recibir_en_hilo(t)
        inicio = time.monotonic()
        self.dato(peer, t, 2000, b"a")
        with self.assertRaises(ConnectionClosed) as cm:
            recibiendo.resultado_o_error(self.limite() + self.MARGEN + 1)
        transcurrido = time.monotonic() - inicio
        self.assertNotIsInstance(cm.exception, TransferCancelled)
        self.assertGreaterEqual(
            transcurrido, self.limite() * 0.95, "no antes del limite"
            )
        self.assertLessEqual(transcurrido, self.limite() + self.MARGEN)

    def test_antes_del_primer_dato_espera_sin_limite(self):
        """El emisor tarda en arrancar (archivo grande a memoria): no se rinde.
        """
        t, peer = self.transporte_a_mano(exp=2000)
        recibiendo = self.recibir_en_hilo(t)
        time.sleep(self.limite() * 2.5)
        self.assertTrue(
            recibiendo.is_alive(),
            f"se rindio sin datos todavia: {recibiendo.error!r}"
        )
        self.dato(peer, t, 2000, b"ab", fin=True)
        self.assertEqual(recibiendo.resultado_o_error(), b"ab")

    def test_un_syn_ack_repetido_no_arranca_la_cuenta(self):
        """Solo un segmento de datos arma el limite, no el SYN-ACK del
        handshake.
        """
        t, peer = self.transporte_a_mano(exp=2000)
        recibiendo = self.recibir_en_hilo(t)
        self.mandar(
            peer,
            t,
            flags=packet.SYN_MASK | packet.ACK_MASK,
            sequence_number=1999,
            ack=1000,
            )
        time.sleep(self.limite() * 2.5)
        self.assertTrue(
            recibiendo.is_alive(),
            f"se rindio sin datos todavia: {recibiendo.error!r}"
        )
        self.dato(peer, t, 2000, b"ab", fin=True)
        self.assertEqual(recibiendo.resultado_o_error(), b"ab")

    def test_cada_dato_reinicia_la_cuenta(self):
        """Con datos llegando cada medio limite, sigue vivo mucho mas que un
        limite.
        """
        t, peer = self.transporte_a_mano(exp=2000)
        recibiendo = self.recibir_en_hilo(t)
        seq = 2000
        fin = time.monotonic() + self.limite() * 2.5
        while time.monotonic() < fin:
            self.dato(peer, t, seq, b"z")
            seq += 1
            time.sleep(self.limite() / 6)
            self.assertTrue(
                recibiendo.is_alive(),
                f"se rindio con datos llegando: {recibiendo.error!r}",
                )
        self.dato(peer, t, seq, b"z", fin=True)
        self.assertEqual(len(recibiendo.resultado_o_error()), seq - 2000 + 1)

    def test_los_duplicados_tambien_reinician_la_cuenta(self):
        """Un reenvio de algo ya entregado prueba que el emisor sigue vivo."""
        t, peer = self.transporte_a_mano(exp=2000)
        recibiendo = self.recibir_en_hilo(t)
        self.dato(peer, t, 2000, b"a")
        fin = time.monotonic() + self.limite() * 2.5
        while time.monotonic() < fin:
            self.dato(peer, t, 2000, b"a")
            time.sleep(self.limite() / 6)
            self.assertTrue(
                recibiendo.is_alive(),
                f"se rindio con duplicados llegando: {recibiendo.error!r}",
                )
        self.dato(peer, t, 2001, b"b", fin=True)
        self.assertEqual(recibiendo.resultado_o_error(), b"ab")

    def test_basura_y_terceros_no_reinician_la_cuenta(self):
        t, peer = self.transporte_a_mano(exp=2000)
        intruso = self.peer()
        recibiendo = self.recibir_en_hilo(t)
        inicio = time.monotonic()
        self.dato(peer, t, 2000, b"a")  # arma el limite
        while (
            recibiendo.is_alive() and
            time.monotonic() - inicio < self.limite() * 3
        ):
            peer.send(b"\x00", t.sock.getsockname())
            self.dato(intruso, t, 2000, b"intruso")
            time.sleep(self.timeout / 4)
        with self.assertRaises(ConnectionClosed):
            recibiendo.resultado_o_error()
        self.assertLessEqual(
            time.monotonic() - inicio,
            self.limite() + self.MARGEN
        )


class TestBordesDelReceptor(ConPeer):

    def test_contesta_cada_dato_con_un_ack(self):
        t, peer = self.transporte_a_mano(exp=2000)
        recibiendo = self.recibir_en_hilo(t)
        self.dato(peer, t, 2000, b"abc")
        p, _ = peer.recv()
        self.assertPaquete(p, ACK=1, ack=2003, protocol=PROTO, payload=b"")
        self.dato(peer, t, 2003, b"d", fin=True)
        self.assertEqual(recibiendo.resultado_o_error(), b"abcd")

    def test_el_ack_anuncia_los_bloques_fuera_de_orden(self):
        t, peer = self.transporte_a_mano(exp=2000)
        recibiendo = self.recibir_en_hilo(t)
        self.dato(peer, t, 2010, b"x" * 10)
        self.dato(peer, t, 2030, b"y" * 10)
        primero, _ = peer.recv()
        segundo, _ = peer.recv()
        self.assertPaquete(segundo, ack=2000)
        self.assertEqual(self.bloques(primero), [(2010, 2020)])
        self.assertEqual(self.bloques(segundo), [(2010, 2020), (2030, 2040)])
        t.close()
        recibiendo.esperar(1.0)

    def test_fin_que_llega_antes_que_el_hueco(self):
        """El FIN bufferado no termina la recepcion hasta que se llena el
        hueco.
        """
        t, peer = self.transporte_a_mano(exp=2000)
        recibiendo = self.recibir_en_hilo(t)
        self.dato(peer, t, 2005, b"mundo", fin=True)
        time.sleep(0.05)
        self.assertTrue(recibiendo.is_alive(), "falta 2000..2005")
        self.dato(peer, t, 2000, b"hola ")
        self.assertEqual(recibiendo.resultado_o_error(), b"hola mundo")

    def test_fin_vacio(self):
        t, peer = self.transporte_a_mano(exp=2000)
        recibiendo = self.recibir_en_hilo(t)
        self.mandar(
            peer,
            t,
            flags=packet.FIN_MASK | packet.ACK_MASK,
            sequence_number=2000
        )
        self.assertEqual(recibiendo.resultado_o_error(), b"")
        p, _ = peer.recv()
        self.assertPaquete(p, ack=2001)

    def test_un_dato_fuera_de_la_ventana_se_descarta_pero_se_contesta(self):
        t, peer = self.transporte_a_mano(exp=2000)
        recibiendo = self.recibir_en_hilo(t)
        self.dato(peer, t, 2000 + sack.RWIND, b"lejos")
        p, _ = peer.recv()
        self.assertPaquete(p, ack=2000)
        self.assertEqual(self.bloques(p), [], "no se guardo")
        self.dato(peer, t, 2000, b"ok", fin=True)
        self.assertEqual(recibiendo.resultado_o_error(), b"ok")

    def test_syn_ack_retransmitido_se_contesta_con_el_ack(self):
        """Se perdio el ACK final del handshake: el servidor reenvia el
        SYN-ACK.
        """
        t, peer = self.transporte_a_mano(seq=1000, exp=2000)
        recibiendo = self.recibir_en_hilo(t)
        self.mandar(
            peer,
            t,
            flags=packet.SYN_MASK | packet.ACK_MASK,
            sequence_number=1999,
            ack=1000,
            )
        p, _ = peer.recv()
        self.assertPaquete(p, ACK=1, SYN=0, ack=2000)
        t.close()
        recibiendo.esperar(1.0)

    def test_un_ack_puro_se_ignora(self):
        t, peer = self.transporte_a_mano(exp=2000)
        recibiendo = self.recibir_en_hilo(t)
        self.ack(peer, t, 1000)
        self.assertIsNone(peer.try_recv(0.1)[0], "un ACK no se contesta")
        self.assertTrue(recibiendo.is_alive())
        t.close()
        recibiendo.esperar(1.0)

    def test_duplicado_tras_entregar_se_vuelve_a_ackear(self):
        t, peer = self.transporte_a_mano(exp=2000)
        recibiendo = self.recibir_en_hilo(t)
        self.dato(peer, t, 2000, b"abc")
        peer.recv()
        self.dato(peer, t, 2000, b"abc")
        p, _ = peer.recv()
        self.assertPaquete(p, ack=2003)
        self.dato(peer, t, 2003, b"", fin=True)
        self.assertEqual(recibiendo.resultado_o_error(), b"abc")


class TestEstadoCerrado(ConPeer):

    def test_send_sobre_conexion_cerrada(self):
        t, _ = self.transporte_a_mano()
        t.close()
        with self.assertRaises(ConnectionClosed):
            t.send(b"x")

    def test_recv_sobre_conexion_cerrada(self):
        t, _ = self.transporte_a_mano()
        t.close()
        with self.assertRaises(ConnectionClosed):
            t.recv()

    def test_send_y_recv_sin_conectar(self):
        t = sack.SelectiveAck("127.0.0.1", 1)
        with self.assertRaises(ConnectionClosed):
            t.send(b"x")
        with self.assertRaises(ConnectionClosed):
            t.recv()

    def test_shutdown_durante_recv_da_connectionclosed(self):
        t, _ = self.transporte_a_mano(exp=2000)
        recibiendo = self.recibir_en_hilo(t)
        t.shutdown()
        with self.assertRaises(ConnectionClosed):
            recibiendo.resultado_o_error()

    def test_shutdown_durante_send_da_connectionclosed(self):
        t, peer = self.transporte_a_mano(seq=1000)
        enviando = Hilo(t.send, b"x" * 5000)
        enviando.start()
        peer.recv()
        time.sleep(0.02)
        t.shutdown()
        with self.assertRaises(ConnectionClosed):
            enviando.resultado_o_error()

    def test_shutdown_avisa_al_otro_extremo(self):
        """Heredado de BaseTransport: ERR+CANCEL x ABORT_NOTICES con el
        protocolo de SACK.
        """
        t, peer = self.transporte_a_mano(seq=1000, exp=2000)
        t.shutdown()
        avisos = peer.drain()
        self.assertEqual(len(avisos), bt.ABORT_NOTICES)
        for p in avisos:
            self.assertPaquete(
                p,
                ERR=1,
                CANCEL=1,
                protocol=PROTO,
                seq=1000,
                ack=2000
            )

    def test_recv_con_is_closed_y_el_socket_abierto(self):
        """Rama del timeout: alguien marco is_closed sin cerrar el socket."""
        t, _ = self.transporte_a_mano(exp=2000)
        recibiendo = self.recibir_en_hilo(t)
        t.is_closed = True
        with self.assertRaises(ConnectionClosed):
            recibiendo.resultado_o_error()

    def test_recv_con_el_socket_roto_y_sin_is_closed_propaga_el_oserror(self):
        t, _ = self.transporte_a_mano(exp=2000)
        recibiendo = self.recibir_en_hilo(t)
        t.sock.close()
        with self.assertRaises(OSError) as cm:
            recibiendo.resultado_o_error()
        self.assertNotIsInstance(cm.exception, ConnectionClosed)

    def test_send_con_el_socket_roto_y_sin_is_closed_propaga_el_oserror(self):
        t, peer = self.transporte_a_mano(seq=1000)
        enviando = Hilo(t.send, b"x" * 10)
        enviando.start()
        peer.recv()
        t.sock.close()
        with self.assertRaises(OSError) as cm:
            enviando.resultado_o_error()
        self.assertNotIsInstance(cm.exception, ConnectionClosed)


if __name__ == "__main__":
    unittest.main()
