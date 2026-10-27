"""Operand bounds and byte encoding for the test-ROM assembler."""

import unittest

from sim.asm import AsmError, assemble


class TestOperandBounds(unittest.TestCase):
    def test_rejects_out_of_range_instruction_operands(self):
        cases = (
            "INC $4000", "DEC $6000", "INC $100", "DEC 0-1",
            "LDA #$100", "LDX #256", "LDY #0-1",
            "LDA ($100),Y", "STA ($1234),Y", "LDA (0-1),Y",
            "STA $10000", "LDA 0-1", "LDA $10000,X",
            "STA $10000,Y", "JMP $10000", "JSR 0-1",
        )
        for source in cases:
            with self.subTest(source=source):
                with self.assertRaisesRegex(AsmError, "operand out of range"):
                    assemble(source)

    def test_rejects_out_of_range_data(self):
        for source in (".byte $100", ".byte 0-1", ".word $10000", ".word 0-1"):
            with self.subTest(source=source):
                with self.assertRaisesRegex(AsmError, "out of range"):
                    assemble(source)

    def test_encodes_valid_boundaries(self):
        cases = {
            "LDA #0": "a9 00",
            "LDA #$FF": "a9 ff",
            "INC $00": "e6 00",
            "DEC $FF": "c6 ff",
            "LDA ($00),Y": "b1 00",
            "STA ($FF),Y": "91 ff",
            "STA $0000": "8d 00 00",
            "STA $FFFF": "8d ff ff",
            "LDA $0000,X": "bd 00 00",
            "LDA $FFFF,X": "bd ff ff",
            "STA $0000,Y": "99 00 00",
            "STA $FFFF,Y": "99 ff ff",
            ".byte 0, $FF": "00 ff",
            ".word 0, $FFFF": "00 00 ff ff",
        }
        for source, hex_bytes in cases.items():
            with self.subTest(source=source):
                expected = bytes.fromhex(hex_bytes)
                image, _ = assemble(source)
                self.assertEqual(image[:len(expected)], expected)

    def test_preserves_explicit_byte_extraction(self):
        image, _ = assemble("""
            LDA #<$1234
            LDX #>$1234
            .byte <$1234, >$1234, <0-1
        """)
        self.assertEqual(image[:7], bytes.fromhex("a9 34 a2 12 34 12 ff"))

    def test_resolves_forward_references_before_checking_bounds(self):
        image, symbols = assemble("""
            LDA #VALUE
            INC VALUE
            LDA (VALUE),Y
            .byte VALUE
            .word target
            LDX #<target
            LDY #>target
            VALUE = $FF
        target: NOP
        """)
        self.assertEqual(symbols["target"], 0x800D)
        self.assertEqual(image[:14], bytes.fromhex(
            "a9 ff e6 ff b1 ff ff 0d 80 a2 0d a0 80 ea"))

    def test_rejects_out_of_range_forward_references(self):
        for source in ("LDA #VALUE", "INC VALUE", "LDA (VALUE),Y", ".byte VALUE"):
            with self.subTest(source=source):
                with self.assertRaisesRegex(AsmError, "out of range"):
                    assemble(source + "\nVALUE = $100\n")
        for source in ("STA VALUE", ".word VALUE"):
            with self.subTest(source=source):
                with self.assertRaisesRegex(AsmError, "out of range"):
                    assemble(source + "\nVALUE = $10000\n")

    def test_reports_source_line_for_range_errors(self):
        with self.assertRaisesRegex(AsmError, r"line 3: INC \$4000: zp operand out of range"):
            assemble(".org $8000\nNOP\nINC $4000\n")


if __name__ == "__main__":
    unittest.main()
