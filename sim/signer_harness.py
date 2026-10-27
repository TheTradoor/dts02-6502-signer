"""Run signing-firmware routines on the fast emulator (sim/emu.py)."""

from . import signer_fw as F
from .emu import Machine

_BUILD = {}


def build(double_sign=True, verify=True):
    key = (double_sign, verify)
    if key not in _BUILD:
        _BUILD[key] = F.build(double_sign, verify)
    return _BUILD[key]


class Fw:
    def __init__(self, double_sign=True, verify=True):
        self.img, self.syms, _ = build(double_sign, verify)
        self.m = Machine(rom=self.img)
        self.m.m.s = 0xFF

    def setp(self, zp, addr):
        self.m.load(F.ZP[zp] if isinstance(zp, str) else zp, bytes([addr & 0xFF, addr >> 8]))

    def call(self, routine, max_cycles=4 * 10**9):
        m = self.m.m
        m.s = 0xFF
        m.pc = self.syms[f"t_{routine}"]
        c0 = m.cycles
        r = self.m.run(max_cycles=max_cycles)
        if r != Machine.STP:
            raise RuntimeError(f"{routine}: halt {r} at pc ${m.pc:04X} (last ${m.last_pc:04X})")
        return m.cycles - c0

    def put(self, addr, b):
        self.m.load(addr, b)

    def get(self, addr, n):
        return self.m.read(addr, n)

    # ---- field helpers
    def fe_op(self, op, a, b=None):
        A, B, R = F.fe("TA"), F.fe("TB"), F.fe("TC")
        self.put(A, F.le32(a))
        self.setp("P1", A)
        if b is not None:
            self.put(B, F.le32(b))
            self.setp("P2", B)
        self.setp("P3", R)
        cyc = self.call(op)
        return int.from_bytes(self.get(R, 32), "little"), cyc
