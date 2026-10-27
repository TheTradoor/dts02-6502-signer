"""Pin-level models of every IC family in the DTS-02 BOM.

Each model is addressed by DIP pin number exactly as in the manufacturer
pinout, so the board netlist (board.py) is a literal transcription of the
spec's pin tables.  Pin roles ("in", "out", "tri", "vcc", "gnd") drive the
static electrical-rule check.
"""

from . import timing as T
from .engine import X, Z, v_and, v_nand, v_not, v_or


class Part:
    kind = "?"
    roles = {}

    def __init__(self, sim, name, pins):
        self.sim = sim
        self.name = name
        self.pins = {}
        remap = sim.pin_remap
        if remap is not None:
            # a physical layout may also connect pins the schematic left open
            pins = dict(pins)
            for (pname, pin) in remap:
                if pname == name and pin not in pins:
                    pins[pin] = None
        for pin, netname in pins.items():
            if remap is not None:
                netname = remap.get((name, pin), netname)
            if netname is None:
                continue
            self.pins[pin] = sim.net(netname)
        for pin, net in self.pins.items():
            if self.roles.get(pin) == "in":
                net.loads.append(self)
        self._out = {}
        self._delays = {}
        sim.add_part(self)

    # per-instance fixed delay drawn from a (min, max) range
    def dly(self, key, rng):
        d = self._delays.get(key)
        if d is None:
            lo, hi = rng
            d = self.sim.rng.uniform(lo, hi)
            self._delays[key] = d
        return d

    def inp(self, pin):
        net = self.pins.get(pin)
        if net is None:
            return X
        v = net.value
        return v if v in (0, 1) else X

    def out(self, pin, value, t, delay):
        net = self.pins.get(pin)
        if net is None:
            return
        if self._out.get(pin, "init") == value:
            return
        self._out[pin] = value
        self.sim.drive(net, (self.name, pin), value, t + delay)

    def evaluate(self, t):
        pass

    def start(self, t):
        """Called once at t=0 so outputs take their initial value."""
        self.evaluate(t)


# ---------------------------------------------------------------------------
# combinational gate packages
# ---------------------------------------------------------------------------

QUAD2 = [((1, 2), 3), ((4, 5), 6), ((9, 10), 8), ((12, 13), 11)]
HEX1 = [((1,), 2), ((3,), 4), ((5,), 6), ((9,), 8), ((11,), 10), ((13,), 12)]
TRI3 = [((1, 2, 13), 12), ((3, 4, 5), 6), ((9, 10, 11), 8)]
NAND8 = [((1, 2, 3, 4, 5, 6, 11, 12), 8)]


def _roles(gates, vcc, gnd):
    r = {vcc: "vcc", gnd: "gnd"}
    for ins, o in gates:
        for i in ins:
            r[i] = "in"
        r[o] = "out"
    return r


class Gates(Part):
    GATES = []
    FN = None
    VCC, GND = 14, 7

    def __init__(self, sim, name, pins, family, skip=()):
        self.roles = _roles(self.GATES, self.VCC, self.GND)
        for o in skip:
            self.roles[o] = "behavioural"
        self.kind = family
        self.family = family
        self.skip = set(skip)        # gate outputs modelled behaviourally elsewhere
        self._hyst = {}
        super().__init__(sim, name, pins)

    def inp(self, pin):
        v = super().inp(pin)
        if self.family in ("HC14", "AC14") and v == X:
            # Schmitt input: a slow RC edge between thresholds keeps the old level
            net = self.pins.get(pin)
            if net is not None and net.value == Z:
                return self._hyst.get(pin, X)
        self._hyst[pin] = v
        return v

    def evaluate(self, t):
        fn = type(self).FN
        for ins, o in self.GATES:
            if o in self.skip or o not in self.pins:
                continue
            v = fn(*[self.inp(i) for i in ins])
            self.out(o, v, t, self.dly(("tpd", o), T.TPD[self.family]))


class Inv6(Gates):
    GATES = HEX1
    FN = staticmethod(v_not)


class Nand4x2(Gates):
    GATES = QUAD2
    FN = staticmethod(v_nand)


class And4x2(Gates):
    GATES = QUAD2
    FN = staticmethod(v_and)


class Or4x2(Gates):
    GATES = QUAD2
    FN = staticmethod(v_or)


class Nand3x3(Gates):
    GATES = TRI3
    FN = staticmethod(v_nand)


