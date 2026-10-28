"""Closed-form analog checks of the spec's component values.

The digital simulator treats edges as ideal; this module covers what it
cannot: the RC oscillator frequency spread, the reset-release delay, pull-up
rise times against realistic capacitance, and the power fixture.  Datasheet
figures used here are listed in DATASHEET with their source.
"""

import itertools
import math

DATASHEET = {
    # TI SN74HC14, VCC = 4.5 V, -40..125 C (scaled by 5/4.5 for a 5 V rail)
    "HC14_VTp_4v5": (1.55, 3.15),
    "HC14_VTn_4v5": (0.90, 2.45),
    "HC14_dVT_4v5": (0.40, 2.10),
    "HC14_tpd_ns": (8, 31),
    # Microchip MCP1x0-460
    "MCP130_460_trip": (4.35, 4.475, 4.60),
    # ST L7805AC output over line/load/temperature
    "L7805AC_out": (4.80, 5.20),
}

VDD = 5.0


def _thresholds(steps=33):
    s = VDD / 4.5
    lo_p, hi_p = (v * s for v in DATASHEET["HC14_VTp_4v5"])
    lo_n, hi_n = (v * s for v in DATASHEET["HC14_VTn_4v5"])
    lo_h, hi_h = (v * s for v in DATASHEET["HC14_dVT_4v5"])
    for i, j in itertools.product(range(steps), repeat=2):
        vp = lo_p + (hi_p - lo_p) * i / (steps - 1)
        vn = lo_n + (hi_n - lo_n) * j / (steps - 1)
        if lo_h <= vp - vn <= hi_h and vn > 0:
            yield vp, vn


def hc14_osc_freq(r, c, vp, vn, tpd_ns):
    t = r * c * (math.log((VDD - vn) / (VDD - vp)) + math.log(vp / vn)) + 2 * tpd_ns * 1e-9
    return 1 / t


def oscillator(r=10e3, c=330e-12, r_tol=0.05, c_tol=0.05, c_stray=(3e-12, 10e-12)):
    """Range of MASTER_CLK over HC14 threshold spread, R/C tolerance, stray C."""
    fs = []
    for vp, vn in _thresholds():
        for rr in (r * (1 - r_tol), r * (1 + r_tol)):
            for cc in (c * (1 - c_tol) + c_stray[0], c * (1 + c_tol) + c_stray[1]):
                for tpd in DATASHEET["HC14_tpd_ns"]:
                    fs.append(hc14_osc_freq(rr, cc, vp, vn, tpd))
    s = VDD / 4.5
    vp_mid = sum(DATASHEET["HC14_VTp_4v5"]) / 2 * s
    vn_mid = sum(DATASHEET["HC14_VTn_4v5"]) / 2 * s
    nominal = hc14_osc_freq(r, c + 5e-12, vp_mid, vn_mid, 15)
    return dict(min_hz=min(fs), max_hz=max(fs), mid_hz=nominal,
                frac_above_400k=sum(f > 400e3 for f in fs) / len(fs))


def capacitor_for_ceiling(f_max=400e3, r=10e3):
    """Smallest standard C keeping the worst-case master clock <= f_max."""
    for c in [330e-12, 390e-12, 470e-12, 560e-12, 680e-12, 820e-12, 1e-9]:
        if oscillator(r=r, c=c)["max_hz"] <= f_max:
            return c, oscillator(r=r, c=c)
    return None, None


def reset_release(r=100e3, c=1e-6, r_tol=0.05, c_tol=0.20):
    s = VDD / 4.5
    vps = [v * s for v in DATASHEET["HC14_VTp_4v5"]]
    ts = []
    for vp in vps:
        for rr in (r * (1 - r_tol), r * (1 + r_tol)):
            for cc in (c * (1 - c_tol), c * (1 + c_tol)):
                ts.append(rr * cc * math.log(VDD / (VDD - vp)))
    typ = r * c * math.log(VDD / (VDD - sum(vps) / 2))
    return dict(min_s=min(ts), max_s=max(ts), typ_s=typ)


