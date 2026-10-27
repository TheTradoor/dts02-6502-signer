"""W65C02S: cycle-accurate instruction core + pin-level bus timing wrapper.

The core is a generator that yields one bus cycle at a time:
    ('R', addr, None, sync)  -> receives the byte read
    ('W', addr, data, False) -> receives None
Cycle sequences (including dummy reads) follow the W65C02S datasheet.  The
full instruction set is implemented and checked against Klaus Dormann's
6502 and 65C02 functional test ROMs (tests/test_cpu_core.py).
"""

from . import timing as T
from .engine import X, Z
from .parts import Part

N, V, U, B, D, I, ZF, C = 0x80, 0x40, 0x20, 0x10, 0x08, 0x04, 0x02, 0x01


class UnknownOpcode(Exception):
    pass


class Core:
    """Complete W65C02S instruction set (including Rockwell/WDC bit ops,
    decimal mode with valid N/Z, and the 65C02 NOP encodings).  Each bus
    cycle, including dummy reads, is yielded in datasheet order."""

    def __init__(self):
        self.a = self.x = self.y = 0
        self.s = 0
        self.p = U | I
        self.pc = 0
        self.fetch_pc = None
        self.instructions = 0
        self.cycles = 0
        self.stopped = False

    # -- bus primitives --------------------------------------------------
    def rd(self, addr, sync=False):
        v = yield ("R", addr & 0xFFFF, None, sync)
        return v

    def wr(self, addr, v):
        yield ("W", addr & 0xFFFF, v & 0xFF, False)

    def nz(self, v):
        v &= 0xFF
        self.p = (self.p & ~(N | ZF)) | (v & N) | (ZF if v == 0 else 0)
        return v

    def run(self):
        yield from self.rd(self.pc)
        yield from self.rd(self.pc)
        for _ in range(3):
            yield from self.rd(0x100 | self.s)
            self.s = (self.s - 1) & 0xFF
        lo = yield from self.rd(0xFFFC)
        hi = yield from self.rd(0xFFFD)
        self.pc = lo | hi << 8
        self.p = (self.p | I) & ~D
        while True:
            yield from self.step()

    def fetch(self):
        v = yield from self.rd(self.pc)
        self.pc = (self.pc + 1) & 0xFFFF
        return v

    def abs_(self):
        lo = yield from self.fetch()
        hi = yield from self.fetch()
        return lo | hi << 8

    def push(self, v):
        yield from self.wr(0x100 | self.s, v)
        self.s = (self.s - 1) & 0xFF

    def pull(self):
        self.s = (self.s + 1) & 0xFF
        return (yield from self.rd(0x100 | self.s))

    # -- effective addresses ----------------------------------------------
    def ea(self, mode, kind):
        """kind: 'r' read, 'w' write, 'm' read-modify-write, 'm7' RMW that always
        takes the indexing cycle (INC/DEC abs,X)."""
        if mode == "zp":
            return (yield from self.fetch())
        if mode in ("zpx", "zpy"):
            b = yield from self.fetch()
            yield from self.rd((self.pc - 1) & 0xFFFF)
            return (b + (self.x if mode == "zpx" else self.y)) & 0xFF
        if mode == "abs":
            return (yield from self.abs_())
        if mode in ("absx", "absy"):
            b = yield from self.abs_()
            ea = (b + (self.x if mode == "absx" else self.y)) & 0xFFFF
            if kind in ("w", "m7") or (ea & 0xFF00) != (b & 0xFF00):
                yield from self.rd((self.pc - 1) & 0xFFFF)
            return ea
        if mode == "indx":
            z = yield from self.fetch()
            yield from self.rd((self.pc - 1) & 0xFFFF)
            z = (z + self.x) & 0xFF
            lo = yield from self.rd(z)
            hi = yield from self.rd((z + 1) & 0xFF)
            return lo | hi << 8
        if mode == "indy":
            z = yield from self.fetch()
            lo = yield from self.rd(z)
            hi = yield from self.rd((z + 1) & 0xFF)
            b = lo | hi << 8
            ea = (b + self.y) & 0xFFFF
            if kind == "w" or (ea & 0xFF00) != (b & 0xFF00):
                yield from self.rd((self.pc - 1) & 0xFFFF)
            return ea
        if mode == "indz":
            z = yield from self.fetch()
            lo = yield from self.rd(z)
            hi = yield from self.rd((z + 1) & 0xFF)
            return lo | hi << 8
        raise ValueError(mode)

    # -- ALU ----------------------------------------------------------------
    def compare(self, reg, v):
        self.p = (self.p & ~C) | (C if reg >= v else 0)
        self.nz(reg - v)

    def adc(self, v):
        a, c = self.a, self.p & C
        if not self.p & D:
            r = a + v + c
            ov = (~(a ^ v) & (a ^ r)) & 0x80
            self.p = (self.p & ~(C | V)) | (C if r > 0xFF else 0) | (V if ov else 0)
            self.a = self.nz(r)
            return False
        # 65C02 decimal (B. Clark, sequences 1 and 2)
        al = (a & 0x0F) + (v & 0x0F) + c
        if al >= 0x0A:
            al = ((al + 0x06) & 0x0F) + 0x10
        r = (a & 0xF0) + (v & 0xF0) + al
        sa = (a & 0xF0) - (0x100 if a & 0x80 else 0)
        sv = (v & 0xF0) - (0x100 if v & 0x80 else 0)
        sr = sa + sv + al
        if r >= 0xA0:
            r += 0x60
        self.p = (self.p & ~(C | V)) | (C if r >= 0x100 else 0) | (V if not -128 <= sr <= 127 else 0)
        self.a = self.nz(r)
        return True

    def sbc(self, v):
        a, c = self.a, self.p & C
        r = a - v - (1 - c)
        ov = ((a ^ v) & (a ^ r)) & 0x80
        self.p = (self.p & ~(C | V)) | (C if r >= 0 else 0) | (V if ov else 0)
        if not self.p & D:
            self.a = self.nz(r)
            return False
        al = (a & 0x0F) - (v & 0x0F) + c - 1
        if r < 0:
            r -= 0x60
        if al < 0:
            r -= 0x06
        self.a = self.nz(r)
        return True

    def shift(self, op, v):
        if op == "ASL":
            self.p = (self.p & ~C) | (C if v & 0x80 else 0)
            return self.nz(v << 1)
        if op == "LSR":
            self.p = (self.p & ~C) | (v & 1)
            return self.nz(v >> 1)
        if op == "ROL":
            c = self.p & C
            self.p = (self.p & ~C) | (C if v & 0x80 else 0)
            return self.nz(v << 1 | c)
        if op == "ROR":
            c = self.p & C
            self.p = (self.p & ~C) | (v & 1)
            return self.nz(v >> 1 | c << 7)
        if op == "INC":
            return self.nz(v + 1)
        if op == "DEC":
            return self.nz(v - 1)
        if op == "TSB":
            self.p = (self.p & ~ZF) | (ZF if (self.a & v) == 0 else 0)
            return (v | self.a) & 0xFF
        if op == "TRB":
            self.p = (self.p & ~ZF) | (ZF if (self.a & v) == 0 else 0)
            return v & ~self.a & 0xFF
        raise ValueError(op)

    # -- dispatch -------------------------------------------------------------
    def step(self):
        self.fetch_pc = self.pc
        op = yield from self.rd(self.pc, True)
        self.pc = (self.pc + 1) & 0xFFFF
        self.instructions += 1
        mnem, mode = OPTABLE[op]
        if mnem in READ_OPS:
            if mode == "imm":
                v = yield from self.fetch()
            else:
                ea = yield from self.ea(mode, "r")
                v = yield from self.rd(ea)
            if mnem == "LDA":
                self.a = self.nz(v)
            elif mnem == "LDX":
                self.x = self.nz(v)
            elif mnem == "LDY":
                self.y = self.nz(v)
            elif mnem == "AND":
                self.a = self.nz(self.a & v)
            elif mnem == "ORA":
                self.a = self.nz(self.a | v)
            elif mnem == "EOR":
                self.a = self.nz(self.a ^ v)
            elif mnem == "CMP":
                self.compare(self.a, v)
            elif mnem == "CPX":
                self.compare(self.x, v)
            elif mnem == "CPY":
                self.compare(self.y, v)
            elif mnem == "BIT":
                if mode == "imm":
                    self.p = (self.p & ~ZF) | (ZF if (self.a & v) == 0 else 0)
                else:
                    self.p = (self.p & ~(N | V | ZF)) | (v & (N | V)) | (ZF if (self.a & v) == 0 else 0)
            elif mnem in ("ADC", "SBC"):
                dec = self.adc(v) if mnem == "ADC" else self.sbc(v)
                if dec:
                    yield from self.rd((self.pc - 1) & 0xFFFF)   # decimal-mode extra cycle
        elif mnem in WRITE_OPS:
            ea = yield from self.ea(mode, "w")
            v = {"STA": self.a, "STX": self.x, "STY": self.y, "STZ": 0}[mnem]
            yield from self.wr(ea, v)
        elif mnem in RMW_OPS and mode != "acc":
            kind = "m7" if (mnem in ("INC", "DEC") and mode == "absx") else "m"
            ea = yield from self.ea(mode, kind)
            v = yield from self.rd(ea)
            yield from self.rd(ea)
            yield from self.wr(ea, self.shift(mnem, v))
        elif mode == "acc":
            yield from self.rd(self.pc)
            self.a = self.shift(mnem, self.a)
        elif mnem in BIT_OPS:                     # RMBn / SMBn
            zp = yield from self.fetch()
            v = yield from self.rd(zp)
            yield from self.rd(zp)
            bit = 1 << int(mnem[3])
            yield from self.wr(zp, (v | bit) if mnem[0] == "S" else (v & ~bit))
        elif mnem in BB_OPS:                      # BBRn / BBSn zp, rel
            zp = yield from self.fetch()
            v = yield from self.rd(zp)
            yield from self.rd(zp)
            off = yield from self.fetch()
            bit = (v >> int(mnem[3])) & 1
            if bit == (1 if mnem[2] == "S" else 0):
                yield from self.branch_to(off)
        elif mnem in BRANCHES:
            off = yield from self.fetch()
            cond = BRANCHES[mnem]
            if cond is None or bool(self.p & cond[0]) == bool(cond[1]):
                yield from self.branch_to(off)
        elif mnem == "JMP":
            if mode == "abs":
                self.pc = yield from self.abs_()
            else:
                b = yield from self.abs_()
                yield from self.rd((self.pc - 1) & 0xFFFF)
                if mode == "indabsx":
                    b = (b + self.x) & 0xFFFF
                lo = yield from self.rd(b)
                hi = yield from self.rd((b + 1) & 0xFFFF)
                self.pc = lo | hi << 8
        elif mnem == "JSR":
            lo = yield from self.fetch()
            yield from self.rd(0x100 | self.s)
            yield from self.push(self.pc >> 8)
            yield from self.push(self.pc & 0xFF)
            hi = yield from self.rd(self.pc)
            self.pc = lo | hi << 8
        elif mnem == "RTS":
            yield from self.rd(self.pc)
            yield from self.rd(0x100 | self.s)
            lo = yield from self.pull()
            hi = yield from self.pull()
            self.pc = lo | hi << 8
            yield from self.rd(self.pc)
            self.pc = (self.pc + 1) & 0xFFFF
        elif mnem == "RTI":
            yield from self.rd(self.pc)
            yield from self.rd(0x100 | self.s)
            self.p = (yield from self.pull()) | U | B
            lo = yield from self.pull()
            hi = yield from self.pull()
            self.pc = lo | hi << 8
        elif mnem == "BRK":
            yield from self.fetch()
            yield from self.push(self.pc >> 8)
            yield from self.push(self.pc & 0xFF)
            yield from self.push(self.p | B | U)
            lo = yield from self.rd(0xFFFE)
            hi = yield from self.rd(0xFFFF)
            self.p = (self.p | I) & ~D
            self.pc = lo | hi << 8
        elif mnem in ("PHA", "PHP", "PHX", "PHY"):
            yield from self.rd(self.pc)
            v = {"PHA": self.a, "PHP": self.p | B | U, "PHX": self.x, "PHY": self.y}[mnem]
            yield from self.push(v)
        elif mnem in ("PLA", "PLP", "PLX", "PLY"):
            yield from self.rd(self.pc)
            yield from self.rd(0x100 | self.s)
            v = yield from self.pull()
            if mnem == "PLP":
                self.p = v | U | B
            elif mnem == "PLA":
                self.a = self.nz(v)
            elif mnem == "PLX":
                self.x = self.nz(v)
            else:
                self.y = self.nz(v)
        elif mnem == "NOP":
            n_bytes, n_cyc = NOP_SHAPE[op]
            for _ in range(n_bytes - 1):
                yield from self.fetch()
            for _ in range(n_cyc - n_bytes):
                yield from self.rd((self.pc - 1) & 0xFFFF)
        elif mnem in ("WAI", "STP"):
            self.stopped = True
            raise UnknownOpcode(f"{mnem} at ${self.fetch_pc:04X}")
        else:                                       # implied, 2 cycles
            yield from self.rd(self.pc)
            self.implied(mnem)

    def branch_to(self, off):
        yield from self.rd(self.pc)
        tgt = (self.pc + (off - 256 if off & 0x80 else off)) & 0xFFFF
        if (tgt & 0xFF00) != (self.pc & 0xFF00):
            yield from self.rd(self.pc)
        self.pc = tgt

    def implied(self, m):
        if m == "INX":
            self.x = self.nz(self.x + 1)
        elif m == "INY":
            self.y = self.nz(self.y + 1)
        elif m == "DEX":
            self.x = self.nz(self.x - 1)
        elif m == "DEY":
            self.y = self.nz(self.y - 1)
        elif m == "TAX":
            self.x = self.nz(self.a)
        elif m == "TXA":
            self.a = self.nz(self.x)
        elif m == "TAY":
            self.y = self.nz(self.a)
        elif m == "TYA":
            self.a = self.nz(self.y)
        elif m == "TXS":
            self.s = self.x
        elif m == "TSX":
            self.x = self.nz(self.s)
        elif m == "SEI":
            self.p |= I
        elif m == "CLI":
            self.p &= ~I
        elif m == "SED":
            self.p |= D
        elif m == "CLD":
            self.p &= ~D
        elif m == "SEC":
            self.p |= C
        elif m == "CLC":
            self.p &= ~C
        elif m == "CLV":
            self.p &= ~V
        elif m == "INA":
            self.a = self.nz(self.a + 1)
        elif m == "DEA":
            self.a = self.nz(self.a - 1)
        else:
            raise UnknownOpcode(m)