class Nand8(Gates):
    GATES = NAND8
    FN = staticmethod(v_nand)


# ---------------------------------------------------------------------------
# HCT74 dual D flip-flop
# ---------------------------------------------------------------------------

class HCT74(Part):
    kind = "HCT74"
    FF = [dict(clr=1, d=2, clk=3, pre=4, q=5, qn=6),
          dict(clr=13, d=12, clk=11, pre=10, q=9, qn=8)]
    roles = {14: "vcc", 7: "gnd"}
    for _f in FF:
        roles.update({_f["clr"]: "in", _f["d"]: "in", _f["clk"]: "in",
                      _f["pre"]: "in", _f["q"]: "out", _f["qn"]: "out"})

    def __init__(self, sim, name, pins):
        super().__init__(sim, name, pins)
        self.state = [X, X]
        self.prev_clk = [X, X]

    def evaluate(self, t):
        for i, f in enumerate(self.FF):
            if f["q"] not in self.pins and f["qn"] not in self.pins:
                continue
            clr, pre, clk = self.inp(f["clr"]), self.inp(f["pre"]), self.inp(f["clk"])
            q = self.state[i]
            async_ = False
            if clr == 0 and pre == 0:
                qv, qnv = 1, 1
                async_ = True
            elif clr == 0:
                q, async_ = 0, True
            elif pre == 0:
                q, async_ = 1, True
            elif clr == X or pre == X:
                q = X
            if not async_ and self.prev_clk[i] == 0 and clk == 1:
                dnet = self.pins.get(f["d"])
                self.sim.check_sample(t, [dnet], T.HCT74["tsu"], T.HCT74["th"],
                                      f"{self.name}.CLK{i}")
                q = self.inp(f["d"])
            elif not async_ and clk == X and self.prev_clk[i] != X:
                q = X
            self.prev_clk[i] = clk
            self.state[i] = q
            if not (clr == 0 and pre == 0):
                qv, qnv = q, v_not(q)
            rng = T.HCT74["clr_q"] if async_ else T.HCT74["clk_q"]
            dq = self.dly(("q", i, async_), rng)
            # Q and /Q of one flip-flop share a path: matched within +-QQN_SKEW
            dqn = dq + self.dly(("qn_skew", i, async_), (-T.QQN_SKEW, T.QQN_SKEW))
            self.out(f["q"], qv, t, dq)
            self.out(f["qn"], qnv, t, max(0.0, dqn))


# ---------------------------------------------------------------------------
# HCT273 octal D flip-flop with clear
# ---------------------------------------------------------------------------

class HCT273(Part):
    kind = "HCT273"
    D = [3, 4, 7, 8, 13, 14, 17, 18]
    Q = [2, 5, 6, 9, 12, 15, 16, 19]
    roles = {20: "vcc", 10: "gnd", 1: "in", 11: "in"}
    roles.update({p: "in" for p in D})
    roles.update({p: "out" for p in Q})

    def __init__(self, sim, name, pins):
        super().__init__(sim, name, pins)
        self.q = [X] * 8
        self.prev = X
        self.clock_edges = 0

    def evaluate(self, t):
        clr, clk = self.inp(1), self.inp(11)
        async_ = False
        if clr == 0:
            self.q = [0] * 8
            async_ = True
        elif clr == X:
            self.q = [X] * 8
        elif self.prev == 0 and clk == 1:
            self.clock_edges += 1
            self.sim.check_sample(t, [self.pins[p] for p in self.D if p in self.pins],
                                  T.HCT273["tsu"], T.HCT273["th"], f"{self.name}.CLK")
            self.q = [self.inp(p) for p in self.D]
        self.prev = clk
        rng = T.HCT273["clr_q"] if async_ else T.HCT273["clk_q"]
        for i, p in enumerate(self.Q):
            self.out(p, self.q[i], t, self.dly(("q", i, async_), rng))


# ---------------------------------------------------------------------------
# HCT138 3-to-8 decoder
# ---------------------------------------------------------------------------

class HCT138(Part):
    kind = "HCT138"
    Y = [15, 14, 13, 12, 11, 10, 9, 7]
    roles = {16: "vcc", 8: "gnd", 1: "in", 2: "in", 3: "in", 4: "in", 5: "in", 6: "in"}
    roles.update({p: "out" for p in Y})

    def evaluate(self, t):
        a, b, c = self.inp(1), self.inp(2), self.inp(3)
        en = v_and(v_not(self.inp(4)), v_not(self.inp(5)), self.inp(6))
        for i, p in enumerate(self.Y):
            if en == 0:
                v = 1
            elif X in (a, b, c) or en == X:
                v = X
            else:
                v = 0 if (a | (b << 1) | (c << 2)) == i else 1
            self.out(p, v, t, self.dly(("y", i), T.TPD["HCT138"]))


