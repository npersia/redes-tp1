"""Selective ACK cuando salta el timeout de retransmision.

El timer es por segmento pero solo se mira el del mas viejo de la ventana
(ACKSender.on_timeout); send() lo revisa cada POLL_INTERVAL. Asi que un
reenvio por timeout sale entre `timeout` y `timeout + POLL_INTERVAL` despues
del envio original.

El limite inferior se verifica estricto: es lo que garantiza el protocolo
(nunca reenvia antes de tiempo). El superior es holgado a proposito: con la
maquina cargada el SO puede tardar cientos de ms en despertar un recvfrom()
con timeout de 50 ms (medido: hasta ~1 s con load average 9 en 6 nucleos,
con un socket UDP pelado, sin SACK ni netsim). Que el reenvio fue por
timeout y no por otra cosa lo dicen los contadores del trace.
"""

import os
import unittest

from base import SACK_TIMEOUT_EXACTO, SACKTestCase, netsim, sack

MAX = sack.MAX_PAYLOAD_SIZE
CWND = sack.CWND
MARGEN = 1.0       # holgura del scheduler para el limite superior (ver docstring)


def seq_del(n):
    return 1 + n * MAX


class Timeout(SACKTestCase):

    timeout = SACK_TIMEOUT_EXACTO

    def setUp(self):
        super().setUp()
        # id(tiempos) -> trafico de esa direccion. Las verificaciones corren
        # despues de las dos direcciones, cuando el registro ya se limpio.
        self._volcados = {}

    def transferir_con(self, tam, emisor=None, receptor=None):
        resultados = []
        datos = os.urandom(tam)
        for direccion, e, r, rol_e, rol_r in self.direcciones(emisor, receptor):
            with self.subTest(direccion=direccion):
                self.net.entries.clear()
                self.assertEqual(self.transferir(e, r, datos), datos, self.volcado())
                tiempos = self.tiempos_por_seq(rol_e)
                self._volcados[id(tiempos)] = f"\n[{direccion}]{self.volcado()}"
                resultados.append((tiempos, e._send_trace))
        return resultados

    def assertEsperoElTimeout(self, tiempos, seq, copia=1):
        """La copia `copia` (1 = primer reenvio) salio un timeout despues de la anterior."""
        espera = tiempos[seq][copia] - tiempos[seq][copia - 1]
        volcado = self._volcados.get(id(tiempos)) or self.volcado()
        self.assertGreaterEqual(espera, self.timeout * 0.95,
                                f"reenvio antes del timeout ({espera * 1000:.1f}ms){volcado}")
        self.assertLessEqual(espera, self.timeout + sack.POLL_INTERVAL + MARGEN,
                             f"reenvio demasiado tarde ({espera * 1000:.1f}ms){volcado}")


class TestTimeoutPorPerdida(Timeout):

    def test_el_ultimo_se_pierde_y_vuelve_por_timeout(self):
        """Nada detras del ultimo: sin duplicados, solo el timer lo rescata."""
        ultimo = seq_del(2)
        for tiempos, trace in self.transferir_con(MAX * 3, emisor=lambda: netsim.drop_seq(ultimo)):
            self.assertEqual(len(tiempos[ultimo]), 2)
            self.assertEsperoElTimeout(tiempos, ultimo)
            self.assertEqual((trace.timeouts, trace.fast_retransmits), (1, 0))

    def test_un_hueco_con_pocos_detras_espera_el_timeout(self):
        """Solo 2 segmentos detras del hueco: 2 duplicados no alcanzan el umbral."""
        for tiempos, trace in self.transferir_con(MAX * 3, emisor=lambda: netsim.drop_seq(seq_del(0))):
            self.assertEsperoElTimeout(tiempos, seq_del(0))
            self.assertEqual({s: len(t) for s, t in tiempos.items() if len(t) > 1}, {seq_del(0): 2},
                             "solo el hueco: los sackeados no se reenvian")
            self.assertEqual(trace.timeouts, 1)

    def test_se_pierde_la_ventana_entera(self):
        perdidos = [seq_del(i) for i in range(CWND)]
        for tiempos, trace in self.transferir_con(MAX * CWND, emisor=lambda: netsim.drop_seqs(*perdidos)):
            for s in perdidos:
                self.assertEqual(len(tiempos[s]), 2)
            self.assertEsperoElTimeout(tiempos, perdidos[0])
            self.assertEqual(trace.timeouts, CWND)

    def test_dos_huecos_en_la_ventana_se_reenvian_solo_los_huecos(self):
        """Al vencer, on_timeout borra las marcas SACK, pero el ACK siguiente
        las vuelve a poner: no se reenvian los segmentos que si llegaron."""
        perdidos = {seq_del(0), seq_del(2)}
        for tiempos, trace in self.transferir_con(MAX * 4, emisor=lambda: netsim.drop_seqs(*perdidos)):
            reenviados = {s for s, t in tiempos.items() if len(t) > 1}
            self.assertEqual(reenviados, perdidos)
            self.assertEqual(trace.timeouts, 2)


class TestTimeoutSinPerdida(Timeout):

    def test_acks_mas_lentos_que_el_timeout_provocan_reenvios_de_mas(self):
        """Nada se pierde, pero los ACKs tardan 2 timeouts: el emisor reenvia
        igual (no hay RTT adaptativo). Los datos llegan bien; el receptor
        descarta los duplicados."""
        def acks_lentos():
            return lambda ctx: (("delay", self.timeout * 2)
                                if netsim.es_ack_puro_sack_pkt(ctx.pkt) else netsim.PASS)
        for tiempos, trace in self.transferir_con(MAX * 3, receptor=acks_lentos):
            self.assertGreater(trace.timeouts, 0)
            self.assertEqual(trace.fast_retransmits, 0)


if __name__ == "__main__":
    unittest.main()
