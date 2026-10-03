"""Selective ACK con fallos lineales: rafagas de perdidas consecutivas.

Con CWND=4 y DUP_ACKS_THRESHOLD=3, una rafaga de 2 o mas segmentos deja como
mucho 2 segmentos detras del hueco dentro de la ventana: nunca se juntan 3 ACKs
duplicados y la recuperacion es siempre por timeout, de a un hueco por vez.
Estos tests lo dejan fijado (es una limitacion de rendimiento, no un error).
"""

import os
import unittest

from base import ConnectionClosed, Hilo, SACK_TIMEOUT_EXACTO, SACKTestCase, netsim, sack

MAX = sack.MAX_PAYLOAD_SIZE
CWND = sack.CWND
SEGMENTOS = 12
DATOS = SEGMENTOS * MAX


def seq_del(n):
    return 1 + n * MAX


def seqs(desde, hasta):
    """seqs de los segmentos desde..hasta (inclusive, 0-based)."""
    return [seq_del(i) for i in range(desde, hasta + 1)]


class Rafagas(SACKTestCase):

    timeout = SACK_TIMEOUT_EXACTO

    def correr(self, emisor=None, receptor=None):
        resultados = []
        datos = os.urandom(DATOS)
        for direccion, e, r, rol_e, rol_r in self.direcciones(emisor, receptor):
            with self.subTest(direccion=direccion):
                self.net.entries.clear()
                self.assertEqual(self.transferir(e, r, datos), datos, self.volcado())
                self.assertVentanaRespetada(rol_e, rol_r)
                copias = self.copias_por_seq(rol_e)
                resultados.append(({s: n for s, n in copias.items() if n > 1}, e._send_trace))
        return resultados

    def rafaga(self, desde, hasta):
        """Corre con los segmentos desde..hasta perdidos una vez y verifica lo comun."""
        perdidos = seqs(desde, hasta)
        for reenviados, trace in self.correr(emisor=lambda: netsim.drop_seqs(*perdidos)):
            self.assertEqual(reenviados, {s: 2 for s in perdidos},
                             "cada perdido se reenvia exactamente una vez")
            self.assertEqual(trace.fast_retransmits, 0,
                             "la ventana no deja juntar 3 duplicados")
            self.assertEqual(trace.timeouts, len(perdidos))


class TestRafagasDeDatos(Rafagas):

    def test_rafaga_de_dos(self):
        self.rafaga(2, 3)

    def test_rafaga_del_tamanio_de_la_ventana(self):
        self.rafaga(2, 2 + CWND - 1)

    def test_rafaga_mas_grande_que_la_ventana(self):
        self.rafaga(2, 2 + CWND + 1)

    def test_rafaga_al_principio(self):
        self.rafaga(0, CWND - 1)

    def test_rafaga_al_final_incluye_el_fin(self):
        self.rafaga(SEGMENTOS - CWND, SEGMENTOS - 1)

    def test_se_pierde_la_primera_copia_de_todo(self):
        self.rafaga(0, SEGMENTOS - 1)


class TestElMismoSegmentoSeguido(Rafagas):

    def test_se_pierde_tres_veces_seguidas(self):
        """La retransmision tambien se pierde, y la siguiente: llega a la 4ta copia."""
        for reenviados, _ in self.correr(emisor=lambda: netsim.drop_seq(seq_del(3), 3)):
            self.assertEqual(reenviados, {seq_del(3): 4})

    def test_se_pierde_justo_hasta_el_ultimo_intento(self):
        """1 envio + max_retries reenvios: la ultima copia posible llega y alcanza."""
        n = self.retries
        for reenviados, _ in self.correr(emisor=lambda: netsim.drop_seq(seq_del(3), n)):
            self.assertEqual(reenviados, {seq_del(3): 1 + n})

    def test_se_pierde_mas_veces_que_los_reintentos(self):
        """Agotamiento: 1 envio + max_retries reenvios, y despues ConnectionClosed.

        Ojo, distinto de SW: alla RETRIES es el total de envios; aca es la
        cantidad de REenvios (salen 1 + max_retries copias).
        """
        cliente, conexion = self.conectados(policy_cliente=netsim.drop_seq(seq_del(3), 99))
        recibiendo = Hilo(conexion.recv)
        recibiendo.start()
        with self.assertRaises(ConnectionClosed) as cm:
            cliente.send(os.urandom(DATOS))
        self.assertIn(f"Too many retries for seq={seq_del(3)}", str(cm.exception))
        self.assertEqual(self.copias_por_seq("cliente")[seq_del(3)], 1 + self.retries)
        # el receptor deja de escuchar al emisor y tambien se rinde
        with self.assertRaises(ConnectionClosed):
            recibiendo.resultado_o_error(self.timeout * (self.retries + 2) + 2)


class TestRafagasDeAcks(Rafagas):

    def test_tres_acks_seguidos(self):
        """Los que siguen son acumulativos y tapan a los perdidos."""
        for reenviados, trace in self.correr(receptor=lambda: netsim.drop_acks_sack_nth(2, 3, 4)):
            self.assertEqual(reenviados, {})
            self.assertEqual(trace.retransmissions, 0)

    def test_todos_los_acks_de_la_primera_ventana(self):
        """Sin ningun ACK de la ventana el emisor no puede avanzar: vence el timer
        del primero y se reenvia; ese reenvio genera un ACK que confirma todo."""
        for reenviados, trace in self.correr(receptor=lambda: netsim.drop_acks_sack_nth(*range(1, CWND + 1))):
            self.assertEqual(reenviados, {seq_del(0): 2})
            self.assertEqual(trace.timeouts, 1)

    def test_tantos_acks_perdidos_que_se_agotan_los_reintentos(self):
        """Cada reenvio provoca un ACK y tambien se pierde: se agota el presupuesto."""
        cliente, conexion = self.conectados(
            policy_servidor=netsim.drop_acks_sack_nth(*range(3, 3 + 2 * CWND)))
        recibiendo = Hilo(conexion.recv)
        recibiendo.start()
        with self.assertRaises(ConnectionClosed) as cm:
            cliente.send(os.urandom(DATOS))
        self.assertIn("Too many retries", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