# ---------------------------------------------------------------------------
# HCT157 quad 2:1 mux
# ---------------------------------------------------------------------------

class HCT157(Part):
    kind = "HCT157"
    CH = [(2, 3, 4), (5, 6, 7), (11, 10, 9), (14, 13, 12)]
    roles = {16: "vcc", 8: "gnd", 1: "in", 15: "in"}
    for _a, _b, _y in CH:
        roles.update({_a: "in", _b: "in", _y: "out"})

    def evaluate(self, t):
        s, e = self.inp(1), self.inp(15)
        for i, (a, b, y) in enumerate(self.CH):
            if y not in self.pins:
                continue
            if e == 1:
                v = 0
            elif e == X:
                v = X
            elif s == 0:
                v = self.inp(a)
            elif s == 1:
                v = self.inp(b)
            else:
                va, vb = self.inp(a), self.inp(b)
                v = va if va == vb else X
            self.out(y, v, t, self.dly(("y", i), T.TPD["HCT157"]))


# ---------------------------------------------------------------------------
# three-state buffers
# ---------------------------------------------------------------------------

def _tri_delay(part, key, old, new, fam):
    if new == Z:
        return part.dly(("dis",) + key, T.TDIS[fam])
    if old in (Z, "init", None):
        return part.dly(("en",) + key, T.TEN[fam])
    return part.dly(("tpd",) + key, T.TPD.get(fam, (5, 35)))


class HCT541(Part):
    kind = "HCT541"
    A = [2, 3, 4, 5, 6, 7, 8, 9]
    Y = [18, 17, 16, 15, 14, 13, 12, 11]
    roles = {20: "vcc", 10: "gnd", 1: "in", 19: "in"}
    roles.update({p: "in" for p in A})
    roles.update({p: "tri" for p in Y})

    def evaluate(self, t):
        en = v_and(v_not(self.inp(1)), v_not(self.inp(19)))
        for i, (a, y) in enumerate(zip(self.A, self.Y)):
            if en == 0:
                v = Z
            elif en == X:
                v = X
            else:
                v = self.inp(a)
            old = self._out.get(y, "init")
            if old == v:
                continue
            self.out(y, v, t, _tri_delay(self, (i,), old, v, "HCT541"))


class HCT244(Part):
    kind = "HCT244"
    CH = [(1, 2, 18), (1, 4, 16), (1, 6, 14), (1, 8, 12),
          (19, 11, 9), (19, 13, 7), (19, 15, 5), (19, 17, 3)]
    roles = {20: "vcc", 10: "gnd", 1: "in", 19: "in"}
    for _g, _a, _y in CH:
        roles.update({_a: "in", _y: "tri"})

    def evaluate(self, t):
        for i, (g, a, y) in enumerate(self.CH):
            if y not in self.pins:
                continue
            en = v_not(self.inp(g))
            v = Z if en == 0 else (X if en == X else self.inp(a))
            old = self._out.get(y, "init")
            if old == v:
                continue
            self.out(y, v, t, _tri_delay(self, (i,), old, v, "HCT244"))


class HCT573(Part):
    kind = "HCT573"
    D = [2, 3, 4, 5, 6, 7, 8, 9]
    Q = [19, 18, 17, 16, 15, 14, 13, 12]
    roles = {20: "vcc", 10: "gnd", 1: "in", 11: "in"}
    roles.update({p: "in" for p in D})
    roles.update({p: "tri" for p in Q})

    def __init__(self, sim, name, pins):
        super().__init__(sim, name, pins)
        self.q = [X] * 8
        self.prev_le = X

    def evaluate(self, t):
        le, oe = self.inp(11), self.inp(1)
        if le == 1:
            self.q = [self.inp(p) for p in self.D]
        elif le == 0 and self.prev_le == 1:
            self.sim.check_sample(t, [self.pins[p] for p in self.D],
                                  T.HCT573["tsu"], T.HCT573["th"], f"{self.name}.LE")
        elif le == X:
            self.q = [X] * 8
        self.prev_le = le
        for i, y in enumerate(self.Q):
            if oe == 1:
                v = Z
            elif oe == X:
                v = X
            else:
                v = self.q[i]
            old = self._out.get(y, "init")
            if old == v:
                continue
            if v == Z:
                d = self.dly(("dis", i), T.TDIS["HCT573"])
            elif old in (Z, "init"):
                d = self.dly(("en", i), T.TEN["HCT573"])
            else:
                d = self.dly(("dq", i), T.HCT573["d_q"])
            self.out(y, v, t, d)


