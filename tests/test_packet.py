"""Tests del (de)serializado de la cabecera RDT."""

import unittest

from base import packet, parse


class TestCabecera(unittest.TestCase):

    def test_version_y_protocolo_van_en_medio_byte_cada_uno(self):
        p = packet.make_packet(version=1, protocol=2)
        self.assertEqual(packet.get_header_version(p), 1)
        self.assertEqual(packet.get_header_protocol(p), 2)
        self.assertEqual(p[0], 0x12)

    def test_version_y_protocolo_se_truncan_a_4_bits(self):
        p = packet.make_packet(version=0xFF, protocol=0xFF)
        self.assertEqual(packet.get_header_version(p), 0x0F)
        self.assertEqual(packet.get_header_protocol(p), 0x0F)

    def test_cada_flag_por_separado(self):
        casos = [
            (packet.SYN_MASK, "SYN"),
            (packet.FIN_MASK, "FIN"),
            (packet.ERR_MASK, "ERR"),
            (packet.ACK_MASK, "ACK"),
            (packet.CANCEL_MASK, "CANCEL"),
        ]
        for mascara, nombre in casos:
            with self.subTest(flag=nombre):
                d = parse(packet.make_packet(flags=mascara))
                for otro in ("SYN", "FIN", "ERR", "ACK", "CANCEL"):
                    self.assertEqual(d[otro], 1 if otro == nombre else 0)

    def test_cancel_usa_el_bit_3(self):
        self.assertEqual(packet.CANCEL_MASK, 0b00001000)
        p = packet.make_packet(flags=packet.CANCEL_MASK)
        self.assertEqual(packet.get_header_flags(p), 0b00001000)

    def test_err_con_cancel_es_el_aborto_deliberado(self):
        d = parse(
            packet.make_packet(flags=packet.ERR_MASK | packet.CANCEL_MASK)
        )
        self.assertEqual((d["ERR"], d["CANCEL"]), (1, 1))
        self.assertEqual((d["SYN"], d["FIN"], d["ACK"]), (0, 0, 0))

    def test_los_bits_2_a_0_siguen_reservados(self):
        # CANCEL no debe pisar lo que queda libre para el futuro.
        usados = (
            packet.SYN_MASK
            | packet.FIN_MASK
            | packet.ERR_MASK
            | packet.ACK_MASK
            | packet.CANCEL_MASK
        )
        self.assertEqual(usados, 0b11111000)

    def test_flags_combinados(self):
        d = parse(packet.make_packet(flags=packet.SYN_MASK | packet.ACK_MASK))
        self.assertEqual(
            (d["SYN"], d["ACK"], d["FIN"], d["ERR"]),
            (1, 1, 0, 0)
        )

    def test_sin_flags(self):
        d = parse(packet.make_packet())
        self.assertEqual(
            (d["SYN"], d["ACK"], d["FIN"], d["ERR"]),
            (0, 0, 0, 0)
        )

    def test_todos_los_flags(self):
        todos = (
            packet.SYN_MASK | packet.FIN_MASK | packet.ERR_MASK
            | packet.ACK_MASK
        )
        d = parse(packet.make_packet(flags=todos))
        self.assertEqual(
            (d["SYN"], d["FIN"], d["ERR"], d["ACK"]),
            (1, 1, 1, 1)
        )

    def test_flags_se_truncan_a_8_bits(self):
        p = packet.make_packet(flags=0x1FF)
        self.assertEqual(packet.get_header_flags(p), 0xFF)

    def test_hlen_sin_opciones_es_12(self):
        p = packet.make_packet()
        self.assertEqual(packet.get_header_hlen(p), 12)
        self.assertEqual(len(p), 12)
        self.assertEqual(packet.get_header_options(p), b"")

    def test_hlen_con_opciones(self):
        p = packet.make_packet(options=b"\x01\x02\x03\x04", payload=b"hola")
        self.assertEqual(packet.get_header_hlen(p), 16)
        self.assertEqual(packet.get_header_options(p), b"\x01\x02\x03\x04")
        self.assertEqual(packet.get_payload(p), b"hola")

    def test_seq_y_ack_ida_y_vuelta(self):
        for seq, ack in [(0, 0), (1, 1), (1400, 1401), (2**32 - 1, 2**32 - 1)]:
            with self.subTest(seq=seq, ack=ack):
                p = packet.make_packet(sequence_number=seq, ack=ack)
                self.assertEqual(packet.get_header_sequence_paquet(p), seq)
                self.assertEqual(packet.get_header_ack(p), ack)

    def test_seq_fuera_de_rango_de_32_bits_rompe(self):
        # No hay wrap-around: seq >= 2^32 revienta al serializar.
        with self.assertRaises(OverflowError):
            packet.make_packet(sequence_number=2**32)

    def test_payload_ida_y_vuelta(self):
        for datos in [b"", b"x", b"a" * 1400, bytes(range(256))]:
            with self.subTest(n=len(datos)):
                p = packet.make_packet(payload=datos)
                self.assertEqual(packet.get_payload(p), datos)

    def test_tamano_total_respeta_mtu_de_1500(self):
        from base import sw

        p = packet.make_packet(payload=b"z" * sw.MAX_PAYLOAD_SIZE)
        # 20 (IP) + 8 (UDP) + cabecera RDT + payload
        self.assertLessEqual(20 + 8 + len(p), 1500)

    def test_defaults_de_make_packet(self):
        d = parse(packet.make_packet())
        self.assertEqual(
            (d["version"], d["protocol"], d["seq"], d["ack"], d["payload"]),
            (0, 0, 0, 0, b""),
        )

    def test_byte_reservado_en_cero(self):
        self.assertEqual(packet.make_packet()[3], 0)


if __name__ == "__main__":
    unittest.main()
