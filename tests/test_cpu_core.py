"""Cycle counts and semantics of the 65C02 core on an ideal bus."""

import unittest

from sim.asm import assemble
from sim.cpu65c02 import Core


def run(src, max_cycles=10_000, stop="end"):
    img, syms = assemble(".org $8000\n" + src + "\nend: JMP end\n.org $FFFC\n.word $8000\n")
    mem = bytearray(0x10000)
    mem[0x8000:] = img
    core = Core()
    gen = core.run()
    req = next(gen)
    trace = []
    for _ in range(max_cycles):
        rw, addr, data, sync = req
        trace.append((rw, addr, data))
        if sync and addr == syms[stop]:
            break
        if rw == "R":
            req = gen.send(mem[addr])
        else:
            mem[addr] = data
            req = gen.send(None)
    return core, mem, trace, syms


class TestCore(unittest.TestCase):
    def test_reset_sequence_reads_vector(self):
        core, mem, trace, _ = run("NOP")
        self.assertTrue(all(rw == "R" for rw, _, _ in trace[:7]))
        self.assertEqual([a for _, a, _ in trace[5:7]], [0xFFFC, 0xFFFD])
        self.assertEqual(trace[7][1], 0x8000)

    def test_cycle_counts(self):
        cases = {"LDA #1": 2, "LDA $1234": 4, "STA $1234": 4, "STA $1234,X": 5,
                 "LDA $12": 3, "JSR sub\nJMP end\nsub: RTS": 6 + 6 + 3, "PHA": 3, "PLA": 4,
                 "INC $12": 5, "TAX": 2, "LDA ($12),Y": 5}
        for src, cycles in cases.items():
            _, _, trace, _ = run(src)
            self.assertEqual(len(trace) - 7 - 1, cycles, src)

    def test_arithmetic_and_flags(self):
        core, mem, _, _ = run("""
            LDA #$F0
            CLC
            ADC #$20
            STA $10
            LDA #$05
            SEC
            SBC #$07
            STA $11
            LDX #3
            loop: DEX
            BNE loop
            STX $12
            LDA #$81
            ASL A
            ROL A
            STA $13
        """)
        self.assertEqual(mem[0x10], 0x10)
        self.assertEqual(mem[0x11], 0xFE)
        self.assertEqual(mem[0x12], 0)
        self.assertEqual(mem[0x13], 0x05)

    def test_stack_and_indirect(self):
        core, mem, _, _ = run("""
            LDX #$FF
            TXS
            LDA #$00
            STA $20
            LDA #$03
            STA $21
            LDY #4
            LDA #$5A
            STA ($20),Y
            JSR sub
            JMP end
            sub: PHA
            PLA
            STA $0400
            RTS
        """)
        self.assertEqual(mem[0x0304], 0x5A)
        self.assertEqual(mem[0x0400], 0x5A)
        self.assertEqual(core.s, 0xFF)


if __name__ == "__main__":
    unittest.main()


class TestBootRomIdealBus(unittest.TestCase):
    def test_full_zeroing_build_boots(self):
        from sim.firmware import boot_rom
        img, syms = boot_rom(zero_end_page=0x40)
        mem = bytearray(range(256)) * 256         # garbage everywhere
        mem[0x8000:] = img
        core = Core()
        gen = core.run()
        req = next(gen)
        for _ in range(400_000):
            rw, addr, data, sync = req
            if sync and addr == syms["done"]:
                break
            if rw == "R":
                v = {0x7F00: 0xFF, 0x7F01: 0x00}.get(addr & 0xFF07 if addr >= 0x7F00 and addr < 0x8000 else addr,
                                                     0xFF if 0x7F00 <= addr < 0x8000 else mem[addr])
                req = gen.send(v)
            else:
                if addr < 0x7F00:
                    mem[addr] = data
                req = gen.send(None)
        else:
            self.fail("boot ROM did not reach done")
        dirty = [a for a in range(0x0200, 0x4000) if mem[a]]
        self.assertEqual(dirty, [])