# ---------------------------------------------------------------------------
# HC595 shift register with output latch
# ---------------------------------------------------------------------------

class HC595(Part):
    kind = "HC595"
    QPINS = [15, 1, 2, 3, 4, 5, 6, 7]     # QA..QH
    roles = {16: "vcc", 8: "gnd", 10: "in", 11: "in", 12: "in", 13: "in", 14: "in",
             9: "out"}
    roles.update({p: "tri" for p in QPINS})

    def __init__(self, sim, name, pins):
        super().__init__(sim, name, pins)
        self.sr = [X] * 8      # stage A..H; power-up contents unknown
        self.st = [X] * 8      # storage register
        self.prev_sck = X
        self.prev_rck = X

    def evaluate(self, t):
        clr, sck, rck, oe = self.inp(10), self.inp(11), self.inp(12), self.inp(13)
        sck_rise = self.prev_sck == 0 and sck == 1
        rck_rise = self.prev_rck == 0 and rck == 1
        if clr == 0:
            self.sr = [0] * 8
        elif sck_rise:
            self.sim.check_sample(t, [self.pins[14]], T.HC595["tsu"], T.HC595["th"],
                                  f"{self.name}.SRCLK")
            self.sr = [self.inp(14)] + self.sr[:7]
        if rck_rise:
            if sck_rise:
                self.sim.violation(t, "simultaneous", self.name, "SRCLK and RCLK rose together")
            self.st = list(self.sr)
        self.prev_sck, self.prev_rck = sck, rck
        self.out(9, self.sr[7], t, self.dly("qh", T.HC595["clk_q"]))
        for i, p in enumerate(self.QPINS):
            if oe == 1:
                v = Z
            elif oe == X:
                v = X
            else:
                v = self.st[i]
            old = self._out.get(p, "init")
            if old == v:
                continue
            self.out(p, v, t, self.dly(("q", i, v == Z), T.HC595["oe"]))


# ---------------------------------------------------------------------------
# memories
# ---------------------------------------------------------------------------

SRAM_ADDR = [10, 9, 8, 7, 6, 5, 4, 3, 25, 24, 21, 23, 2, 26, 1]   # A0..A14
EPROM_ADDR = [10, 9, 8, 7, 6, 5, 4, 3, 25, 24, 21, 23, 2, 26, 27]
DQ = [11, 12, 13, 15, 16, 17, 18, 19]


class _Memory(Part):
    ADDR = []
    TP = {}

    def read_addr(self):
        a = 0
        for i, p in enumerate(self.ADDR):
            v = self.inp(p)
            if v == X:
                return None
            a |= v << i
        return a

    def _drive_outputs(self, t, en, addr, t_valid, t_x):
        if not en:
            for i, p in enumerate(DQ):
                if self._out.get(p, "init") not in (Z, "init"):
                    self.out(p, Z, t, self.dly(("hz", i), self.TP_HZ))
            return
        val = None if addr is None else self.mem[addr]
        for i, p in enumerate(DQ):
            self._out[p] = None  # force scheduling
            self.out(p, X, t_x, 0)
            self._out[p] = None
            bit = X if val is None else (val >> i) & 1
            self.out(p, bit, t_valid, 0)


