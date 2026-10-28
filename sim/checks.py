"""Static checks (ERC, placement, BOM) and waveform measurements for the
Section 11.2 bus-handoff acceptance items."""

import re

from .specdoc import spec_text

# ---------------------------------------------------------------------------
# electrical rule check on the transcribed netlist
# ---------------------------------------------------------------------------

BEHAVIOURAL = {"SRC", "OSC", "RESETRC"}


def erc(sim):
    errors, notes = [], []
    srcs = {}       # net -> list of (part, pin, role)
    loads = {}
    for p in sim.parts.values():
        roles = p.roles
        for pin, role in roles.items():
            net = p.pins.get(pin)
            if role == "in" and net is None:
                errors.append(f"{p.name} pin {pin}: input left unconnected")
            if net is None:
                continue
            if role == "vcc" and net.name != "VCC":
                errors.append(f"{p.name} pin {pin}: supply pin on {net.name}")
            if role == "gnd" and net.name != "GND":
                errors.append(f"{p.name} pin {pin}: ground pin on {net.name}")
            if role in ("out", "tri"):
                if net.name in ("VCC", "GND"):
                    errors.append(f"{p.name} pin {pin}: output tied to {net.name}")
                srcs.setdefault(net.name, []).append((p, pin, role))
            if role == "in":
                loads.setdefault(net.name, []).append((p, pin))
    for name, lst in srcs.items():
        totem = [x for x in lst if x[2] == "out"]
        tri = [x for x in lst if x[2] == "tri"]
        if len(totem) > 1:
            errors.append(f"net {name}: {len(totem)} push-pull outputs "
                          + ", ".join(f"{p.name}.{pin}" for p, pin, _ in totem))
        if totem and tri:
            errors.append(f"net {name}: push-pull output shares net with three-state drivers")
    for name, lst in loads.items():
        net = sim.nets[name]
        if name in ("VCC", "GND"):
            continue
        if name not in srcs and net.pullup_ohms is None:
            errors.append(f"net {name}: inputs {[f'{p.name}.{pin}' for p, pin in lst]} have no source")
        if name in srcs and all(r == "tri" for _, _, r in srcs[name]) and net.pullup_ohms is None:
            # LED loads, series resistors and the keypad's diode-isolated rows float by design
            logic = [f"{p.name}.{pin}" for p, pin in lst if p.kind not in ("SC56-11", "LOAD", "RSER", "KEYPAD")]
            # series resistors pass a driven level through; check what feeds them
            feeders = [p for p, _, _ in srcs[name] if p.kind != "RSER"]
            if logic and feeders:
                notes.append(f"net {name}: only three-state drivers, no pull-up; loads {logic}")
    return errors, notes


# ---------------------------------------------------------------------------
# placement table (Section 3.3)
# ---------------------------------------------------------------------------

DIP_PINS = {}


def placement():
    """Parse Section 3.3 and check each row range against the pin count and
    for overlaps within a board."""
    text = spec_text()
    sec = text[text.index("### 3.3 Exact DIP placement"):text.index("### 3.4")]
    rows = re.findall(r"\| (BB\d+) \| (U\d+) \| ([a-j])(\d+) \| Pin (\d+) at ([a-j])(\d+) \| (\d+)–(\d+) \|", sec)
    problems = []
    by_board = {}
    for bb, u, c1, r1, npins, c2, r2, lo, hi in rows:
        r1, npins, r2, lo, hi = int(r1), int(npins), int(r2), int(lo), int(hi)
        if r1 != r2 or r1 != lo:
            problems.append(f"{u}: pin 1 row {r1} / pin {npins} row {r2} / range start {lo} disagree")
        if hi - lo + 1 != npins // 2:
            problems.append(f"{u}: rows {lo}-{hi} hold {hi - lo + 1} pins per side, need {npins // 2}")
        if hi > 63:
            problems.append(f"{u}: row {hi} beyond 63-row board")
        wide = npins in (24, 28, 40)
        if wide and (c1, c2) != ("c", "g"):
            problems.append(f"{u}: 0.6-inch DIP not in c/g")
        if not wide and (c1, c2) != ("e", "f"):
            problems.append(f"{u}: 0.3-inch DIP not in e/f")
        for other_u, olo, ohi in by_board.get(bb, []):
            if not (hi < olo or lo > ohi):
                problems.append(f"{bb}: {u} rows {lo}-{hi} overlap {other_u} rows {olo}-{ohi}")
        by_board.setdefault(bb, []).append((u, lo, hi))
    return rows, problems