READ_OPS = {"LDA", "LDX", "LDY", "AND", "ORA", "EOR", "CMP", "CPX", "CPY", "BIT", "ADC", "SBC"}
WRITE_OPS = {"STA", "STX", "STY", "STZ"}
RMW_OPS = {"ASL", "LSR", "ROL", "ROR", "INC", "DEC", "TSB", "TRB"}
BIT_OPS = {f"{k}MB{i}" for k in "RS" for i in range(8)}
BB_OPS = {f"BB{k}{i}" for k in "RS" for i in range(8)}
BRANCHES = {"BPL": (N, 0), "BMI": (N, 1), "BVC": (V, 0), "BVS": (V, 1), "BCC": (C, 0),
            "BCS": (C, 1), "BNE": (ZF, 0), "BEQ": (ZF, 1), "BRA": None}


def _build_optable():
    t = {}
    groups = {
        "ORA": {0x09: "imm", 0x05: "zp", 0x15: "zpx", 0x0D: "abs", 0x1D: "absx", 0x19: "absy", 0x01: "indx", 0x11: "indy", 0x12: "indz"},
        "AND": {0x29: "imm", 0x25: "zp", 0x35: "zpx", 0x2D: "abs", 0x3D: "absx", 0x39: "absy", 0x21: "indx", 0x31: "indy", 0x32: "indz"},
        "EOR": {0x49: "imm", 0x45: "zp", 0x55: "zpx", 0x4D: "abs", 0x5D: "absx", 0x59: "absy", 0x41: "indx", 0x51: "indy", 0x52: "indz"},
        "ADC": {0x69: "imm", 0x65: "zp", 0x75: "zpx", 0x6D: "abs", 0x7D: "absx", 0x79: "absy", 0x61: "indx", 0x71: "indy", 0x72: "indz"},
        "STA": {0x85: "zp", 0x95: "zpx", 0x8D: "abs", 0x9D: "absx", 0x99: "absy", 0x81: "indx", 0x91: "indy", 0x92: "indz"},
        "LDA": {0xA9: "imm", 0xA5: "zp", 0xB5: "zpx", 0xAD: "abs", 0xBD: "absx", 0xB9: "absy", 0xA1: "indx", 0xB1: "indy", 0xB2: "indz"},
        "CMP": {0xC9: "imm", 0xC5: "zp", 0xD5: "zpx", 0xCD: "abs", 0xDD: "absx", 0xD9: "absy", 0xC1: "indx", 0xD1: "indy", 0xD2: "indz"},
        "SBC": {0xE9: "imm", 0xE5: "zp", 0xF5: "zpx", 0xED: "abs", 0xFD: "absx", 0xF9: "absy", 0xE1: "indx", 0xF1: "indy", 0xF2: "indz"},
        "ASL": {0x0A: "acc", 0x06: "zp", 0x16: "zpx", 0x0E: "abs", 0x1E: "absx"},
        "ROL": {0x2A: "acc", 0x26: "zp", 0x36: "zpx", 0x2E: "abs", 0x3E: "absx"},
        "LSR": {0x4A: "acc", 0x46: "zp", 0x56: "zpx", 0x4E: "abs", 0x5E: "absx"},
        "ROR": {0x6A: "acc", 0x66: "zp", 0x76: "zpx", 0x6E: "abs", 0x7E: "absx"},
        "STX": {0x86: "zp", 0x96: "zpy", 0x8E: "abs"},
        "LDX": {0xA2: "imm", 0xA6: "zp", 0xB6: "zpy", 0xAE: "abs", 0xBE: "absy"},
        "DEC": {0xC6: "zp", 0xD6: "zpx", 0xCE: "abs", 0xDE: "absx"},
        "INC": {0xE6: "zp", 0xF6: "zpx", 0xEE: "abs", 0xFE: "absx"},
        "STY": {0x84: "zp", 0x94: "zpx", 0x8C: "abs"},
        "LDY": {0xA0: "imm", 0xA4: "zp", 0xB4: "zpx", 0xAC: "abs", 0xBC: "absx"},
        "CPY": {0xC0: "imm", 0xC4: "zp", 0xCC: "abs"},
        "CPX": {0xE0: "imm", 0xE4: "zp", 0xEC: "abs"},
        "BIT": {0x89: "imm", 0x24: "zp", 0x34: "zpx", 0x2C: "abs", 0x3C: "absx"},
        "STZ": {0x64: "zp", 0x74: "zpx", 0x9C: "abs", 0x9E: "absx"},
        "TSB": {0x04: "zp", 0x0C: "abs"},
        "TRB": {0x14: "zp", 0x1C: "abs"},
        "JMP": {0x4C: "abs", 0x6C: "ind", 0x7C: "indabsx"},
        "JSR": {0x20: "abs"},
    }
    for m, ops in groups.items():
        for op, mode in ops.items():
            t[op] = (m, mode)
    simple = {0x00: "BRK", 0x40: "RTI", 0x60: "RTS", 0x08: "PHP", 0x28: "PLP", 0x48: "PHA",
              0x68: "PLA", 0xDA: "PHX", 0xFA: "PLX", 0x5A: "PHY", 0x7A: "PLY", 0x18: "CLC",
              0x38: "SEC", 0x58: "CLI", 0x78: "SEI", 0xB8: "CLV", 0xD8: "CLD", 0xF8: "SED",
              0xAA: "TAX", 0x8A: "TXA", 0xA8: "TAY", 0x98: "TYA", 0x9A: "TXS", 0xBA: "TSX",
              0xE8: "INX", 0xC8: "INY", 0xCA: "DEX", 0x88: "DEY", 0x1A: "INA", 0x3A: "DEA",
              0xEA: "NOP", 0xCB: "WAI", 0xDB: "STP"}
    for op, m in simple.items():
        t[op] = (m, "imp")
    for m, op in {"BPL": 0x10, "BMI": 0x30, "BVC": 0x50, "BVS": 0x70, "BCC": 0x90,
                  "BCS": 0xB0, "BNE": 0xD0, "BEQ": 0xF0, "BRA": 0x80}.items():
        t[op] = (m, "rel")
    for i in range(8):
        t[0x07 + 0x10 * i] = (f"RMB{i}", "zp")
        t[0x87 + 0x10 * i] = (f"SMB{i}", "zp")
        t[0x0F + 0x10 * i] = (f"BBR{i}", "zprel")
        t[0x8F + 0x10 * i] = (f"BBS{i}", "zprel")
    for op in range(256):
        if op not in t:
            t[op] = ("NOP", "imp")
    return t