class SRAM62256(_Memory):
    kind = "AS6C62256"
    ADDR = SRAM_ADDR
    roles = {28: "vcc", 14: "gnd", 20: "in", 22: "in", 27: "in"}
    roles.update({p: "in" for p in SRAM_ADDR})
    roles.update({p: "tri" for p in DQ})

    def __init__(self, sim, name, pins):
        super().__init__(sim, name, pins)
        rng = sim.rng
        self.mem = [rng.randrange(256) for _ in range(32768)]   # power-up garbage
        self.TP_HZ = T.SRAM["tOHZ"]
        self.sig = None
        self.t_addr = 0
        self.t_ce = 0
        self.t_oe = 0
        self.prev_ce = X
        self.prev_oe = X
        self.wr_active = False
        self.wr_start = 0
        self.wr_addr = None
        self.writes = []      # (time, addr, value) log
        # DQ pins are also inputs for writes; register as loads
        for p in DQ:
            if p in self.pins:
                self.pins[p].loads.append(self)

    def evaluate(self, t):
        P = T.SRAM
        addr = self.read_addr()
        ce, oe, we = self.inp(20), self.inp(22), self.inp(27)
        if ce != self.prev_ce and ce == 0:
            self.t_ce = t
        if oe != self.prev_oe and oe == 0:
            self.t_oe = t
        self.prev_ce, self.prev_oe = ce, oe
        if ce == 0 and we == X:
            self.sim.violation(t, "write-enable-X", self.name, "WE unknown while selected")
        # ---- write tracking
        writing = (ce == 0 and we == 0)
        if writing and not self.wr_active:
            self.wr_active = True
            self.wr_start = t
            self.wr_addr = addr
            for p in self.ADDR:
                n = self.pins[p]
                if n.last_change > t - P["tAS"]:
                    self.sim.violation(t, "setup", f"{self.name}.write", f"address {n.name} not set up")
        elif writing and addr != self.wr_addr:
            self.sim.violation(t, "addr-during-write", self.name,
                               f"address changed {self.wr_addr} -> {addr} with WE active")
            self.wr_addr = None
        elif not writing and self.wr_active:
            self.wr_active = False
            if t - self.wr_start < P["tWP"]:
                self.sim.violation(t, "pulse-width", f"{self.name}.WE",
                                   f"write pulse {t - self.wr_start:.1f} ns < {P['tWP']}")
            dnets = [self.pins[p] for p in DQ]
            self.sim.check_sample(t, dnets, P["tDW"], P["tDH"], f"{self.name}.write-data")
            vals = [n.value for n in dnets]
            if self.wr_addr is None or any(v not in (0, 1) for v in vals):
                if self.wr_addr is not None:
                    self.mem[self.wr_addr] = None
                self.writes.append((t, self.wr_addr, None))
            else:
                v = sum(b << i for i, b in enumerate(vals))
                self.mem[self.wr_addr] = v
                self.writes.append((t, self.wr_addr, v))
        # ---- read path
        en = (ce == 0 and oe == 0 and we == 1)
        sig = (en, addr if en else None)
        if sig == self.sig:
            return
        changed_addr = self.sig is None or self.sig[1] != addr
        was_en = self.sig is not None and self.sig[0]
        self.sig = sig
        if changed_addr:
            self.t_addr = t
        if not en:
            self._drive_outputs(t, False, None, 0, 0)
            return
        t_valid = max(self.t_addr + P["tAA"], self.t_ce + P["tACE"], self.t_oe + P["tOE"], t)
        if not was_en:
            # turn-on is limited by whichever enable arrived last
            t_x = t + (P["tCLZ"] if self.t_ce >= self.t_oe else P["tOLZ"])
        else:
            t_x = t + P["tOH"]
        if t_x > t_valid:
            t_x = t_valid
        self._drive_outputs(t, True, addr, t_valid, t_x)


class EPROM27256(_Memory):
    kind = "AT27C256R"
    ADDR = EPROM_ADDR
    roles = {28: "vcc", 14: "gnd", 1: "in", 20: "in", 22: "in"}
    roles.update({p: "in" for p in EPROM_ADDR})
    roles.update({p: "tri" for p in DQ})

    def __init__(self, sim, name, pins, image):
        super().__init__(sim, name, pins)
        assert len(image) == 32768
        self.mem = list(image)
        self.TP_HZ = T.EPROM["tDF"]
        self.sig = None
        self.t_addr = self.t_ce = self.t_oe = 0
        self.prev_ce = self.prev_oe = X

    def evaluate(self, t):
        P = T.EPROM
        addr = self.read_addr()
        ce, oe = self.inp(20), self.inp(22)
        if ce != self.prev_ce and ce == 0:
            self.t_ce = t
        if oe != self.prev_oe and oe == 0:
            self.t_oe = t
        self.prev_ce, self.prev_oe = ce, oe
        if self.inp(1) != 1:
            self.sim.violation(t, "vpp", self.name, "VPP not at VCC")
        en = (ce == 0 and oe == 0)
        if ce == X or oe == X:
            # unknown enable: conservatively drive unknown data
            en = True
        sig = (en, addr if en else None)
        if sig == self.sig:
            return
        changed_addr = self.sig is None or self.sig[1] != addr
        was_en = self.sig is not None and self.sig[0]
        self.sig = sig
        if changed_addr:
            self.t_addr = t
        if not en:
            self._drive_outputs(t, False, None, 0, 0)
            return
        t_valid = max(self.t_addr + P["tACC"], self.t_ce + P["tCE"], self.t_oe + P["tOE"], t)
        t_x = t if was_en else t + 1
        self._drive_outputs(t, True, addr, t_valid, min(t_x, t_valid))


