"""Section 11.2 bus-handoff measurements on a simulated waveform."""

from . import checks
from .board import C_D, P_D
from .runner import Bench


def measure(bench_kwargs, scenario="ram", label="done", run_cycles=None):
    b = Bench(scenario=scenario, **bench_kwargs)
    b.sim.driver_watch.update(P_D + C_D)
    b.watch_edges("PHI2", "RD_WIN_N", "RWB", "RWB_CYCLE", "WR_GPIO_N", "LATCH_OE_N", "EARLY")
    b.run_to(label)
    t0 = b.sim.check_from + 50_000       # skip power-up / reset
    t1 = b.sim.now
    master = b.cpu_period / b.sim.master_per_cpu
    res = {"master_period_ns": master}

    pd = [checks.handoff_stats(checks.driver_intervals(b.sim.driver_log.get(n, []), t0, t1)) for n in P_D]
    cd = [checks.handoff_stats(checks.driver_intervals(b.sim.driver_log.get(n, []), t0, t1)) for n in C_D]
    res["P_D_max_drivers"] = max(s["max_drivers"] for s in pd)
    res["C_D_max_drivers"] = max(s["max_drivers"] for s in cd)
    res["P_D_min_gap_owner_change_ns"] = min(s["min_gap_owner_change_ns"] for s in pd)
    res["C_D_min_gap_owner_change_ns"] = min(s["min_gap_owner_change_ns"] for s in cd)
    res["P_D_min_gap_ns"] = min(s["min_gap_ns"] for s in pd)
    res["C_D_min_gap_ns"] = min(s["min_gap_ns"] for s in cd)

    phi_hi = checks.high_intervals(b.edge_log["PHI2"], t0, t1)
    rwb_hi = checks.high_intervals(b.edge_log["RWB"], t0, t1)
    rd_lo = checks.low_intervals(b.edge_log["RD_WIN_N"], t0, t1)
    # item 1: RD_WIN_N low only while PHI2 high (<=100 ns skew) and RWB high
    res["item1_rdwin_outside_phi2"] = len(checks.contained(rd_lo, phi_hi, 100))
    # RWB itself changes ~10-30 ns after PHI2 falls; allow the same 100 ns skew
    res["item1_rdwin_outside_rwb"] = len(checks.contained(rd_lo, rwb_hi, 100))
    # item 3: U4 drives P_D only inside PHI2-high write cycles (enable/release skew)
    u4 = []
    for n in P_D:
        for s, e, o in checks.driver_intervals(b.sim.driver_log.get(n, []), t0, t1):
            if "U4" in o:
                u4.append((s, e))
    res["item3_u4_outside_phi2"] = len(checks.contained(u4, phi_hi, 50))
    rwb_lo = checks.low_intervals(b.edge_log["RWB"], t0, t1)
    res["item3_u4_outside_write"] = len(checks.contained(u4, rwb_lo, 50))
    # item 4: U44 keeps driving C_D >= 2 us (at 380 kHz) after PHI2 falls in reads
    falls = [t for t, o, n in b.edge_log["PHI2"] if o == 1 and n == 0 and t >= t0]
    reads = {round(t): rw for t, rw, a, d, s in b.cpu.cycles}
    hold_after = []
    u44 = checks.driver_intervals(b.sim.driver_log.get("C_D0", []), t0, t1)
    for tf in falls:
        if tf > t1 - 2 * master:        # run stopped mid-cycle
            continue
        cyc = reads.get(round(tf))
        if cyc != "R":
            continue
        for s, e, o in u44:
            if "U44" in o and s <= tf <= e:
                hold_after.append(e - tf)
                break
        else:
            hold_after.append(0.0)
    res["item4_min_cd_hold_after_fall_ns"] = min(hold_after) if hold_after else None
    # item 6: RWB_CYCLE changes only near PHI2 rising edges
    rises = [t for t, o, n in b.edge_log["PHI2"] if o == 0 and n == 1]
    bad6 = 0
    for t, o, n in b.edge_log["RWB_CYCLE"]:
        if t < t0:
            continue
        if not any(-50 <= t - r <= 100 for r in rises):
            bad6 += 1
    res["item6_rwb_cycle_off_edge"] = bad6
    # WR_GPIO_N: one rising edge per $7F02 write, ~one master period before PHI2 falls
    gpio_writes = [t for t, rw, a, d, s in b.cpu.cycles if rw == "W" and t >= t0
                   and 0x7F00 <= a <= 0x7FFF and (a & 7) == 2]
    gp_rise = b.rising("WR_GPIO_N", t0)
    res["gpio_writes"] = len(gpio_writes)
    res["gpio_rising_edges"] = len(gp_rise)
    lead = []
    for r in gp_rise:
        nxt = [f for f in falls if f > r]
        if nxt:
            lead.append(nxt[0] - r)
    res["gpio_edge_lead_before_phi2_fall_ns"] = (min(lead), max(lead)) if lead else None
    res["violations"] = b.violations()
    res["cycles"] = len(b.cpu.cycles)
    return res