# ---------------------------------------------------------------------------
# bus monitor (Section 11.2 items)
# ---------------------------------------------------------------------------

def driver_intervals(log, t0, t_end):
    """Convert a driver log into [(start, end, owners)] intervals with owners."""
    out = []
    cur_t, cur = None, ()
    for t, owners in log:
        if t < t0:
            cur = owners
            cur_t = t0
            continue
        if cur_t is None:
            cur_t = t0
        if owners != cur:
            if cur:
                out.append((cur_t, t, cur))
            cur_t, cur = t, owners
    if cur:
        out.append((cur_t, t_end, cur))
    return out


def handoff_stats(intervals):
    """Max simultaneous drivers, and the minimum idle gap before a driver
    turns on (any owner) and before a *different* owner turns on."""
    max_drv = max((len(o) for _, _, o in intervals), default=0)
    min_gap = min_gap_change = float("inf")
    prev = None
    for s, e, o in intervals:
        if prev is not None:
            gap = s - prev[1]
            min_gap = min(min_gap, gap)
            if o != prev[2]:
                min_gap_change = min(min_gap_change, gap)
        prev = (s, e, o)
    return dict(max_drivers=max_drv, min_gap_ns=min_gap, min_gap_owner_change_ns=min_gap_change,
                intervals=len(intervals))


def high_intervals(edge_log, t0, t_end):
    out, start, val = [], None, None
    for t, old, new in edge_log:
        if new == 1 and val != 1:
            start = t
        elif new != 1 and val == 1 and start is not None:
            if t >= t0:
                out.append((max(start, t0), t))
            start = None
        val = new
    if val == 1 and start is not None:
        out.append((max(start, t0), t_end))
    return out


def low_intervals(edge_log, t0, t_end):
    inv = [(t, None, 1 if n == 0 else 0) for t, _, n in edge_log]
    return high_intervals(inv, t0, t_end)


def contained(inner, outer, tol):
    """Every inner interval lies within some outer interval (± tol)."""
    bad = []
    j = 0
    for s, e in inner:
        ok = any(os - tol <= s and e <= oe + tol for os, oe in outer)
        if not ok:
            bad.append((s, e))
    return bad


# ---------------------------------------------------------------------------
# BOM quantities (Section 2) against the generated physical layout
# ---------------------------------------------------------------------------

def bom(L):
    """Return [(item, spec_qty, layout_qty)] for the countable BOM lines.
    Counts come from sim/layout.py, which places every physical part."""
    text = spec_text()

    def qty(pattern):
        m = re.search(r"^\| (\d+) \| " + pattern, text, re.M)
        return int(m.group(1)) if m else None

    items = L.items
    ics = {u for (u, _) in L.pin_hole}

    def count(kind=None, value=None, prefix=None):
        n = 0
        for it in items:
            if kind and it["kind"] != kind:
                continue
            v = it["value"].split()[0]
            if value is not None and v != value:
                continue
            if prefix and not it["ref"].startswith(prefix):
                continue
            n += 1
        return n
    m = re.search(r"Installed total: (\d+) ICs.*?; (\d+) are DIP", text)
    return [
        ("DIP ICs", int(m.group(2)), len(ics)),
        ("all ICs (DIP + U42 + U43)", int(m.group(1)), len(ics) + count(kind="U")),
        ("SC56-11EWA digits", qty(r"Kingbright"), count(kind="DIGIT")),
        ("47k RSET", qty(r"47 kΩ"), count("R", "47k")),
        ("150R", qty(r"150 Ω"), count("R", "150")),
        ("4.7k resistors", qty(r"4\.7 kΩ"), count("R", "4.7k")),
        ("10k resistors", qty(r"10 kΩ"), count("R", "10k")),
        ("1k resistors (1/8 W)", qty(r"1 kΩ, ≥1/8 W"), count("R", "1k") - count("R", "1k", "R_BLEED")),
        ("100nF capacitors", qty(r"100 nF"), count("C", "100n")),
        ("10uF capacitors", qty(r"10 µF"), count("C", "10uF")),
        ("Schottky row diodes", qty(r"BAT85"), count("D", prefix="DK")),
        ("status LEDs", qty(r"3 mm LEDs"), count(kind="LED")),
        ("PNP switch", qty(r"BC327"), count("Q", prefix="Q1")),
        ("NPN driver", qty(r"2N3904"), count("Q", prefix="Q2")),
        ("keypad", qty(r"4×4 membrane"), count(kind="KEYPAD")),
        ("breadboards", qty(r"Good-quality 830-tie"), len(set(b for (b, _, _) in L.pin_hole.values()))),
    ]
