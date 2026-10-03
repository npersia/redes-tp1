"""Selective ACK con fallos no lineales: perdidas dispersas, aleatorias y mezcladas.

Las pruebas aleatorias usan semillas fijas (reproducibles) y, como en
test_e2e de SW, EXIMEN de la perdida al ACK del FIN: ese caso es irrecuperable
por diseno y ya esta cubierto en test_sack_un_fallo.TestHallazgosDeUnFallo.
Tambien suben el presupuesto de reintentos: sin backoff, con mucha perdida y
muchos segmentos la chance de agotarlos deja de ser despreciable.
"""

import os
import random
import unittest

from base import SACK_TIMEOUT_EXACTO, SACKTestCase, netsim, sack
from netsim import DROP, PASS

MAX = sack.MAX_PAYLOAD_SIZE
SEGMENTOS = 40
DATOS = SEGMENTOS * MAX
SEMILLAS = (1, 7, 42, 1234, 99991)


def seq_del(n):
    return 1 + n * MAX


class Disperso(SACKTestCase):

    retries = 15

    def correr(self, emisor=None, receptor=None, tam=DATOS):
        resultados = []
        datos = os.urandom(tam)
        for direccion, e, r, rol_e, rol_r in self.direcciones(emisor, receptor):
            with self.subTest(direccion=direccion):
                self.net.entries.clear()
                self.assertEqual(self.transferir(e, r, datos, timeout=30), datos, self.volcado())
                self.assertVentanaRespetada(rol_e, rol_r)
                copias = self.copias_por_seq(rol_e)
                resultados.append(({s: n for s, n in copias.items() if n > 1}, e._send_trace))
        return resultados

    def menos_el_ack_final(self, politica, tam=DATOS):
        final = 1 + tam
        return netsim.salvo(netsim.es_ack_de(final), politica)


class TestPerdidasDispersas(Disperso):

    timeout = SACK_TIMEOUT_EXACTO

    def test_perdidas_aisladas_en_posiciones_salteadas(self):
        """Huecos separados: cada uno tiene segmentos detras y se rescata por fast retransmit."""
        perdidos = [seq_del(i) for i in (1, 9, 17, 25, 33)]
        for reenviados, trace in self.correr(emisor=lambda: netsim.drop_seqs(*perdidos)):
            self.assertEqual(reenviados, {s: 2 for s in perdidos})
            self.assertEqual(trace.timeouts, 0)
            self.assertEqual(trace.fast_retransmits, len(perdidos))

    def test_perdidas_en_posiciones_cuadradas(self):
        """1, 4, 9, 16, 25, 36: cada vez mas separadas."""
        perdidos = [seq_del(i * i) for i in range(1, 7)]
        for reenviados, _ in self.correr(emisor=lambda: netsim.drop_seqs(*perdidos)):
            self.assertEqual(set(reenviados), set(perdidos))

    def test_dos_huecos_dentro_de_la_misma_ventana(self):
        """Huecos no contiguos en vuelo a la vez: los bloques SACK los separan."""
        perdidos = [seq_del(4), seq_del(6)]
        for reenviados, _ in self.correr(emisor=lambda: netsim.drop_seqs(*perdidos)):
            self.assertEqual(set(reenviados), set(perdidos))
            self.assertNotIn(seq_del(5), reenviados, "el sackeado del medio no se reenvia")

    def test_datos_y_acks_perdidos_salteados(self):
        perdidos = [seq_del(i) for i in (3, 15, 28)]
        for reenviados, _ in self.correr(emisor=lambda: netsim.drop_seqs(*perdidos),
                                         receptor=lambda: netsim.drop_acks_sack_nth(5, 11, 20, 31)):
            self.assertTrue(set(perdidos) <= set(reenviados))


class TestPerdidaAleatoria(Disperso):

    def aleatoria(self, tasa_datos, tasa_acks):
        for semilla in SEMILLAS:
            with self.subTest(semilla=semilla):
                rng_d = random.Random(semilla)
                rng_a = random.Random(semilla + 1)
                self.correr(
                    emisor=lambda: netsim.lossy(tasa_datos, rng_d),
                    receptor=lambda: self.menos_el_ack_final(netsim.lossy(tasa_acks, rng_a)),
                )

    def test_10_por_ciento_de_datos(self):
        self.aleatoria(0.10, 0.0)

    def test_10_por_ciento_en_las_dos_puntas(self):
        self.aleatoria(0.10, 0.10)

    def test_30_por_ciento_en_las_dos_puntas(self):
        self.aleatoria(0.30, 0.30)


class TestMezclas(Disperso):

    def test_perdida_duplicado_y_desorden_juntos(self):
        def caos():
            return netsim.todas(
                netsim.drop_seqs(seq_del(2), seq_del(20)),
                netsim.dup_seq(seq_del(7)),
                netsim.dup_seq(seq_del(30)),
                netsim.delay_seq(seq_del(12), 0.01),
                netsim.delay_seq(seq_del(25), 0.03),
            )
        for reenviados, _ in self.correr(emisor=caos):
            self.assertTrue({seq_del(2), seq_del(20)} <= set(reenviados))

    def test_acks_duplicados_y_demorados(self):
        def acks_raros():
            rng = random.Random(5)

            def politica(ctx):
                if not netsim.es_ack_puro_sack_pkt(ctx.pkt):
                    return PASS
                x = rng.random()
                if x < 0.15:
                    return netsim.DUP
                if x < 0.25:
                    return ("delay", 0.01)
                return PASS
            return politica
        self.correr(receptor=acks_raros)

    def test_mucha_perdida_aleatoria_y_desorden(self):
        def politica_emisor():
            rng = random.Random(77)

            def politica(ctx):
                x = rng.random()
                if x < 0.2:
                    return DROP
                if x < 0.3:
                    return ("delay", rng.uniform(0.001, 0.02))
                return PASS
            return politica
        self.correr(emisor=politica_emisor)


if __name__ == "__main__":
    unittest.main()
