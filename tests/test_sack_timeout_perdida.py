"""Selective ACK con timeout combinado con perdidas: los reenvios
tambien fallan."""

import os
import time
import unittest

from base import ConnectionClosed, Hilo, netsim, sack
from test_sack_timeout import Timeout

MAX = sack.MAX_PAYLOAD_SIZE


def seq_del(n):
    return 1 + n * MAX


class TestReenvioPerdido(Timeout):

    def test_doble_timeout_el_reenvio_tambien_se_pierde(self):
        ultimo = seq_del(2)
        for tiempos, trace in self.transferir_con(
            MAX * 3, emisor=lambda: netsim.drop_seq(ultimo, 2)
        ):
            self.assertEqual(len(tiempos[ultimo]), 3)
            self.assertEsperoElTimeout(tiempos, ultimo, copia=1)
            self.assertEsperoElTimeout(tiempos, ultimo, copia=2)
            self.assertEqual(trace.timeouts, 2)

    def test_fast_retransmit_perdido_y_lo_rescata_el_timeout(self):
        """Primera copia perdida -> fast retransmit; esa tambien se
        pierde -> timeout."""
        hueco = seq_del(1)
        for tiempos, trace in self.transferir_con(
            MAX * 12, emisor=lambda: netsim.drop_seq(hueco, 2)
        ):
            self.assertEqual(len(tiempos[hueco]), 3)
            self.assertEqual((trace.fast_retransmits, trace.timeouts), (1, 1))
            self.assertEsperoElTimeout(tiempos, hueco, copia=2)

    def test_rafaga_por_timeout_y_despues_un_hueco_por_fast_retransmit(self):
        perdidos = (seq_del(2), seq_del(3), seq_del(8))
        for tiempos, trace in self.transferir_con(
            MAX * 12, emisor=lambda: netsim.drop_seqs(*perdidos)
        ):
            self.assertEqual(
                {s for s, t in tiempos.items() if len(t) > 1}, set(perdidos)
            )
            self.assertEqual((trace.timeouts, trace.fast_retransmits), (2, 1))

    def test_timeout_con_perdida_de_datos_y_de_acks(self):
        """La rafaga va por timeout y encima se pierden los ACKs de
        esos reenvios."""
        for tiempos, trace in self.transferir_con(
            MAX * 8,
            emisor=lambda: netsim.drop_seqs(seq_del(2), seq_del(3)),
            receptor=lambda: netsim.drop_acks_sack_nth(5, 6),
        ):
            self.assertGreaterEqual(trace.timeouts, 2)


class TestAgotamientoPorTimeout(Timeout):

    def test_el_ultimo_se_pierde_siempre(self):
        """Cada reenvio espera un timeout: 1 + max_retries copias y
        ConnectionClosed."""
        ultimo = seq_del(2)
        cliente, conexion = self.conectados(
            policy_cliente=netsim.drop_seq(ultimo, 99)
        )
        recibiendo = Hilo(conexion.recv)
        recibiendo.start()

        inicio = time.monotonic()
        with self.assertRaises(ConnectionClosed) as cm:
            cliente.send(os.urandom(MAX * 3))
        transcurrido = time.monotonic() - inicio

        self.assertIn(f"Too many retries for seq={ultimo}", str(cm.exception))
        tiempos = self.tiempos_por_seq("cliente")
        self.assertEqual(len(tiempos[ultimo]), 1 + self.retries)
        for copia in range(1, 1 + self.retries):
            self.assertEsperoElTimeout(tiempos, ultimo, copia=copia)
        # se rinde al vencer el timer de la ultima copia, sin mandar otra
        self.assertGreaterEqual(
            transcurrido,
            self.timeout * (self.retries + 1) * 0.95
        )
        # y el receptor, que dejo de oir al emisor, tambien termina
        with self.assertRaises(ConnectionClosed):
            recibiendo.resultado_o_error(self.timeout * (self.retries + 2) + 2)


if __name__ == "__main__":
    unittest.main()