def pullup(r_ohm, c_pf, vih=2.0, v=VDD):
    tau = r_ohm * c_pf * 1e-12
    return dict(tau_ns=tau * 1e9, rise_10_90_ns=2.197 * tau * 1e9,
                t_to_vih_ns=tau * math.log(v / (v - vih)) * 1e9,
                t_to_4v_ns=tau * math.log(v / (v - 4.0)) * 1e9,
                slew_ns_per_v_at_2v=tau / (v - 2.0) * 1e9)


# Datasheet maximum pin capacitances (pF).  Sources: TI HCT Ci 10 pF max;
# Alliance AS6C62256 CI/O 8 pF; Microchip AT27C256R COUT 12 pF; three-state
# HCT outputs taken as 15 pF.  Breadboard: ~2.5 pF per occupied terminal
# strip; hookup wire over a baseplate ~0.4 pF/cm.
P_D_LOADS = {"U4 out": 15, "U44 in": 10, "U2 I/O": 8, "U3 out": 12,
             "U14 out": 15, "U15 out": 15, "U16 in": 10}
P_D_TYPICAL = {"U4 out": 5, "U44 in": 3.5, "U2 I/O": 5, "U3 out": 6,
               "U14 out": 5, "U15 out": 5, "U16 in": 3.5}


def p_d_capacitance(wire_mm=250, strip_pf=2.5, wire_pf_per_cm=0.4):
    extra = len(P_D_LOADS) * strip_pf + wire_mm / 10 * wire_pf_per_cm
    return dict(pins_max=sum(P_D_LOADS.values()), pins_typ=sum(P_D_TYPICAL.values()),
                board=extra, total_max=sum(P_D_LOADS.values()) + extra,
                total_typ=sum(P_D_TYPICAL.values()) + extra)


def power():
    v_batt_max, v_diode, v_out = 12.8, 0.35, 5.0
    i_assumed = 0.30
    p = (v_batt_max - v_diode - v_out) * i_assumed
    rise = p * (15 + 5)
    # worst-case current estimate (mA): display bound, SRAM/EPROM active,
    # HCT delta-ICC for 16 P_D receivers at a 2.4 V high, pull-ups when low,
    # bleeder, status LEDs, logic quiescent/switching allowance
    budget = {"display (resistor bound)": 214.4, "SRAM active": 30, "EPROM active": 20,
              "HCT dICC, 16 inputs at 2.4 V": 16 * 2.9, "P_D pull-ups low": 8 * 1.06,
              "C_D pull-ups low": 8 * 0.5, "bleeder 1k": 5.0, "status LEDs": 3 * 0.64,
              "CPU + logic allowance": 10}
    i_worst = sum(budget.values()) / 1000
    p_worst = (v_batt_max - v_diode - v_out) * i_worst
    return dict(p_spec_w=p, rise_spec_c=rise, budget_ma=budget, i_worst_a=i_worst,
                p_worst_w=p_worst, rise_worst_c=p_worst * 20,
                cell_floor_8=7.9 / 8, cell_floor_6=7.9 / 6)


def display_current():
    seg_nom = (5 - 1.9) / 3300
    seg_bound = 5.25 / (3300 * 0.95)
    return dict(seg_nom_ma=seg_nom * 1e3, panel_nom_ma=seg_nom * 128 * 1e3,
                seg_bound_ma=seg_bound * 1e3, chip_bound_ma=seg_bound * 8 * 1e3,
                panel_bound_ma=seg_bound * 128 * 1e3)


def supervisor_margin():
    trip_max = DATASHEET["MCP130_460_trip"][2]
    reg_min = DATASHEET["L7805AC_out"][0]
    return dict(margin_460_v=reg_min - trip_max, margin_475_v=reg_min - 4.75)


