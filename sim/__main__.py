"""Command line: python3 -m sim <command> [options]

  check                 static + analog checks (fast)
  run SCENARIO          boot the test ROM in a scenario and report the board state
  diag                  run the spec's Section 11.3 diagnostic ROM
  handoff               Section 11.2 bus-handoff measurements
  race                  worst-case read-window corner, rev 0.3 vs six-state fix
"""

import argparse
import collections
import sys

from . import analog, checks, corners, specdoc
from .board import P_D
from .firmware import SCENARIOS


def _bench_kw(a):
    kw = dict(seed=a.seed, master_hz=a.freq * 1e3)
    if a.six_state:
        kw["mutations"] = ["six_state"]
    if a.vcd:
        from .vcd import VCD
        kw["vcd"] = VCD(a.vcd)
    return kw


def _summary(b):
    c = collections.Counter(v.kind for v in b.violations())
    print(f"  cycles simulated : {len(b.cpu.cycles)}  ({b.sim.now/1e6:.2f} ms)")
    print(f"  FAULT/RESULT/MSG : {b.net('FAULT')}/{b.net('RESULT_LOCK')}/{b.net('MESSAGE_LOCK')}")
    print(f"  display          : [{b.display_text()}]")
    print(f"  A X Y S PC       : {b.cpu.core.a:02X} {b.cpu.core.x:02X} {b.cpu.core.y:02X} "
          f"{b.cpu.core.s:02X} {b.cpu.core.pc:04X}")
    print(f"  violations       : {sum(c.values())} {dict(c) if c else ''}")
    for v in b.violations()[:10]:
        print("   ", v)


def cmd_check(a):
    ok = True
    rom = specdoc.diagnostic_rom()
    good = specdoc.sha256(rom) == specdoc.expected_diagnostic_hash()
    ok &= good
    print(f"diagnostic ROM SHA-256 matches spec: {good}")
    from . import ed25519_ref as E
    import re
    t = specdoc.spec_text()
    sec = t[t.index("### 10.6"):t.index("## 11.")]
    v = {k: bytes.fromhex(h) for k, h in re.findall(r"^(seed|A|R|S)\s*=\s*([0-9a-f]+)", sec, re.M)}
    good = E.public_key(v["seed"]) == v["A"] and E.sign(v["seed"], b"") == v["R"] + v["S"]
    ok &= good
    print(f"RFC 8032 vector reproduces (independent implementation): {good}")
    rows, probs = checks.placement()
    ok &= not probs
    print(f"placement table: {len(rows)} ICs, problems: {probs or 'none'}")
    from .runner import Bench
    b = Bench(scenario="idle")
    errs, notes = checks.erc(b.sim)
    ok &= not errs
    print(f"ERC: {len(errs)} errors, {len(notes)} notes")
    for e in errs + notes:
        print("   ", e)
    bad = [(i, s, n) for i, s, n in checks.bom(b.sim) if s != n]
    ok &= not bad
    print(f"BOM vs netlist: {'all match' if not bad else bad}")
    print("\nanalog:")
    print("  " + analog.report().replace("\n", "\n  "))
    return 0 if ok else 1


def cmd_run(a):
    from .runner import Bench
    b = Bench(scenario=a.scenario, **_bench_kw(a))
    label = "done"
    b.run_to(label)
    print(f"scenario {a.scenario}: reached '{label}'")
    _summary(b)
    if a.reset:
        b.press_reset()
        st = (b.net("FAULT"), b.net("RESULT_LOCK"), b.net("MESSAGE_LOCK"))
        target = "hold" if st[0] == 1 else ("idle" if st != (0, 0, 0) else "done")
        b.run_to(target, max_cycles=60_000)
        b.run_cycles(20)
        print(f"after CPU RESET: reached '{target}'")
        _summary(b)
    if a.vcd:
        b.sim.vcd.close()
        print(f"waveform written to {a.vcd}")
    return 1 if b.violations() else 0


def cmd_diag(a):
    from .runner import Bench
    b = Bench(image=specdoc.diagnostic_rom(), symbols={"loop": 0x800A}, **_bench_kw(a))
    b.h.set_switch_byte(a.switch)
    b.run_to("loop")
    b.run_cycles(a.cycles)
    print(f"diagnostic ROM, raw switch byte ${a.switch:02X}: RAM[$0200]=${b.ram.mem[0x200]:02X}")
    _summary(b)
    if a.vcd:
        b.sim.vcd.close()
        print(f"waveform written to {a.vcd}")
    return 0 if b.ram.mem[0x200] == a.switch and not b.violations() else 1


def cmd_handoff(a):
    from .handoff import measure
    kw = _bench_kw(a)
    r = measure(kw, scenario="ram")
    for k, v in r.items():
        if k != "violations":
            print(f"  {k:38s} {v}")
    print(f"  violations                             {len(r['violations'])}")
    return 0


def cmd_race(a):
    from .runner import Bench
    for label, mut, corner in [("rev 0.3", [], corners.read_window_trailing_edge),
                               ("six-state fix", ["six_state"], corners.read_window_six_state_fix)]:
        b = Bench(image=specdoc.diagnostic_rom(), symbols={"loop": 0x800A}, seed=a.seed,
                  mutations=mut)
        corner(b)
        b.sim.driver_watch.update(P_D)
        b.run_cycles(150)
        ov = [e - s for n in P_D for s, e, o in
              checks.driver_intervals(b.sim.driver_log.get(n, []), b.sim.check_from, b.sim.now)
              if len(o) > 1]
        print(f"{label:14s}: {len(ov)} P_D overlap intervals, longest {max(ov, default=0):.1f} ns")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="python3 -m sim", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("check")
    for name in ("run", "diag", "handoff", "race"):
        s = sub.add_parser(name)
        if name == "run":
            s.add_argument("scenario", choices=sorted(SCENARIOS))
            s.add_argument("--reset", action="store_true", help="press CPU RESET afterwards")
        if name == "diag":
            s.add_argument("--switch", type=lambda x: int(x, 0), default=0xA5)
            s.add_argument("--cycles", type=int, default=200)
        s.add_argument("--freq", type=float, default=380, help="master clock, kHz")
        s.add_argument("--seed", type=int, default=1, help="delay Monte Carlo seed")
        s.add_argument("--six-state", action="store_true", help="apply proposed read-window fix")
        s.add_argument("--vcd", help="write a GTKWave VCD file")
    a = p.parse_args(argv)
    fn = {"check": cmd_check, "run": cmd_run, "diag": cmd_diag, "handoff": cmd_handoff,
          "race": cmd_race}.get(a.cmd or "check")
    return fn(a)


if __name__ == "__main__":
    sys.exit(main())
