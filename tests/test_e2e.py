"""Cliente y servidor reales hablando entre si sobre la red simulada."""

import hashlib
import os
import random
import tempfile
import time
import unittest

from base import ConnectionClosed, Hilo, SWTestCase, packet, bt, listener, sw
from netsim import (DROP, PASS, drop_acks_nth, drop_data_nth, dup_nth,
                    es_ack_de, lossy, salvo)

MAX = sw.MAX_PAYLOAD_SIZE


class TestTransferencia(SWTestCase):

    def _upload(self, datos, policy_cliente=None, policy_servidor=None):
        cliente, conexion = self.conectados(policy_cliente, policy_servidor)
        receptor = Hilo(conexion.recv)
        receptor.start()
        cliente.send(datos)
        return receptor.resultado_o_error(10.0), cliente, conexion

    # -- camino feliz ------------------------------------------------------

    def test_handshake_deja_a_ambos_lados_sincronizados(self):
        cliente, conexion = self.conectados()
        self.assertFalse(cliente.is_closed)
        self.assertFalse(conexion.is_closed)
        self.assertEqual(cliente.sequence_number, conexion.exp_sequence_number)
        self.assertEqual(conexion.sequence_number, cliente.exp_sequence_number)
        self.assertEqual(cliente.remote_address, conexion.sock.getsockname())
        self.assertEqual(conexion.remote_address[1],
                         cliente.sock.getsockname()[1])

    def test_mensaje_de_un_solo_paquete(self):
        recibido, cliente, conexion = self._upload(b"hola servidor")
        self.assertEqual(recibido, b"hola servidor")
        self.assertEqual(cliente.sequence_number, conexion.exp_sequence_number)

    def test_mensaje_vacio(self):
        recibido, _, _ = self._upload(b"")
        self.assertEqual(recibido, b"")

    def test_mensaje_de_exactamente_un_mtu(self):
        datos = os.urandom(MAX)
        recibido, _, _ = self._upload(datos)
        self.assertEqual(recibido, datos)

    def test_mensaje_multichunk_binario(self):
        datos = os.urandom(MAX * 5 + 123)
        recibido, _, _ = self._upload(datos)
        self.assertEqual(recibido, datos)
        self.assertEqual(hashlib.sha256(recibido).hexdigest(),
                         hashlib.sha256(datos).hexdigest())

    def test_dos_mensajes_seguidos_sobre_la_misma_conexion(self):
        cliente, conexion = self.conectados()
        for datos in (b"primero", b"segundo mas largo", b""):
            with self.subTest(n=len(datos)):
                receptor = Hilo(conexion.recv)
                receptor.start()
                cliente.send(datos)
                self.assertEqual(receptor.resultado_o_error(10.0), datos)

    def test_transferencia_en_los_dos_sentidos(self):
        cliente, conexion = self.conectados()

        receptor = Hilo(conexion.recv)
        receptor.start()
        cliente.send(b"DOWNLOAD archivo.txt")
        self.assertEqual(receptor.resultado_o_error(10.0), b"DOWNLOAD archivo.txt")

        respuesta = os.urandom(MAX * 2 + 40)
        receptor2 = Hilo(cliente.recv)
        receptor2.start()
        conexion.send(respuesta)
        self.assertEqual(receptor2.resultado_o_error(10.0), respuesta)

    # -- perdidas deterministas -------------------------------------------

    def test_pierde_el_primer_paquete_de_datos(self):
        datos = os.urandom(MAX * 3)
        recibido, cliente, _ = self._upload(datos, policy_cliente=drop_data_nth(1))
        self.assertEqual(recibido, datos)
        self.assertGreater(cliente.sock.dropped, 0)

    def test_pierde_un_paquete_intermedio(self):
        datos = os.urandom(MAX * 4)
        recibido, _, _ = self._upload(datos, policy_cliente=drop_data_nth(3))
        self.assertEqual(recibido, datos)

    def test_pierde_varios_paquetes_de_datos_del_mismo_chunk(self):
        datos = b"critico" * 100
        recibido, _, _ = self._upload(datos, policy_cliente=drop_data_nth(1, 2, 3))
        self.assertEqual(recibido, datos)

    def test_pierde_un_ack_intermedio_y_el_receptor_deduplica(self):
        datos = os.urandom(MAX * 3)
        recibido, _, conexion = self._upload(datos, policy_servidor=drop_acks_nth(2))
        self.assertEqual(recibido, datos, "el duplicado no contamino el buffer")
        self.assertGreater(conexion.sock.dropped, 0)

    def test_pierde_el_primer_ack(self):
        datos = b"x" * (MAX + 10)
        recibido, _, _ = self._upload(datos, policy_servidor=drop_acks_nth(1))
        self.assertEqual(recibido, datos)

    def test_duplicacion_de_paquetes_de_datos(self):
        datos = os.urandom(MAX * 2 + 9)
        recibido, _, _ = self._upload(datos, policy_cliente=lambda c: "dup")
        self.assertEqual(recibido, datos, "los duplicados se descartan")

    def test_duplicacion_de_acks(self):
        datos = os.urandom(MAX * 2 + 9)
        recibido, _, _ = self._upload(datos, policy_servidor=lambda c: "dup")
        self.assertEqual(recibido, datos)

    def test_retardo_que_reordena_respecto_de_la_retransmision(self):
        """Un paquete demorado llega despues de su retransmision: duplicado tardio."""
        datos = b"reordenado" * 50
        n = {"i": 0}

        def politica(ctx):
            n["i"] += 1
            return ("delay", self.timeout * 1.5) if n["i"] == 1 else PASS

        recibido, _, _ = self._upload(datos, policy_cliente=politica)
        self.assertEqual(recibido, datos)

    def test_perdida_total_sostenida_termina_en_connectionclosed(self):
        cliente, conexion = self.conectados(policy_cliente=lambda c: DROP)
        receptor = Hilo(conexion.recv)
        receptor.start()
        with self.assertRaises(ConnectionClosed):
            cliente.send(b"esto no llega nunca")
        conexion.shutdown()