# ---------------------------------------------------------------------------
# passives and loads
# ---------------------------------------------------------------------------

class SeriesR(Part):
    """Series resistor treated as a zero-delay directional connection."""
    kind = "RSER"
    roles = {1: "in", 2: "tri"}

    def evaluate(self, t):
        v = self.pins[1].value
        if v not in (0, 1, X):
            v = Z
        self.out(2, v, t, 0)


class SC56Digit(Part):
    """Kingbright SC56-11 common-cathode digit; front-view pin numbers."""
    kind = "SC56-11"
    SEG_PIN = {"a": 7, "b": 6, "c": 4, "d": 2, "e": 1, "f": 9, "g": 10, "dp": 5}
    roles = {p: "in" for p in SEG_PIN.values()}
    roles.update({3: "gnd", 8: "gnd"})

    def lit(self):
        """Return 8-bit mask (bit0=a .. bit7=dp) of lit segments; None if unknown."""
        m = 0
        for i, s in enumerate(["a", "b", "c", "d", "e", "f", "g", "dp"]):
            net = self.pins.get(self.SEG_PIN[s])
            v = net.value if net else 0
            if v == 1:
                m |= 1 << i
            elif v == X:
                return None
        return m


class LoadOnly(Part):
    kind = "LOAD"

    def __init__(self, sim, name, pins):
        self.roles = {p: "in" for p in pins}
        super().__init__(sim, name, pins)


# ---------------------------------------------------------------------------
# behavioural sources (oscillator, supervisor, RC nodes, switches)
# ---------------------------------------------------------------------------

class Source(Part):
    """A harness-controlled driver on one net (pin 1)."""
    kind = "SRC"
    roles = {1: "out"}

    def __init__(self, sim, name, net, value=Z):
        super().__init__(sim, name, {1: net})
        self.value = value

    def start(self, t):
        self.set(self.value, t)

    def set(self, value, t=None):
        if t is None:
            t = self.sim.now
        self.value = value
        self._out.pop(1, None)
        self.out(1, value, t, 0)


class Oscillator(Part):
    """U5 gate 1 relaxation oscillator, replaced by an ideal square wave of
    the requested frequency (the frequency itself is analysed in analog.py)."""
    kind = "OSC"
    roles = {1: "out"}

    def __init__(self, sim, name, net, freq_hz, duty=0.5):
        super().__init__(sim, name, {1: net})
        self.period = 1e9 / freq_hz
        self.duty = duty

    def start(self, t):
        self._tick(t, 0)

    def _tick(self, t, v):
        self._out.pop(1, None)
        self.out(1, v, t, 0)
        nxt = self.period * (self.duty if v == 1 else 1 - self.duty)
        self.sim.schedule(t + nxt, self._tick, 1 - v)


class ResetRC(Part):
    """RESET_RC node: 100k/1uF charged from COLD_CLEAR_N, fast discharge via
    1k+1N4148 into COLD_CLEAR_N, CPU RESET button to GND through 1k.
    Output is the logic level seen by the HC14 Schmitt input (U5 pin 9)."""
    kind = "RESETRC"
    roles = {1: "in", 2: "in", 3: "out"}

    def __init__(self, sim, name, cold_net, button_net, out_net, delay_ns):
        super().__init__(sim, name, {1: cold_net, 2: button_net, 3: out_net})
        self.delay = delay_ns
        self.version = 0

    def evaluate(self, t):
        cold = self.inp(1)
        pressed = self.inp(2) == 0
        self.version += 1
        if cold != 1 or pressed:
            self.out(3, 0, t, 1000)       # ~1k * 1uF discharge, threshold crossed in ~1 us
        else:
            v = self.version
            self.sim.schedule(t + self.delay, self._charged, v)

    def _charged(self, t, v):
        if v == self.version:
            self.out(3, 1, t, 0)