OPTABLE = _build_optable()
# 65C02 undefined opcodes are NOPs of fixed length / duration (bytes, cycles)
NOP_SHAPE = {op: (1, 1) for op in range(256)}
NOP_SHAPE[0xEA] = (1, 2)
for _op in (0x02, 0x22, 0x42, 0x62, 0x82, 0xC2, 0xE2):
    NOP_SHAPE[_op] = (2, 2)
NOP_SHAPE[0x44] = (2, 3)
for _op in (0x54, 0xD4, 0xF4):
    NOP_SHAPE[_op] = (2, 4)
NOP_SHAPE[0x5C] = (3, 8)
for _op in (0xDC, 0xFC):
    NOP_SHAPE[_op] = (3, 4)


def _halted(addr):
    """Bus activity after an unknown opcode: keep reading the same address."""
    while True:
        yield ("R", addr, None, False)


ADDR_PINS = list(range(9, 21)) + [22, 23, 24, 25]          # A0..A15
DATA_PINS = [33, 32, 31, 30, 29, 28, 27, 26]               # D0..D7


class W65C02S(Part):
    kind = "W65C02S"
    roles = {8: "vcc", 21: "gnd", 2: "in", 4: "in", 6: "in", 36: "in", 38: "in",
             37: "in", 40: "in", 34: "out", 7: "out", 1: "out", 3: "out", 5: "out",
             39: "out"}
    roles.update({p: "out" for p in ADDR_PINS})
    roles.update({p: "tri" for p in DATA_PINS})

    def __init__(self, sim, name, pins):
        super().__init__(sim, name, pins)
        rng = sim.rng
        self.core = Core()
        self.gen = None
        self.req = ("R", rng.randrange(65536), None, False)
        self.rwb_init = 0                 # adversarial power-up: RWB low
        self.prev_phi2 = X
        self.last_edge = None
        self.reset_clocks = 0
        self.ever_reset = False
        self.cycles = []                  # (t_fall, rw, addr, data, sync)
        self.keep_trace = True
        self.halted = None

    def start(self, t):
        rw, addr = self.req[0], self.req[1]
        for i, p in enumerate(ADDR_PINS):
            self.out(p, (addr >> i) & 1, t, 0)
        self.out(34, self.rwb_init, t, 0)
        self.out(7, 0, t, 0)
        for p in DATA_PINS:
            self.out(p, Z, t, 0)

    def evaluate(self, t):
        phi2 = self.inp(37)
        if phi2 == self.prev_phi2:
            return
        if phi2 == X:
            self.sim.violation(t, "clock-X", self.name, "PHI2 unknown")
        if self.last_edge is not None and phi2 in (0, 1) and self.prev_phi2 in (0, 1):
            w = t - self.last_edge
            if w < T.CPU["tPW"]:
                self.sim.violation(t, "pulse-width", f"{self.name}.PHI2", f"{w:.1f} ns")
        prev = self.prev_phi2
        self.prev_phi2 = phi2
        self.last_edge = t
        if prev == 0 and phi2 == 1:
            self._rise(t)
        elif prev == 1 and phi2 == 0:
            self._fall(t)

    def _rise(self, t):
        if self.req[0] == "W":
            d = self.req[2]
            for i, p in enumerate(DATA_PINS):
                self.out(p, (d >> i) & 1, t, self.dly(("mds", i), T.CPU["tMDS"]))

    def _fall(self, t):
        rw, addr, data, sync = self.req
        val = None
        if rw == "R":
            nets = [self.pins[p] for p in DATA_PINS]
            self.sim.check_sample(t, nets, T.CPU["tDSR"], T.CPU["tDHR"], f"{self.name}.read@{addr:04X}")
            val = 0
            for i, n in enumerate(nets):
                b = n.value if n.value in (0, 1) else self.sim.rng.randrange(2)
                val |= b << i
        else:
            for i, p in enumerate(DATA_PINS):
                self.out(p, Z, t, self.dly(("dhw", i), T.CPU["tDHW"]))
        if self.keep_trace:
            self.cycles.append((t, rw, addr, data if rw == "W" else val, sync))

        resb = self.inp(40)
        if resb != 1:
            self.reset_clocks += 1
            self.ever_reset = True
            self.gen = None
            nxt = ("R", addr, None, False)
        elif self.gen is None:
            if self.reset_clocks < 2:
                self.sim.violation(t, "reset-width", self.name,
                                   f"RESB released after {self.reset_clocks} clocks (<2)")
            self.reset_clocks = 0
            self.core = Core() if not self.ever_reset else self._keep_regs()
            self.gen = self.core.run()
            nxt = next(self.gen)
        else:
            try:
                nxt = self.gen.send(val if rw == "R" else None)
            except Exception as e:      # unknown opcode etc.
                self.halted = str(e)
                self.sim.violation(t, "cpu-halt", self.name, str(e))
                nxt = ("R", addr, None, False)
                self.gen = _halted(addr)
        self.req = nxt
        nrw, naddr, _, nsync = nxt
        changed = addr ^ naddr
        for i, p in enumerate(ADDR_PINS):
            if (changed >> i) & 1:
                self.out(p, (naddr >> i) & 1, t, self.dly(("ads", i), T.CPU["tADS"]))
        self.out(34, 1 if nrw == "R" else 0, t, self.dly("rwb", T.CPU["tADS"]))
        self.out(7, 1 if nsync else 0, t, self.dly("sync", T.CPU["tADS"]))

    def _keep_regs(self):
        # Registers other than those reset defines keep their values across RESB.
        c = Core()
        c.a, c.x, c.y, c.s, c.pc = self.core.a, self.core.x, self.core.y, self.core.s, self.core.pc
        c.p = self.core.p
        return c
