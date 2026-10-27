"""Hole-level breadboard layout, jumper list and physical connectivity.

The generator places every IC exactly where spec Section 3.3 says, puts
every passive into specific holes (or onto the soldered LED/switch/power
panels), and routes each net with point-to-point jumpers, tracking every
hole on every terminal strip and rail.  `connectivity()` then forgets the
schematic and rebuilds nets purely from what is physically joined: holes in
one 5-hole strip, holes in one rail half, and the two ends of each wire.
That physical netlist is checked against the schematic (shorts, opens,
supply) and can be fed to the simulator instead of the schematic.

Breadboard model (830-tie, 63 rows): rows 1-63, strips a-e and f-j per row,
four rails (L-, L+ on the left, R+, R- on the right) split between rows 31
and 32, 25 usable holes per rail half.
"""

import math
from collections import defaultdict

from . import checks
from .board import C_D, GND, KEYS, P_D, SW, VCC, build

COLS_L, COLS_R = "abcde", "fghij"
RAILS = ("L-", "L+", "R+", "R-")
SPLIT_ROW = 31
RAIL_HOLES_PER_HALF = 25
BOARDS = ["BB1", "BB10", "BB2", "BB3", "BB4", "BB9", "BB5", "BB6", "BB7", "BB8"]
PANELS = ["LED", "SW", "PWR"]
GRID = {"BB1": (0, 0), "BB10": (1, 0), "BB2": (2, 0), "BB3": (3, 0), "BB4": (4, 0),
        "BB9": (5, 0), "BB5": (0, 1), "BB6": (1, 1), "BB7": (2, 1), "BB8": (3, 1),
        "SW": (4, 1), "PWR": (5, 1)}
PITCH_X, PITCH_Y = 70.0, 180.0           # 55 x 165 mm boards, 15 mm gaps (Section 3.2)
X_MM = {"L-": 2.5, "L+": 5.0, "a": 10.0, "b": 12.54, "c": 15.08, "d": 17.62, "e": 20.16,
        "f": 27.78, "g": 30.32, "h": 32.86, "i": 35.40, "j": 37.94, "R+": 44.0, "R-": 46.5}
# spec Section 3.3 reserved landing rows
RESERVED = {("BB1", r) for r in range(2, 12)} | {("BB10", r) for r in range(36, 51)}

PIN_PF, STRIP_PF, WIRE_PF_PER_MM = 5.0, 2.5, 0.04
MAX_LEAD_SPAN_MM = 30.0        # longest sensible bent-lead span for an axial part
NO_CONNECT = None


def side_of(col):
    return "L" if col in COLS_L else "R"


def strip_key(h):
    b, c, r = h
    if c in COLS_L or c in COLS_R:
        return (b, r, side_of(c))
    if c in RAILS:
        return (b, c, 0 if r <= SPLIT_ROW else 1)
    return (b, "pad", r)                 # panel pad: r is the net name


ACTIVE_GRID = dict(GRID)


def hole_xy(h):
    GRID = ACTIVE_GRID
    b, c, r = h
    if b == "LED":
        digit = _LED_X.get(r)
        x = 10 + 15.24 * digit if digit is not None else 125.0
        return (x, 2 * PITCH_Y + 20)
    gx, gy = GRID[b]
    if c == "pad":
        return (gx * PITCH_X + 27, gy * PITCH_Y + 80)
    return (gx * PITCH_X + X_MM[c], gy * PITCH_Y + 5 + (r - 1) * 2.54)


_LED_X = {}
for _b in range(4):
    for _j in range(4):
        _u, _d = 21 + 4 * _b + _j, 4 * _b + _j
        for _s in ["a", "b", "c", "d", "e", "f", "g", "dp"]:
            _LED_X[f"U{_u}_Q{_s}"] = _d
            _LED_X[f"D{_d}_{_s}"] = _d


def wire_mm(h1, h2):
    (x1, y1), (x2, y2) = hole_xy(h1), hole_xy(h2)
    return 1.15 * math.hypot(x2 - x1, y2 - y1) + 10.0     # routing slack + stripped ends


def fmt(h):
    b, c, r = h
    if c == "pad":
        return f"{b}:{r}"
    if c in RAILS:
        return f"{b}:{c}@{r}"
    return f"{b}:{c}{r}"


