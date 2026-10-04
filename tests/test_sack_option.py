"""Opcion SACK (TLV del RFC 2018) y operaciones sobre bloques [left, right)."""

import unittest

import base  # noqa: F401 - arma el sys.path hacia src/
from lib.protocols.selective_ack.sack_option import (
    BLOCK_SIZE,
    MAX_BLOCKS,
    OPTION_HEADER_SIZE,
    SACK_TYPE,
    add_block,
    covers,
    discard_below,
    make_sack_option,
    parse_sack_option,
    )


class TestMakeSackOption(unittest.TestCase):

    def test_sin_bloques_no_hay_opcion(self):
        """Un ACK sin huecos queda con hlen=12, igual que Stop & Wait."""
        self.assertEqual(make_sack_option([]), b"")

    def test_un_bloque(self):
        opcion = make_sack_option([(100, 200)])
        self.assertEqual(opcion[0], SACK_TYPE)
        self.assertEqual(opcion[1], OPTION_HEADER_SIZE + BLOCK_SIZE)
        self.assertEqual(int.from_bytes(opcion[2:6], "big"), 100)
        self.assertEqual(int.from_bytes(opcion[6:10], "big"), 200)

    def test_el_largo_cuenta_tipo_y_largo(self):
        for n in range(1, MAX_BLOCKS + 1):
            with self.subTest(bloques=n):
                bloques = [(i * 10, i * 10 + 5) for i in range(n)]
                opcion = make_sack_option(bloques)
                self.assertEqual(
                    len(opcion),
                    OPTION_HEADER_SIZE + BLOCK_SIZE * n
                    )
                self.assertEqual(opcion[1], len(opcion))

    def test_mas_de_max_blocks_se_trunca(self):
        bloques = [(i * 10, i * 10 + 5) for i in range(MAX_BLOCKS + 3)]
        opcion = make_sack_option(bloques)
        self.assertEqual(parse_sack_option(opcion), bloques[:MAX_BLOCKS])

    def test_acepta_cualquier_iterable(self):
        self.assertEqual(
            make_sack_option(((1, 2),)),
            make_sack_option([(1, 2)])
            )


class TestParseSackOption(unittest.TestCase):

    def test_ida_y_vuelta(self):
        for bloques in (
            [(1, 2)],
            [(10, 20), (30, 40)],
            [(i * 100, i * 100 + 50) for i in range(MAX_BLOCKS)],
        ):
            with self.subTest(bloques=bloques):
                self.assertEqual(
                    parse_sack_option(make_sack_option(bloques)),
                    bloques
                    )

    def test_respeta_el_orden_de_llegada(self):
        bloques = [(30, 40), (10, 20)]
        self.assertEqual(parse_sack_option(make_sack_option(bloques)), bloques)

    def test_opciones_vacias(self):
        self.assertEqual(parse_sack_option(b""), [])

    def test_menos_que_un_encabezado(self):
        self.assertEqual(parse_sack_option(bytes([SACK_TYPE])), [])

    def test_largo_menor_que_el_encabezado_corta(self):
        """Un largo < 2 no puede avanzar: se deja de leer en vez de colgarse.
        """
        self.assertEqual(parse_sack_option(bytes([SACK_TYPE, 1, 0, 0])), [])
        self.assertEqual(parse_sack_option(bytes([SACK_TYPE, 0])), [])

    def test_largo_que_se_pasa_del_buffer_corta(self):
        opcion = make_sack_option([(1, 2)])
        self.assertEqual(parse_sack_option(opcion[:-1]), [])

    def test_tipo_desconocido_se_saltea(self):
        ajena = bytes([0x07, 4, 0xAA, 0xBB])
        self.assertEqual(
            parse_sack_option(ajena + make_sack_option([(5, 9)])), [(5, 9)]
            )

    def test_cuerpo_con_bloque_incompleto_ignora_el_resto(self):
        """Largo impar: el bloque partido del final no se interpreta."""
        cuerpo = (
            (5).to_bytes(4, "big") +
            (9).to_bytes(4, "big") +
            b"\x00\x00\x00"
        )
        opcion = bytes([SACK_TYPE, OPTION_HEADER_SIZE + len(cuerpo)]) + cuerpo
        self.assertEqual(parse_sack_option(opcion), [(5, 9)])

    def test_bloques_vacios_o_invertidos_se_descartan(self):
        cuerpo = b"".join(
            a.to_bytes(4, "big") + b.to_bytes(4, "big")
            for a, b in ((9, 9), (20, 10), (1, 3))
            )
        opcion = bytes([SACK_TYPE, OPTION_HEADER_SIZE + len(cuerpo)]) + cuerpo
        self.assertEqual(parse_sack_option(opcion), [(1, 3)])


class TestAddBlock(unittest.TestCase):

    def test_bloque_vacio_o_invertido_no_cambia_nada(self):
        self.assertEqual(add_block([(1, 5)], 7, 7), [(1, 5)])
        self.assertEqual(add_block([(1, 5)], 9, 7), [(1, 5)])

    def test_no_modifica_la_lista_original(self):
        original = [(1, 5)]
        add_block(original, 10, 20)
        self.assertEqual(original, [(1, 5)])

    def test_disjuntos_quedan_ordenados(self):
        self.assertEqual(add_block([(30, 40)], 10, 20), [(10, 20), (30, 40)])

    def test_adyacentes_se_unen(self):
        self.assertEqual(add_block([(120, 135)], 135, 141), [(120, 141)])

    def test_solapados_se_unen(self):
        self.assertEqual(add_block([(10, 20)], 15, 30), [(10, 30)])

    def test_contenido_no_agranda(self):
        self.assertEqual(add_block([(10, 50)], 20, 30), [(10, 50)])

    def test_puente_entre_dos_bloques(self):
        self.assertEqual(add_block([(10, 20), (30, 40)], 20, 30), [(10, 40)])

    def test_entrada_desordenada(self):
        self.assertEqual(
            add_block(
                [(50, 60), (10, 20)],
                30,
                40
            ),
            [(10, 20), (30, 40), (50, 60)]
        )


class TestDiscardBelow(unittest.TestCase):

    def test_descarta_los_que_terminan_antes(self):
        self.assertEqual(discard_below([(1, 5), (10, 20)], 5), [(10, 20)])

    def test_recorta_el_que_cruza(self):
        self.assertEqual(discard_below([(1, 10)], 4), [(4, 10)])

    def test_deja_los_que_estan_arriba(self):
        self.assertEqual(discard_below([(10, 20)], 3), [(10, 20)])

    def test_vacio(self):
        self.assertEqual(discard_below([], 100), [])


class TestCovers(unittest.TestCase):

    def test_dentro(self):
        self.assertTrue(covers([(10, 50)], 20, 30))

    def test_bordes_exactos(self):
        self.assertTrue(covers([(10, 50)], 10, 50))

    def test_se_pasa_por_un_lado(self):
        self.assertFalse(covers([(10, 50)], 5, 20))
        self.assertFalse(covers([(10, 50)], 40, 51))

    def test_partido_entre_dos_bloques_no_cuenta(self):
        self.assertFalse(covers([(10, 20), (20, 30)], 15, 25))

    def test_sin_bloques(self):
        self.assertFalse(covers([], 1, 2))


if __name__ == "__main__":
    unittest.main()