class TestPerdidaAleatoria(SWTestCase):
    """Integridad de datos bajo perdida aleatoria reproducible.

    Estas pruebas EXIMEN de la perdida al ACK del ultimo chunk (el del FIN).
    No es una comodidad: es que ese ACK es irrecuperable por diseno (ver
    TestHallazgosDeProtocolo.test_perder_el_ack_del_fin_...), asi que
    dejarlo caer haria fallar ~1 de cada 5 corridas por un motivo que ya
    esta cubierto aparte. Lo que se mide aca es el camino de datos.

    Tambien se usa un presupuesto de reintentos holgado (15) porque el
    protocolo no tiene backoff: con RETRIES=10 fijos y perdida alta, la
    probabilidad de agotarlos crece con la cantidad de chunks.
    """

    retries = 15

    def _upload(self, datos, policy_cliente=None, policy_servidor=None):
        cliente, conexion = self.conectados()
        cliente.sock.policy = policy_cliente
        if policy_servidor is not None:
            # el ACK final no se pierde: ver docstring de la clase
            final = cliente.sequence_number + max(len(datos), 1)
            conexion.sock.policy = salvo(es_ack_de(final), policy_servidor)
        receptor = Hilo(conexion.recv)
        receptor.start()
        cliente.send(datos)
        return receptor.resultado_o_error(20.0), cliente, conexion

    def test_integridad_con_20_por_ciento_de_perdida(self):
        rng = random.Random(1234)
        datos = os.urandom(MAX * 8 + 77)
        recibido, cliente, conexion = self._upload(
            datos,
            policy_cliente=lossy(0.2, rng),
            policy_servidor=lossy(0.2, rng),
        )
        self.assertEqual(recibido, datos)
        self.assertGreater(cliente.sock.dropped + conexion.sock.dropped, 0)

    def test_integridad_con_perdida_alta_en_un_archivo_grande(self):
        rng = random.Random(20260927)
        datos = bytes(rng.getrandbits(8) for _ in range(40_000))   # ~29 chunks
        recibido, _, _ = self._upload(
            datos,
            policy_cliente=lossy(0.15, rng),
            policy_servidor=lossy(0.15, rng),
        )
        self.assertEqual(hashlib.sha256(recibido).hexdigest(),
                         hashlib.sha256(datos).hexdigest())
        self.assertEqual(len(recibido), len(datos))

    def test_integridad_con_perdida_y_duplicacion_combinadas(self):
        rng = random.Random(99)

        def caotica(ctx):
            r = rng.random()
            if r < 0.15:
                return DROP
            if r < 0.30:
                return "dup"
            return PASS

        datos = os.urandom(MAX * 6 + 11)
        recibido, _, _ = self._upload(datos, policy_cliente=caotica,
                                      policy_servidor=caotica)
        self.assertEqual(recibido, datos)