class Layout:
    def __init__(self):
        self.used = {}                     # hole -> owner
        self.strip_net = {}                # (board,row,side) -> net ("" = pin, no net)
        self.rail_count = defaultdict(int)
        self.pin_hole = {}                 # (ic, pin) -> hole
        self.items = []                    # dicts: kind, ref, value, leads=[(hole, net)]
        self.wires = []                    # (h1, h2, net)
        self.errors = []
        self.terminals = {}                # schematic: (part, pin) -> net
        self.sim_hole = {}                 # (sim part, pin) -> hole

    # ---- hole allocation ----------------------------------------------------
    def take(self, h, owner):
        if h in self.used:
            self.errors.append(f"hole {fmt(h)} used twice ({self.used[h]} / {owner})")
        self.used[h] = owner
        return h

    def free_in_strip(self, key, owner):
        b, r, side = key
        for c in (COLS_L if side == "L" else COLS_R):
            h = (b, c, r)
            if h not in self.used:
                return self.take(h, owner)
        self.errors.append(f"hole budget: strip {b} row {r}{side} full ({owner})")
        return self.take((b, (COLS_L if side == "L" else COLS_R)[0], r), owner + "!overflow")

    def rail(self, b, rail, near_row, owner):
        half = 0 if near_row <= SPLIT_ROW else 1
        rows = range(1, SPLIT_ROW + 1) if half == 0 else range(SPLIT_ROW + 1, 64)
        if self.rail_count[(b, rail, half)] >= RAIL_HOLES_PER_HALF:
            self.errors.append(f"hole budget: rail {b} {rail} half {half} full ({owner})")
        for r in sorted(rows, key=lambda r: abs(r - near_row)):
            h = (b, rail, r)
            if h not in self.used:
                self.rail_count[(b, rail, half)] += 1
                return self.take(h, owner)
        raise RuntimeError("rail exhausted")

    def landing(self, b, net, owner, rows=None, side=None, near_row=32, near_hole=None):
        """A fresh empty strip on board b dedicated to net, nearest near_row
        (or nearest the physical position of near_hole)."""
        cands = rows if rows is not None else sorted(range(1, 64), key=lambda r: abs(r - near_row))
        if near_hole is not None and rows is None:
            opts = [(r, sd) for r in range(1, 64) for sd in "LR" if (b, r) not in RESERVED]
            opts.sort(key=lambda o: math.dist(hole_xy(near_hole),
                                              hole_xy((b, "c" if o[1] == "L" else "h", o[0]))))
            for r, sd in opts:
                key = (b, r, sd)
                cols = COLS_L if sd == "L" else COLS_R
                if key in self.strip_net or any((b, c, r) in self.used for c in cols):
                    continue
                self.strip_net[key] = net
                return key
        for r in cands:
            if rows is None and (b, r) in RESERVED:
                continue
            for sd in ([side] if side else ["L", "R"]):
                key = (b, r, sd)
                if key in self.strip_net:
                    continue
                cols = COLS_L if sd == "L" else COLS_R
                if any((b, c, r) in self.used for c in cols):
                    continue
                self.strip_net[key] = net
                return key
        raise RuntimeError(f"no free strip on {b} for {net}")

    def strips_of(self, b, net):
        return [k for k, n in self.strip_net.items() if n == net and k[0] == b]

    def attach(self, net, b, owner, near_row=32, side=None, rows=None, near_hole=None):
        """Return a hole on board/panel b that is (intended to be) on net."""
        if b in PANELS:
            return (b, "pad", net)
        if net in (VCC, GND):
            sd = side or "L"
            return self.rail(b, sd + ("+" if net == VCC else "-"), near_row, owner)
        for key in ([] if rows is not None else
                    sorted(self.strips_of(b, net), key=lambda k: abs(k[1] - near_row))):
            if side and key[2] != side:
                continue
            free = [c for c in (COLS_L if key[2] == "L" else COLS_R) if (b, c, key[1]) not in self.used]
            if free:
                return self.free_in_strip(key, owner)
        key = self.landing(b, net, owner, rows=rows, side=side, near_row=near_row,
                           near_hole=near_hole)
        return self.free_in_strip(key, owner)

    def part(self, kind, ref, value, *leads, sim_part=None):
        """leads: (net, board, kwargs-for-attach)"""
        hs = []
        for net, b, kw in leads:
            if hs and b == hs[0][0][0] and b not in PANELS and "rows" not in kw:
                kw = dict(kw, near_hole=hs[0][0])     # land the next lead close to the first
            hs.append((self.attach(net, b, ref, **kw), net))
        self.items.append(dict(kind=kind, ref=ref, value=value, leads=hs))
        if len(hs) == 2 and hs[0][0][0] == hs[1][0][0] and hs[0][0][0] not in PANELS:
            span = math.dist(hole_xy(hs[0][0]), hole_xy(hs[1][0]))
            if span > MAX_LEAD_SPAN_MM:
                self.errors.append(f"{ref}: leads {fmt(hs[0][0])}-{fmt(hs[1][0])} span {span:.0f} mm")
        if sim_part:
            for i, (h, _) in enumerate(hs):
                self.sim_hole[(sim_part, i + 1)] = h
        return hs

    def wire(self, h1, h2, net):
        self.wires.append((h1, h2, net))


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------

