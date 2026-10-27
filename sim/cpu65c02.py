"""W65C02S: cycle-accurate instruction core + pin-level bus timing wrapper.

The core is a generator that yields one bus cycle at a time:
    ('R', addr, None, sync)  -> receives the byte read
    ('W', addr, data, False) -> receives None
Cycle sequences (including dummy reads) follow the W65C02S datasheet for the
subset of opcodes implemented here; that subset covers every program in
firmware.py and the spec's diagnostic ROM.  Binary-mode ADC/SBC only.
"""

from . import timing as T
from .engine import X, Z
from .parts import Part

N, V, U, B, D, I, ZF, C = 0x80, 0x40, 0x20, 0x10, 0x08, 0x04, 0x02, 0x01


class UnknownOpcode(Exception):
    pass


class Core:
    def __init__(self):
        self.a = self.x = self.y = 0
        self.s = 0
        self.p = U | I
        self.pc = 0
        self.fetch_pc = None
        self.instructions = 0

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
        # reset sequence: two internal cycles, three stack reads, vector
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

    # -- addressing helpers ----------------------------------------------
    def fetch(self):
        v = yield from self.rd(self.pc)
        self.pc = (self.pc + 1) & 0xFFFF
        return v

    def abs_(self):
        lo = yield from self.fetch()
        hi = yield from self.fetch()
        return lo | hi << 8

    def indexed(self, base, idx, write):
        ea = (base + idx) & 0xFFFF
        if write or (ea & 0xFF00) != (base & 0xFF00):
            yield from self.rd((self.pc - 1) & 0xFFFF)     # 65C02 dummy: last operand byte
        return ea

    def ind_y(self, write):
        zp = yield from self.fetch()
        lo = yield from self.rd(zp)
        hi = yield from self.rd((zp + 1) & 0xFF)
        return (yield from self.indexed(lo | hi << 8, self.y, write))

    def operand_ea(self, mode, write=False):
        if mode == "zp":
            return (yield from self.fetch())
        if mode == "abs":
            return (yield from self.abs_())
        if mode == "absx":
            b = yield from self.abs_()
            return (yield from self.indexed(b, self.x, write))
        if mode == "absy":
            b = yield from self.abs_()
            return (yield from self.indexed(b, self.y, write))
        if mode == "indy":
            return (yield from self.ind_y(write))
        raise ValueError(mode)

    def load(self, mode):
        if mode == "imm":
            return (yield from self.fetch())
        ea = yield from self.operand_ea(mode)
        return (yield from self.rd(ea))

    def push(self, v):
        yield from self.wr(0x100 | self.s, v)
        self.s = (self.s - 1) & 0xFF

    def pull(self):
        self.s = (self.s + 1) & 0xFF
        return (yield from self.rd(0x100 | self.s))

    def compare(self, reg, v):
        r = reg - v
        self.p = (self.p & ~C) | (C if reg >= v else 0)
        self.nz(r)

    def adc(self, v):
        c = self.p & C
        r = self.a + v + c
        ov = (~(self.a ^ v) & (self.a ^ r)) & 0x80
        self.p = (self.p & ~(C | V)) | (C if r > 0xFF else 0) | (V if ov else 0)
        self.a = self.nz(r)

    # -- instruction execution ------------------------------------------
    LOADS = {0xA9: ("a", "imm"), 0xA5: ("a", "zp"), 0xAD: ("a", "abs"),
             0xBD: ("a", "absx"), 0xB9: ("a", "absy"), 0xB1: ("a", "indy"),
             0xA2: ("x", "imm"), 0xA6: ("x", "zp"), 0xAE: ("x", "abs"),
             0xA0: ("y", "imm"), 0xA4: ("y", "zp"), 0xAC: ("y", "abs")}
    STORES = {0x85: ("a", "zp"), 0x8D: ("a", "abs"), 0x9D: ("a", "absx"),
              0x99: ("a", "absy"), 0x91: ("a", "indy"),
              0x86: ("x", "zp"), 0x8E: ("x", "abs"),
              0x84: ("y", "zp"), 0x8C: ("y", "abs"),
              0x64: ("z", "zp"), 0x9C: ("z", "abs")}
    ALU = {0xC9: ("cmp", "imm"), 0xC5: ("cmp", "zp"), 0xCD: ("cmp", "abs"),
           0xDD: ("cmp", "absx"), 0xD9: ("cmp", "absy"),
           0xE0: ("cpx", "imm"), 0xC0: ("cpy", "imm"),
           0x29: ("and", "imm"), 0x2D: ("and", "abs"),
           0x09: ("ora", "imm"), 0x0D: ("ora", "abs"),
           0x49: ("eor", "imm"), 0x4D: ("eor", "abs"), 0x5D: ("eor", "absx"),
           0x69: ("adc", "imm"), 0xE9: ("sbc", "imm"),
           0x2C: ("bit", "abs")}
    IMPLIED = {0xE8, 0xC8, 0xCA, 0x88, 0xAA, 0x8A, 0xA8, 0x98, 0x9A, 0xBA,
               0x78, 0x58, 0xD8, 0x38, 0x18, 0xEA, 0x0A, 0x4A, 0x2A, 0x6A}
    BRANCH = {0x10: (N, 0), 0x30: (N, 1), 0x50: (V, 0), 0x70: (V, 1),
              0x90: (C, 0), 0xB0: (C, 1), 0xD0: (ZF, 0), 0xF0: (ZF, 1), 0x80: None}

    def step(self):
        self.fetch_pc = self.pc
        op = yield from self.rd(self.pc, True)
        self.pc = (self.pc + 1) & 0xFFFF
        self.instructions += 1

        if op in self.LOADS:
            reg, mode = self.LOADS[op]
            v = yield from self.load(mode)
            setattr(self, reg, self.nz(v))
        elif op in self.STORES:
            reg, mode = self.STORES[op]
            ea = yield from self.operand_ea(mode, write=True)
            yield from self.wr(ea, 0 if reg == "z" else getattr(self, reg))
        elif op in self.ALU:
            fn, mode = self.ALU[op]
            v = yield from self.load(mode)
            if fn == "cmp":
                self.compare(self.a, v)
            elif fn == "cpx":
                self.compare(self.x, v)
            elif fn == "cpy":
                self.compare(self.y, v)
            elif fn == "and":
                self.a = self.nz(self.a & v)
            elif fn == "ora":
                self.a = self.nz(self.a | v)
            elif fn == "eor":
                self.a = self.nz(self.a ^ v)
            elif fn == "adc":
                self.adc(v)
            elif fn == "sbc":
                self.adc(v ^ 0xFF)
            elif fn == "bit":
                self.p = (self.p & ~(N | V | ZF)) | (v & (N | V)) | (ZF if (self.a & v) == 0 else 0)
        elif op in self.IMPLIED:
            yield from self.rd(self.pc)
            self.implied(op)
        elif op in self.BRANCH:
            off = yield from self.fetch()
            cond = self.BRANCH[op]
            taken = cond is None or (bool(self.p & cond[0]) == bool(cond[1]))
            if taken:
                yield from self.rd(self.pc)
                tgt = (self.pc + (off - 256 if off & 0x80 else off)) & 0xFFFF
                if (tgt & 0xFF00) != (self.pc & 0xFF00):
                    yield from self.rd(self.pc)
                self.pc = tgt
        elif op in (0xE6, 0xC6):            # INC/DEC zp
            ea = yield from self.fetch()
            v = yield from self.rd(ea)
            yield from self.rd(ea)
            v = (v + (1 if op == 0xE6 else -1)) & 0xFF
            yield from self.wr(ea, self.nz(v))
        elif op == 0x4C:                     # JMP abs
            self.pc = yield from self.abs_()
        elif op == 0x20:                     # JSR
            lo = yield from self.fetch()
            yield from self.rd(0x100 | self.s)
            yield from self.push(self.pc >> 8)
            yield from self.push(self.pc & 0xFF)
            hi = yield from self.rd(self.pc)
            self.pc = lo | hi << 8
        elif op == 0x60:                     # RTS
            yield from self.rd(self.pc)
            yield from self.rd(0x100 | self.s)
            lo = yield from self.pull()
            hi = yield from self.pull()
            self.pc = lo | hi << 8
            yield from self.rd(self.pc)
            self.pc = (self.pc + 1) & 0xFFFF
        elif op in (0x48, 0x08):             # PHA / PHP
            yield from self.rd(self.pc)
            yield from self.push(self.a if op == 0x48 else self.p | B | U)
        elif op in (0x68, 0x28):             # PLA / PLP
            yield from self.rd(self.pc)
            yield from self.rd(0x100 | self.s)
            v = yield from self.pull()
            if op == 0x68:
                self.a = self.nz(v)
            else:
                self.p = v | U
        elif op == 0x00:                     # BRK
            yield from self.fetch()
            yield from self.push(self.pc >> 8)
            yield from self.push(self.pc & 0xFF)
            yield from self.push(self.p | B | U)
            lo = yield from self.rd(0xFFFE)
            hi = yield from self.rd(0xFFFF)
            self.p = (self.p | I) & ~D
            self.pc = lo | hi << 8
        elif op == 0x40:                     # RTI
            yield from self.rd(self.pc)
            yield from self.rd(0x100 | self.s)
            self.p = (yield from self.pull()) | U
            lo = yield from self.pull()
            hi = yield from self.pull()
            self.pc = lo | hi << 8
        else:
            raise UnknownOpcode(f"opcode ${op:02X} at ${self.fetch_pc:04X}")

    def implied(self, op):
        if op == 0xE8:
            self.x = self.nz(self.x + 1)
        elif op == 0xC8:
            self.y = self.nz(self.y + 1)
        elif op == 0xCA:
            self.x = self.nz(self.x - 1)
        elif op == 0x88:
            self.y = self.nz(self.y - 1)
        elif op == 0xAA:
            self.x = self.nz(self.a)
        elif op == 0x8A:
            self.a = self.nz(self.x)
        elif op == 0xA8:
            self.y = self.nz(self.a)
        elif op == 0x98:
            self.a = self.nz(self.y)
        elif op == 0x9A:
            self.s = self.x
        elif op == 0xBA:
            self.x = self.nz(self.s)
        elif op == 0x78:
            self.p |= I
        elif op == 0x58:
            self.p &= ~I
        elif op == 0xD8:
            self.p &= ~D
        elif op == 0x38:
            self.p |= C
        elif op == 0x18:
            self.p &= ~C
        elif op == 0x0A:
            self.p = (self.p & ~C) | (C if self.a & 0x80 else 0)
            self.a = self.nz(self.a << 1)
        elif op == 0x4A:
            self.p = (self.p & ~C) | (self.a & 1)
            self.a = self.nz(self.a >> 1)
        elif op == 0x2A:
            c = self.p & C
            self.p = (self.p & ~C) | (C if self.a & 0x80 else 0)
            self.a = self.nz(self.a << 1 | c)
        elif op == 0x6A:
            c = self.p & C
            self.p = (self.p & ~C) | (self.a & 1)
            self.a = self.nz(self.a >> 1 | c << 7)


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
                self.gen = iter(lambda: ("R", addr, None, False), None)
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