class TestHallazgosDeProtocolo(SWTestCase):
    """Defectos que siguen sin arreglar; el test prueba que son reproducibles."""

    def test_perder_el_ack_del_fin_hace_fallar_un_upload_exitoso(self):
        """HALLAZGO (sin arreglar): falso negativo al cerrar la transferencia.

        El receptor retorna de recv() apenas ve el FIN y su ACK se pierde.
        El emisor retransmite hasta agotar RETRIES y levanta ConnectionClosed,
        aunque el archivo llego completo. Con los valores de produccion
        (TIMEOUT=1.0, RETRIES=10) el cliente ademas se cuelga 10 segundos y
        despues imprime 'La transferencia fue cancelada'.
        """
        datos = b"archivo completo" * 40
        cliente, conexion = self.conectados()

        # cae el ACK del unico/ultimo chunk
        conexion.sock.policy = drop_acks_nth(1)

        receptor = Hilo(conexion.recv)
        receptor.start()

        t0 = time.monotonic()
        with self.assertRaises(ConnectionClosed) as cm:
            cliente.send(datos)
        transcurrido = time.monotonic() - t0

        self.assertEqual(
            receptor.resultado_o_error(5.0), datos,
            "el servidor SI recibio el archivo completo",
        )
        self.assertIn("Connexion lost", str(cm.exception))
        self.assertGreaterEqual(transcurrido, self.timeout * self.retries * 0.8)

    def test_download_termina_aunque_se_pierda_el_ack_del_pedido(self):
        """Antes era un HALLAZGO: livelock a maxima velocidad, sin timeout.

        Secuencia: el cliente manda 'DOWNLOAD x', el servidor lo recibe y su
        ACK se pierde. El servidor pasa a send() con el archivo; el cliente
        sigue en send() reintentando el pedido. Antes cada uno ignoraba los
        datos del otro sin consumir reintentos. Ahora el servidor, que ya
        entrego el pedido, lo reconfirma: el cliente termina su send() y pasa
        a recv(). Mas casos en test_cambio_de_sentido.py.
        """
        cliente, conexion = self.conectados()
        conexion.sock.policy = drop_acks_nth(1)      # se pierde el ACK del pedido
        archivo = b"contenido del archivo" * 20

        def servidor():
            pedido = conexion.recv()
            conexion.send(archivo)
            return pedido

        hilo_srv = Hilo(servidor)
        hilo_srv.start()
        cliente.send(b"DOWNLOAD archivo.txt")

        self.assertEqual(cliente.recv(), archivo)
        self.assertEqual(hilo_srv.resultado_o_error(5.0), b"DOWNLOAD archivo.txt")
        self.assertLess(cliente.sock.tx, self.retries * 5, "sin inundar la red")
        self.assertLess(conexion.sock.tx, self.retries * 5)

    def test_un_syn_tardio_no_confunde_a_una_conexion_ya_establecida(self):
        """El socket efimero ignora los SYN: solo el de escucha los ve."""
        cliente, conexion = self.conectados()
        intruso = self.peer()
        intruso.send_pkt(conexion.sock.getsockname(), flags=packet.SYN_MASK,
                         sequence_number=0)

        receptor = Hilo(conexion.recv)
        receptor.start()
        cliente.send(b"sigue funcionando")
        self.assertEqual(receptor.resultado_o_error(5.0), b"sigue funcionando")


class TestCierre(SWTestCase):

    def test_close_suelta_el_socket_y_es_idempotente(self):
        cliente, conexion = self.conectados()
        sock = cliente.sock

        cliente.close()
        self.assertTrue(cliente.is_closed)
        self.assertTrue(sock.closed)
        self.assertIsNone(cliente.sock, "close() suelta la referencia")

        cliente.close()              # no debe explotar
        cliente.shutdown()           # tampoco, y no puede avisar nada ya

    def test_close_no_avisa_nada_aunque_shutdown_si(self):
        """La diferencia entre los dos cierres, lado a lado."""
        cliente, conexion = self.conectados()
        sock_close = cliente.sock
        antes = sock_close.tx
        cliente.close()
        self.assertEqual(sock_close.tx, antes,
                         "close() es el final normal: no manda avisos")

        otro, _ = self.conectados()
        sock_shutdown = otro.sock
        antes = sock_shutdown.tx
        otro.shutdown()
        self.assertEqual(sock_shutdown.tx, antes + bt.ABORT_NOTICES,
                         f"shutdown() manda {bt.ABORT_NOTICES} avisos{self.volcado()}")

    def test_shutdown_no_avisa_dos_veces(self):
        """El mismo transporte pasa por shutdown() y por close() en server.py."""
        cliente, conexion = self.conectados()
        sock = cliente.sock

        cliente.shutdown()
        despues = sock.tx
        cliente.shutdown()
        cliente.close()
        self.assertEqual(sock.tx, despues, "el segundo cierre no manda nada")

    def test_el_socket_de_escucha_no_le_avisa_a_nadie(self):
        """Su remote_address apunta a si mismo: un aviso iria contra el propio socket."""
        servidor = self.servidor()
        self.assertIsInstance(servidor, listener.Listener)
        sock = servidor.sock
        antes = sock.tx

        servidor.shutdown()
        self.assertEqual(sock.tx, antes, "el socket de escucha no manda avisos")
        self.assertTrue(servidor.is_closed)
        self.assertTrue(sock.closed)

    def test_close_sin_socket_no_explota(self):
        t = sw.StopAndWait("127.0.0.1", 1)
        t.close()
        self.assertTrue(t.is_closed)

    def test_no_hay_intercambio_de_fin_al_cerrar(self):
        """OBSERVACION: close() no manda nada al otro extremo.

        El flag FIN solo marca el ultimo chunk de un send(). No existe un
        cierre de conexion negociado: tras un close() el par se entera solo
        por timeout. El que si avisa es shutdown(), con ERR+CANCEL.
        """
        cliente, conexion = self.conectados()
        antes = conexion.sock.tx
        cliente.close()
        time.sleep(0.05)
        self.assertEqual(conexion.sock.tx, antes)
        self.assertFalse(conexion.is_closed, "el servidor no se entera del cierre")

    def test_enviar_despues_de_cerrar_falla(self):
        cliente, conexion = self.conectados()
        cliente.close()
        with self.assertRaises(ConnectionClosed):
            cliente.send(b"x")
        with self.assertRaises(ConnectionClosed):
            cliente.recv()


if __name__ == "__main__":
    unittest.main()