def schematic(mutations=()):
    sim, _ = build(bytes(32768), mutations=mutations)
    terms = {}
    kinds = {}
    for p in sim.parts.values():
        kinds[p.name] = p.kind
        for pin, net in p.pins.items():
            terms[(p.name, pin)] = net.name
    return terms, kinds, sim


def generate(grid=None, moves=None, cd_pullups_at_cpu=False, mutations=(),
             pd_rows=range(36, 44)):
    """grid: board arrangement override; moves: {ic: new pin-1 row} placement
    overrides; cd_pullups_at_cpu applies the F9 suggestion; mutations selects a
    schematic variant (e.g. ["rev03"])."""
    ACTIVE_GRID.clear()
    ACTIVE_GRID.update(grid or GRID)
    L = Layout()
    L.variant = dict(grid=grid, moves=moves, cd_pullups_at_cpu=cd_pullups_at_cpu,
                     mutations=tuple(mutations))
    terms, kinds, esim = schematic(mutations)
    L.terminals = terms

    # ---- ICs at Section 3.3 positions --------------------------------------
    rows, probs = checks.placement()
    L.errors += probs
    ic_rows = {}
    for bb, u, c1, r1, npins, c2, r2, lo, hi in rows:
        r1, n = int(r1), int(npins)
        if moves and u in moves:
            r1 = moves[u]
        ic_rows[u] = (bb, c1, c2, r1, n)
        for pin in range(1, n + 1):
            if pin <= n // 2:
                h = (bb, c1, r1 + pin - 1)
            else:
                h = (bb, c2, r1 + (n - pin))
            L.take(h, f"{u}.{pin}")
            L.pin_hole[(u, pin)] = h
            L.sim_hole[(u, pin)] = h
            net = terms.get((u, pin), "")
            key = strip_key(h)
            if key in L.strip_net and L.strip_net[key] != (net if net not in (VCC, GND) else ""):
                L.errors.append(f"strip collision {key}: {L.strip_net[key]} vs {u}.{pin}")
            # supply / tie-off pins own their strip but are joined to a rail by a wire
            L.strip_net[key] = net if net not in (VCC, GND) else ""

    # ---- passives (spec Sections 5, 6, 8, 9) -------------------------------
    for i in range(8):           # C_D pull-ups, BB1 rows 2-11 (or at the CPU pins)
        if cd_pullups_at_cpu:
            h = L.pin_hole[("U1", 33 - i)]
            L.part("R", f"R_CD{i}", "10k", (C_D[i], "BB1", dict(near_row=h[2], side="R")),
                   (VCC, "BB1", dict(side="R", near_row=h[2])))
        else:
            L.part("R", f"R_CD{i}", "10k", (C_D[i], "BB1", dict(rows=[2 + i], side="L")),
                   (VCC, "BB1", dict(side="L", near_row=2 + i)))
    for i in range(8):           # P_D pull-ups, BB10 rows 36-50
        r = list(pd_rows)[i]
        L.part("R", f"R_PD{i}", "4.7k", (P_D[i], "BB10", dict(rows=[r], side="L")),
               (VCC, "BB10", dict(side="L", near_row=r)))
    for net, pin in [("CPU_RDY", 2), ("CPU_IRQB", 4), ("CPU_NMIB", 6), ("CPU_BE", 36), ("CPU_SOB", 38)]:
        h = L.pin_hole[("U1", pin)]
        L.part("R", f"R_{net}", "10k", (net, "BB1", dict(near_row=h[2], side=side_of(h[1]))),
               (VCC, "BB1", dict(side=side_of(h[1]), near_row=h[2])))
    # oscillator and reset RC around U5 (BB1)
    u5 = lambda p: L.pin_hole[("U5", p)]
    L.part("R", "R_OSC", "10k", ("OSC_RC", "BB1", dict(near_row=u5(1)[2], side="L")),
           ("MASTER_CLK", "BB1", dict(near_row=u5(2)[2], side="L")))
    L.part("C", "C_OSC", "330p C0G", ("OSC_RC", "BB1", dict(near_row=u5(1)[2], side="L")),
           (GND, "BB1", dict(side="L", near_row=u5(1)[2])))
    L.part("R", "R_RST", "100k", ("COLD_CLEAR_N", "BB1", dict(near_row=u5(6)[2], side="L")),
           ("RESET_RC", "BB1", dict(near_row=u5(9)[2], side="R")))
    L.part("C", "C_RST", "1uF", ("RESET_RC", "BB1", dict(near_row=u5(9)[2], side="R")),
           (GND, "BB1", dict(side="R", near_row=u5(9)[2])))
    L.part("R", "R_RSTD", "1k", ("RESET_RC", "BB1", dict(near_row=u5(9)[2], side="R")),
           ("RST_DIODE", "BB1", dict(near_row=u5(9)[2], side="R")))
    # the diode bridges back over U5 to the COLD_CLEAR_N strip (pin 6)
    L.part("D", "D_RST", "1N4148 (anode, cathode)", ("RST_DIODE", "BB1", dict(near_row=u5(9)[2], side="R")),
           ("COLD_CLEAR_N", "BB1", dict(near_row=u5(6)[2], side="L")))
    # switch panel: reset button, keys, DIP switch
    L.part("R", "R_RSTBTN", "1k", ("RESET_RC", "SW", {}), ("RESET_BTN_N", "SW", {}))
    L.part("S", "SW_RESET", "pushbutton", ("RESET_BTN_N", "SW", {}), (GND, "SW", {}))
    for k in KEYS:
        L.part("R", f"R_KEY_{k}", "10k", (f"KEYIN_{k}", "SW", {}), (VCC, "SW", {}))
        L.part("C", f"C_KEY_{k}", "100n", (f"KEYIN_{k}", "SW", {}), (GND, "SW", {}))
        L.part("R", f"R_KEYD_{k}", "1k", (f"KEYIN_{k}", "SW", {}), (f"KEYSW_{k}", "SW", {}))
        L.part("S", f"SW_{k}", "pushbutton", (f"KEYSW_{k}", "SW", {}), (GND, "SW", {}))
    for i in range(8):
        L.part("S", f"DIP{i}", "DIP switch pole", (SW[i], "SW", {}), (GND, "SW", {}))
        h = L.pin_hole[("U14", 2 + i)]
        L.part("R", f"R_SW{i}", "10k", (SW[i], "BB4", dict(near_row=h[2], side="L")),
               (VCC, "BB4", dict(side="L", near_row=h[2])))
    # 220R fanout branches (BB3) and DATA branches (BB4)
    for name in sorted(k for k, v in kinds.items() if v == "RSER"):
        if name.startswith("R_U"):
            continue
        n1, n2 = terms[(name, 1)], terms[(name, 2)]
        b = "BB4" if name.startswith("R_DATA") else "BB3"
        src = [k for k in L.strip_net if L.strip_net[k] == n1 and k[0] == b]
        nr, sd = (src[0][1], src[0][2]) if src else (32, None)
        L.part("R", name, "220", (n1, b, dict(near_row=nr, side=sd)), (n2, b, dict(near_row=nr, side=sd)),
               sim_part=name)
    # segment resistors and digits on the soldered LED panel
    for name in sorted(k for k, v in kinds.items() if v == "RSER" and k.startswith("R_U")):
        L.part("R", name, "3.3k", (terms[(name, 1)], "LED", {}), (terms[(name, 2)], "LED", {}),
               sim_part=name)
    for name in sorted(k for k, v in kinds.items() if v == "SC56-11"):
        pins = [(terms[(name, p)], "LED", {}) for p in range(1, 11)]
        L.part("DIGIT", name, "SC56-11EWA", *pins, sim_part=name)
    # status LEDs (BB9)
    for q in ["MESSAGE_LOCK", "RESULT_LOCK", "FAULT"]:
        src = L.strips_of("BB9", q)
        nr, sd = (src[0][1], src[0][2]) if src else (32, "L")
        hs = L.part("R", f"R_LED_{q}", "4.7k", (q, "BB9", dict(near_row=nr, side=sd)),
                    (f"LEDA_{q}", "BB9", dict(near_row=nr, side=sd)))
        L.sim_hole[(f"LED_{q}", 1)] = hs[0][0]
        ra = hs[1][0][2]
        L.part("LED", f"LED_{q}", "3 mm", (f"LEDA_{q}", "BB9", dict(near_row=ra, side=sd)),
               (GND, "BB9", dict(side=sd, near_row=ra)))
    # power fixture
    L.part("U", "U42", "MCP130-460 (RST,VDD,VSS)", ("POR_RAW_N", "PWR", {}), (VCC, "PWR", {}), (GND, "PWR", {}))
    L.part("R", "R_POR", "4.7k", ("POR_RAW_N", "PWR", {}), (VCC, "PWR", {}))
    L.part("C", "C_U42", "100n", (VCC, "PWR", {}), (GND, "PWR", {}))
    L.part("U", "U43", "L7805ACV (IN,GND,OUT)", ("VIN", "PWR", {}), (GND, "PWR", {}), (VCC, "PWR", {}))
    L.part("C", "C_IN1", "330n", ("VIN", "PWR", {}), (GND, "PWR", {}))
    L.part("C", "C_IN2", "100uF", ("VIN", "PWR", {}), (GND, "PWR", {}))
    L.part("C", "C_OUT1", "100n", (VCC, "PWR", {}), (GND, "PWR", {}))
    L.part("C", "C_OUT2", "47uF", (VCC, "PWR", {}), (GND, "PWR", {}))
    L.part("R", "R_BLEED", "1k 1/4W", (VCC, "PWR", {}), (GND, "PWR", {}))
    L.part("D", "D_REV", "1N5819 (anode, cathode)", ("VBAT_SW", "PWR", {}), ("VIN", "PWR", {}))
    # IC supply pins, tie-offs and bypass capacitors
    for (u, pin), h in sorted(L.pin_hole.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        net = terms.get((u, pin))
        if net in (VCC, GND):
            sd = side_of(h[1])
            hs = L.free_in_strip(strip_key(h), f"{u}.{pin}->rail")
            hr = L.rail(h[0], sd + ("+" if net == VCC else "-"), h[2], f"{u}.{pin}")
            L.wire(hs, hr, net)
    for u, (bb, c1, c2, r1, n) in sorted(ic_rows.items()):
        vcc_pins = [p for p in range(1, n + 1) if terms.get((u, p)) == VCC
                    and L.terminals.get((u, p)) == VCC]
        vpin = {14: 14, 16: 16, 20: 20, 28: 28, 40: 8}[n]
        h = L.pin_hole[(u, vpin)]
        hc = L.free_in_strip(strip_key(h), f"C_BYP_{u}")
        hg = L.rail(bb, side_of(h[1]) + "-", h[2], f"C_BYP_{u}")
        L.items.append(dict(kind="C", ref=f"C_BYP_{u}", value="100n", leads=[(hc, VCC), (hg, GND)]))
    for b in BOARDS:
        hp = L.rail(b, "R+", 1, f"C_BULK_{b}")
        hn = L.rail(b, "R-", 1, f"C_BULK_{b}")
        L.items.append(dict(kind="C", ref=f"C_BULK_{b}", value="10uF", leads=[(hp, VCC), (hn, GND)]))

    # behavioural stand-ins of the simulator sit on the physical node they model
    L.sim_hole[("OSC", 1)] = L.pin_hole[("U5", 2)]
    L.sim_hole[("OSC_RC_NODE", 1)] = L.pin_hole[("U5", 1)]
    L.sim_hole[("RESET_RC_MODEL", 1)] = L.pin_hole[("U5", 6)]
    L.sim_hole[("RESET_RC_MODEL", 2)] = ("SW", "pad", "RESET_BTN_N")
    L.sim_hole[("RESET_RC_MODEL", 3)] = L.pin_hole[("U5", 9)]
    L.sim_hole[("U42", 1)] = ("PWR", "pad", "POR_RAW_N")
    L.sim_hole[("RESET_BTN", 1)] = ("SW", "pad", "RESET_BTN_N")
    for i in range(8):
        L.sim_hole[(f"DIPSW{i}", 1)] = ("SW", "pad", SW[i])
    for k in KEYS:
        L.sim_hole[(f"KEYSW_{k}", 1)] = ("SW", "pad", f"KEYIN_{k}")

    # ---- signal routing: nearest-neighbour daisy chain per net -------------
    anchors = defaultdict(list)
    for key, net in L.strip_net.items():
        if net:
            anchors[net].append(key)
    for it in L.items:
        for h, net in it["leads"]:
            if h[1] == "pad" and net not in (VCC, GND):
                anchors[net].append(strip_key(h))
    for net in sorted(anchors):
        nodes = sorted(set(anchors[net]), key=lambda k: (BOARDS.index(k[0]) if k[0] in BOARDS else 99, str(k[1])))
        if len(nodes) < 2:
            continue
        best = None
        for start in nodes:
            rest = [n for n in nodes if n != start]
            chain, total = [start], 0.0
            while rest:
                nxt = min(rest, key=lambda k: _dist(chain[-1], k))
                total += _dist(chain[-1], nxt)
                rest.remove(nxt)
                chain.append(nxt)
            if best is None or total < best[0]:
                best = (total, chain)
        chain = best[1]
        for a, b in zip(chain, chain[1:]):
            L.wire(_hole_for(L, a, net), _hole_for(L, b, net), net)

    # ---- power distribution: star from the power fixture ------------------
    for b in BOARDS:
        for net, rl, rr in [(VCC, "L+", "R+"), (GND, "L-", "R-")]:
            top = L.rail(b, rl, 1, f"PWR->{b}")
            L.wire(("PWR", "pad", net), top, net)
            for rail in (rl, rr):
                L.wire(L.rail(b, rail, SPLIT_ROW, "bridge"), L.rail(b, rail, SPLIT_ROW + 1, "bridge"), net)
            L.wire(L.rail(b, rl, 2, "LR"), L.rail(b, rr, 2, "LR"), net)
    for panel in ("SW", "LED"):
        L.wire(("PWR", "pad", GND), (panel, "pad", GND), GND)
    L.wire(("PWR", "pad", VCC), ("SW", "pad", VCC), VCC)
    return L


def _dist(a, b):
    ha = (a[0], "pad", a[2]) if a[1] == "pad" else ((a[0], "a" if a[2] == "L" else "j", a[1]))
    hb = (b[0], "pad", b[2]) if b[1] == "pad" else ((b[0], "a" if b[2] == "L" else "j", b[1]))
    return wire_mm(ha, hb)


def _hole_for(L, key, net):
    if key[1] == "pad":
        return (key[0], "pad", key[2])
    return L.free_in_strip(key, f"wire {net}")


# ---------------------------------------------------------------------------
# physical connectivity -> netlist
# ---------------------------------------------------------------------------

class UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def connectivity(L, faults=()):
    """Physical nets from strips, rails and wires.  `faults` apply build
    mistakes after layout: ("wire_row", net, index, drow), ("ic_shift", ic,
    drow), ("drop_wire", net, predicate-text)."""
    pin_hole = dict(L.pin_hole)
    sim_hole = dict(L.sim_hole)
    wires = list(L.wires)
    for f in faults:
        if f[0] == "ic_shift":
            _, ic, dr = f
            for (u, pin), h in list(pin_hole.items()):
                if u == ic:
                    pin_hole[(u, pin)] = (h[0], h[1], h[2] + dr)
                    sim_hole[(u, pin)] = pin_hole[(u, pin)]
        elif f[0] == "wire_row":
            _, net, idx, dr = f
            ws = [i for i, w in enumerate(wires) if w[2] == net and w[1][1] in COLS_L + COLS_R]
            i = ws[idx]
            h1, h2, n = wires[i]
            wires[i] = (h1, (h2[0], h2[1], h2[2] + dr), n)
        elif f[0] == "drop_wire":
            _, board, rail = f
            wires = [w for w in wires if not (w[0][0] == board and w[0][1] == rail
                                              and w[1][1] == rail and w[0][2] != w[1][2])]
    uf = UF()
    declared = defaultdict(set)
    for (u, pin), h in pin_hole.items():
        k = strip_key(h)
        uf.find(k)
        net = L.terminals.get((u, pin))
        if net and net not in (VCC, GND):
            declared[k].add(net)
    for h1, h2, net in wires:
        uf.union(strip_key(h1), strip_key(h2))
        declared[strip_key(h1)].add(net)
        declared[strip_key(h2)].add(net)
    for it in L.items:
        for h, net in it["leads"]:
            declared[strip_key(h)].add(net)
    groups = defaultdict(set)
    for k, nets in declared.items():
        groups[uf.find(k)] |= nets
    for k in list(uf.p):
        groups.setdefault(uf.find(k), set())
    # name physical nodes
    names, shorts, owners = {}, [], defaultdict(list)
    for root, nets in groups.items():
        if len(nets) > 1:
            shorts.append(sorted(nets))
            names[root] = "SHORT[" + "+".join(sorted(nets)) + "]"
        elif nets:
            owners[next(iter(nets))].append(root)
            names[root] = next(iter(nets))
        else:
            names[root] = f"FLOAT{len(names)}"
    opens = []
    for net, roots in owners.items():
        if len(roots) > 1:
            roots.sort(key=lambda r: -len([k for k in uf.p if uf.find(k) == r]))
            opens.append((net, len(roots)))
            for i, r in enumerate(roots[1:], 1):
                names[r] = f"{net}~{i}"
    node = lambda h: names[uf.find(strip_key(h))]
    return dict(node=node, shorts=shorts, opens=opens, sim_hole=sim_hole, wires=wires,
                uf=uf, names=names)


def physical_netlist(L, faults=()):
    """(pin_remap, pullups, report) for board.build(physical=...)."""
    con = connectivity(L, faults)
    node = con["node"]
    remap = {k: node(h) for k, h in con["sim_hole"].items()}
    # capacitance per physical node: IC pins, strips touched, wire length
    cap = defaultdict(float)
    for (u, pin), h in L.pin_hole.items():
        cap[node(h)] += PIN_PF
    strips = defaultdict(set)
    for h1, h2, net in con["wires"]:
        n = node(h1)
        cap[n] += wire_mm(h1, h2) * WIRE_PF_PER_MM
        strips[n] |= {strip_key(h1), strip_key(h2)}
    for n, s in strips.items():
        cap[n] += STRIP_PF * len(s)
    pullups = []
    for it in L.items:
        if it["kind"] != "R" or len(it["leads"]) != 2:
            continue
        (h1, _), (h2, _) = it["leads"]
        a, b = node(h1), node(h2)
        ohms = _ohms(it["value"])
        if b == VCC and a not in (VCC, GND):
            pullups.append((a, ohms, max(cap[a], 10.0)))
        elif a == VCC and b not in (VCC, GND):
            pullups.append((b, ohms, max(cap[b], 10.0)))
    # behavioural-only pull-up of the reset-button node model
    pullups.append((remap.get(("RESET_BTN", 1), "RESET_BTN_N"), 100e3, 1.0))
    return remap, pullups, con, cap


def _ohms(v):
    v = v.split()[0].lower()
    mult = 1e3 if v.endswith("k") else 1.0
    return float(v.rstrip("k")) * mult


# ---------------------------------------------------------------------------
# checks against spec Sections 3.2 / 3.4
# ---------------------------------------------------------------------------

def wire_report(L):
    by_net = defaultdict(float)
    boards = defaultdict(set)
    for h1, h2, net in L.wires:
        by_net[net] += wire_mm(h1, h2)
        boards[net] |= {h1[0], h2[0]}
    phi2_seg = None
    u7, u1 = L.pin_hole[("U7", 2)], L.pin_hole[("U1", 37)]
    for h1, h2, net in L.wires:
        if net == "PHI2" and {strip_key(h1), strip_key(h2)} == {strip_key(u7), strip_key(u1)}:
            phi2_seg = wire_mm(h1, h2)
    return by_net, boards, phi2_seg


LOCK_INTERFACE = {"A13", "A14", "RAM_SELECT", "nRWB", "PHI2_FAULT", "COLD_CLEAR_N", "CPU_RESET_N",
                  "Y3_N", "Y4_N", "WR_PHASE_N", "WRITE_BLOCK", "MESSAGE_LOCK",
                  "RESULT_LOCK", "FAULT", "FAULT_N"}


def harness_checks(L):
    by_net, boards, phi2_seg = wire_report(L)
    problems, info = [], {}
    for n in C_D:
        if not boards[n] <= {"BB1", "BB10"}:
            problems.append(f"{n} leaves BB1/BB10: {sorted(boards[n])}")
    for n in P_D:
        if not boards[n] <= {"BB10", "BB2", "BB4"}:
            problems.append(f"{n} reaches {sorted(boards[n])}")
    crossing = defaultdict(set)          # nets with a wire leaving each board
    for h1, h2, net in L.wires:
        if h1[0] != h2[0]:
            crossing[h1[0]].add(net)
            crossing[h2[0]].add(net)
    bb9 = crossing["BB9"] - {VCC, GND}
    if bb9 != LOCK_INTERFACE:
        problems.append(f"BB9 harness {sorted(bb9 ^ LOCK_INTERFACE)} differs from Section 3.4")
    for b in range(4):
        bb = f"BB{5 + b}"
        nets = crossing[bb] - {VCC, GND}
        allowed = {f"B{b}_SER", f"B{b}_SRCLK", f"B{b}_RCLK", f"B{b}_OE_N", f"B{b}_SRCLR_N"}
        outside = {n for n in nets if n not in allowed and not n.startswith("U")}
        if outside:
            problems.append(f"{bb} carries {sorted(outside)}")
    led_gnd = [w for w in L.wires if w[2] == GND and "LED" in (w[0][0], w[1][0])]
    if not all({w[0][0], w[1][0]} == {"PWR", "LED"} for w in led_gnd):
        problems.append("display return current shares a logic-board ground jumper")
    info["PHI2 U7->U1 segment mm"] = phi2_seg
    info["C_D max line mm"] = max(by_net[n] for n in C_D)
    info["P_D max line mm"] = max(by_net[n] for n in P_D)
    info["PHI2 total mm"] = by_net["PHI2"]
    info["PHI2 boards"] = sorted(boards["PHI2"])
    # rev 0.4 limits (Sections 3.2, 6.5, 11.2): PHI2 segment length and the HCT
    # input-transition limit expressed as capacitance per pulled-up bus line
    cap = physical_netlist(L)[3]
    info["P_D max pF"] = max(cap[n] for n in P_D)
    info["C_D max pF"] = max(cap[n] for n in C_D)
    targets = []
    if phi2_seg and phi2_seg > 50:
        targets.append(f"PHI2 U7->U1 {phi2_seg:.0f} mm > 50 mm")
    if info["P_D max pF"] > 320:
        targets.append(f"P_D {info['P_D max pF']:.0f} pF > 320 pF")
    if info["C_D max pF"] > 150:
        targets.append(f"C_D {info['C_D max pF']:.0f} pF > 150 pF")
    return problems, targets, info, by_net


def summary(L):
    s = dict(ics=len({u for u, _ in L.pin_hole}), wires=len(L.wires),
             parts=len(L.items), holes=len(L.used))
    inter = defaultdict(int)
    for h1, h2, net in L.wires:
        if h1[0] != h2[0]:
            inter[tuple(sorted((h1[0], h2[0])))] += 1
    s["inter_board_wires"] = sum(inter.values())
    s["by_pair"] = dict(sorted(inter.items()))
    return s


# ---------------------------------------------------------------------------
# build documents
# ---------------------------------------------------------------------------

def write_docs(L, outdir):
    import csv
    import os
    os.makedirs(outdir, exist_ok=True)
    rows = []
    for it in L.items:
        hs = " ".join(f"{fmt(h)}({n})" for h, n in it["leads"])
        rows.append(("part", it["ref"], it["value"], "", hs, "", ""))
    for h1, h2, net in L.wires:
        rows.append(("wire", "", "", net, fmt(h1), fmt(h2), f"{wire_mm(h1, h2):.0f}"))
    with open(os.path.join(outdir, "wiring.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["kind", "ref", "value", "net", "from", "to", "length_mm"])
        w.writerows(rows)

    problems, targets, info, by_net = harness_checks(L)
    remap, pull, con, cap = physical_netlist(L)
    s = summary(L)
    out = []
    a = out.append
    a("# DTS-02 breadboard wiring list (generated)\n")
    a("Generated by `python3 -m sim wiring` from `spec.md` (Section 3.3 placement, pin tables). "
      "Do not edit by hand: change the generator or the spec and regenerate.\n")
    a("## Conventions\n")
    a("- `BB1:c41` = board BB1, column c, row 1-63.  Columns a-e and f-j are separate 5-hole strips.")
    a("- `BB1:L+@12` = left + rail next to row 12 (`L-`, `L+`, `R+`, `R-`).  Rails are assumed split "
      "between rows 31 and 32; the listed bridge wires join the halves.")
    a("- `SW:`, `LED:`, `PWR:` = soldered pads on the switch panel, LED panel and power fixture; "
      "one pad per net.  `PWR:VCC` / `PWR:GND` are the regulator output and return.")
    a("- Red wire for VCC, black for GND.  Lengths are straight-line estimates with 15 % slack + 10 mm.\n")
    a("## Verification of this list\n")
    a(f"- {s['ics']} DIP ICs, {s['parts']} other parts, {s['wires']} wires "
      f"({s['inter_board_wires']} between boards), {s['holes']} breadboard holes used.")
    a(f"- Hole allocation errors (double use, full strip, full rail half): {len(L.errors)}.")
    a(f"- Physical connectivity vs schematic: {len(con['shorts'])} shorts, {len(con['opens'])} opens.")
    a(f"- Section 3.4 harness rules: {'pass' if not problems else problems}.")
    a(f"- Section 3.2 / 6.5 limits (PHI2 segment ≤50 mm, P_D ≤320 pF, C_D ≤150 pF): "
      f"{'met' if not targets else '; '.join(targets)}.")
    a(f"- Estimated line capacitance: P_D max {max(cap[n] for n in P_D):.0f} pF, "
      f"C_D max {max(cap[n] for n in C_D):.0f} pF.")
    a("- The simulator runs the test firmware on the netlist derived from this list "
      "(`tests/test_breadboard.py`).\n")
    a("## Inter-board harnesses\n")
    a("| Boards | Wires | Nets |")
    a("|---|---:|---|")
    pairs = defaultdict(list)
    for h1, h2, net in L.wires:
        if h1[0] != h2[0]:
            pairs[tuple(sorted((h1[0], h2[0])))].append(net)
    for (x, y), nets in sorted(pairs.items()):
        a(f"| {x}–{y} | {len(nets)} | {', '.join(sorted(set(nets)))} |")
    a("")
    for b in BOARDS + PANELS:
        a(f"## {b}\n")
        ics = sorted({u for (u, _), h in L.pin_hole.items() if h[0] == b},
                     key=lambda u: int(u[1:]))
        if ics:
            a("ICs (notch toward row 1): " + ", ".join(
                f"{u} pin 1 at {fmt(L.pin_hole[(u, 1)])}" for u in ics) + "\n")
        parts = [it for it in L.items if any(h[0] == b for h, _ in it["leads"])]
        if parts:
            a("| Part | Value | Leads |")
            a("|---|---|---|")
            for it in parts:
                a(f"| {it['ref']} | {it['value']} | " +
                  ", ".join(f"{fmt(h)} ({n})" for h, n in it["leads"]) + " |")
            a("")
        ws = [w for w in L.wires if w[0][0] == b or w[1][0] == b]
        ws.sort(key=lambda w: (w[2], fmt(w[0])))
        if ws:
            a("| Net | From | To | mm |")
            a("|---|---|---|---:|")
            for h1, h2, net in ws:
                if h1[0] != b:
                    h1, h2 = h2, h1
                a(f"| {net} | {fmt(h1)} | {fmt(h2)} | {wire_mm(h1, h2):.0f} |")
            a("")
    with open(os.path.join(outdir, "WIRING.md"), "w") as f:
        f.write("\n".join(out) + "\n")
    return os.path.join(outdir, "WIRING.md")