def report():
    lines = []
    o = oscillator()
    lines.append(f"Oscillator 10k/330p: {o['min_hz']/1e3:.0f}-{o['max_hz']/1e3:.0f} kHz over "
                 f"HC14 threshold/R/C spread (mid-threshold {o['mid_hz']/1e3:.0f} kHz); "
                 f"{100*o['frac_above_400k']:.0f}% of corners exceed 400 kHz")
    c, oc = capacitor_for_ceiling()
    if c:
        lines.append(f"  smallest C with worst case <=400 kHz: {c*1e12:.0f} pF "
                     f"({oc['min_hz']/1e3:.0f}-{oc['max_hz']/1e3:.0f} kHz)")
    r = reset_release()
    lines.append(f"Reset release 100k/1uF: {r['min_s']*1e3:.0f}-{r['max_s']*1e3:.0f} ms "
                 f"(mid {r['typ_s']*1e3:.0f} ms); spec states 70-90 ms")
    pdc = p_d_capacitance()
    lines.append(f"P_D line capacitance estimate: typ {pdc['total_typ']:.0f} pF, "
                 f"datasheet-max {pdc['total_max']:.0f} pF (budget 60 pF)")
    for label, rr, cc in [("P_D 4.7k @60pF", 4.7e3, 60), ("P_D 4.7k @typ", 4.7e3, pdc["total_typ"]),
                          ("P_D 4.7k @max", 4.7e3, pdc["total_max"]),
                          ("P_D 47k (rev0.2) @60pF", 47e3, 60), ("C_D 10k @45pF", 10e3, 45)]:
        p = pullup(rr, cc)
        lines.append(f"  {label:24s} tau {p['tau_ns']:5.0f} ns, 10-90% {p['rise_10_90_ns']:5.0f} ns, "
                     f"->2.0V {p['t_to_vih_ns']:5.0f} ns, slew@2V {p['slew_ns_per_v_at_2v']:5.0f} ns/V "
                     f"(HCT limit 500)")
    pw = power()
    lines.append(f"Regulator: spec case {pw['p_spec_w']:.2f} W / +{pw['rise_spec_c']:.0f} C; "
                 f"worst-case current estimate {pw['i_worst_a']*1e3:.0f} mA -> "
                 f"{pw['p_worst_w']:.2f} W / +{pw['rise_worst_c']:.0f} C")
    lines.append(f"Battery floor per cell: 8 cells {pw['cell_floor_8']:.3f} V, 6 cells {pw['cell_floor_6']:.3f} V")
    d = display_current()
    lines.append(f"Display: {d['seg_nom_ma']:.2f} mA/segment, {d['panel_nom_ma']:.0f} mA panel nominal; "
                 f"bound {d['seg_bound_ma']:.3f} mA/seg, {d['chip_bound_ma']:.1f} mA/chip, "
                 f"{d['panel_bound_ma']:.1f} mA panel")
    s = supervisor_margin()
    lines.append(f"Supervisor margin to L7805AC 4.80 V min: -460 {s['margin_460_v']*1e3:.0f} mV, "
                 f"-475 {s['margin_475_v']*1e3:.0f} mV")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())


# ---------------------------------------------------------------------------
# rev 0.5 keypad and display interface (spec Section 8)
# ---------------------------------------------------------------------------

def keypad_levels(pullup=10e3, vdd=VDD, vol_hct273=0.05):
    """Column low level when a key connects it through the row diode to a low
    HCT273 output: pull-up current through the diode."""
    i = vdd / pullup
    vf_schottky = 0.25 + 0.02 * (i / 1e-3)      # BAT85 ~0.25 V at 0.5 mA (datasheet curve)
    vf_silicon = 0.60                           # 1N4148 at ~0.5 mA
    return dict(i_ma=i * 1e3, schottky_low_v=vol_hct273 + vf_schottky,
                silicon_low_v=vol_hct273 + vf_silicon, hct_vil=0.8)


def display_interface(vcc=VDD, r_series=1e3, r_pd=10e3, iseg_ma=12.0, iq_ma=8.0,
                      vce_sat=0.3, vbe=0.8, r_base=150.0, vce_sat_q2=0.2):
    """U19 -> 1k -> MAX7219 input with 10k pull-down; Q1/Q2 supply switch."""
    v_in = vcc * r_pd / (r_pd + r_series)
    per_chip = 8 * iseg_ma + iq_ma
    peak = 2 * per_chip
    ib = (vcc - vbe - vce_sat_q2) / r_base * 1e3
    return dict(mx_input_high_v=v_in, disp_v=vcc - vce_sat, display_peak_ma=peak,
                q1_base_ma=ib, q1_beta_forced=peak / ib)
