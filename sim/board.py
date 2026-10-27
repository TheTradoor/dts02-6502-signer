"""DTS-02 rev 0.3 netlist, transcribed pin-by-pin from spec.md.

Every dict below is {DIP pin: net name}.  Section references point at the
table each entry comes from.  `mutations` lets tests deliberately break the
design (negative controls) to prove the checkers catch real faults.
"""

import random

from .cpu65c02 import W65C02S
from .engine import Simulator
from .parts import (HC595, HCT138, HCT157, HCT244, HCT273, HCT541, HCT573,
                    HCT74, And4x2, EPROM27256, Inv6, LoadOnly, Nand3x3,
                    Nand4x2, Nand8, Oscillator, Or4x2, ResetRC, SC56Digit,
                    SeriesR, Source, SRAM62256)

VCC, GND = "VCC", "GND"

# Pull-ups: net -> (ohms, budgeted pF).  Section 6.5 / 8 / 6.1 / 5.2
P_D = [f"P_D{i}" for i in range(8)]
C_D = [f"C_D{i}" for i in range(8)]
SW = [f"SW{i}" for i in range(8)]
KEYS = ["ENTER", "NEXT", "BACK", "APPROVE", "ABORT"]

DEFAULT_CAP = {"P_D": 60.0, "C_D": 45.0}


def build(rom_image, master_hz=380e3, seed=1, bb9=True, reset_delay_ns=None,
          por_ns=20_000, mutations=(), cap_pf=None, vcd=None, physical=None):
    """physical: optional (pin_remap, pullups) from sim.layout, where pullups is
    [(net, ohms, pf)].  The netlist is then whatever the breadboard wiring
    actually connects, not the schematic."""
    rng = random.Random(seed)
    sim = Simulator(rng)
    sim.vcd = vcd
    if physical is not None:
        sim.pin_remap = physical[0]
    mut = set(mutations)
    cap = dict(DEFAULT_CAP)
    if cap_pf:
        cap.update(cap_pf)

    sim.net(VCC).value = 1
    sim.net(VCC).supply = True
    sim.net(GND).value = 0
    sim.net(GND).supply = True

    def pullup(net, ohms, pf=20.0):
        n = sim.net(net)
        n.pullup_ohms = ohms
        n.cap_pf = pf

    if physical is not None:
        for n, ohms, pf in physical[1]:
            pullup(n, ohms, pf)
    else:
        for n in P_D:
            pullup(n, 4.7e3 if "weak_pd_pullups" not in mut else 47e3, cap["P_D"])
        for n in C_D:
            pullup(n, 10e3, cap["C_D"])
        for n in SW:
            pullup(n, 10e3)
        for n in ["CPU_RDY", "CPU_IRQB", "CPU_NMIB", "CPU_BE", "CPU_SOB"]:
            pullup(n, 10e3)
        pullup("POR_RAW_N", 4.7e3)

    A = [f"A{i}" for i in range(16)]

    # ---- U1 CPU, Section 6.1 -------------------------------------------
    u1 = {1: None, 2: "CPU_RDY", 3: None, 4: "CPU_IRQB", 5: None, 6: "CPU_NMIB",
          7: "SYNC", 8: VCC, 21: GND, 34: "RWB", 35: None, 36: "CPU_BE",
          37: "PHI2", 38: "CPU_SOB", 39: None, 40: "CPU_RESET_N"}
    for i in range(12):
        u1[9 + i] = A[i]
    for i, p in enumerate([22, 23, 24, 25]):
        u1[p] = A[12 + i]
    for i, p in enumerate([33, 32, 31, 30, 29, 28, 27, 26]):
        u1[p] = C_D[i]
    W65C02S(sim, "U1", u1)

    # ---- U2 SRAM / U3 EPROM, Section 6.2 -------------------------------
    common = {2: A[12], 3: A[7], 4: A[6], 5: A[5], 6: A[4], 7: A[3], 8: A[2],
              9: A[1], 10: A[0], 11: P_D[0], 12: P_D[1], 13: P_D[2], 14: GND,
              15: P_D[3], 16: P_D[4], 17: P_D[5], 18: P_D[6], 19: P_D[7],
              21: A[10], 22: "RD_WIN_N", 23: A[11], 24: A[9], 25: A[8],
              26: A[13], 28: VCC}
    ram = {**common, 1: A[14], 20: "RAM_CE_N", 27: "RAM_WE_N"}
    rom = {**common, 1: VCC, 20: "nA15", 27: A[14]}
    if "oe_from_rwb" in mut:          # revision-0.2 style: OE driven by RWB only
        ram[22] = "nRWB"
        rom[22] = "nRWB"
    SRAM62256(sim, "U2", ram)
    EPROM27256(sim, "U3", rom, rom_image)

    # ---- U4 write buffer, Section 6.3 ----------------------------------
    u4 = {1: "QA_N", 10: GND, 19: "RWB", 20: VCC}
    for i in range(8):
        u4[2 + i] = C_D[i]
        u4[18 - i] = P_D[i]
    if "u4_always_on" in mut:
        u4[1] = GND
        u4[19] = GND
    HCT541(sim, "U4", u4)

    # ---- U5 oscillator + reset conditioning, Section 5.3 ---------------
    Inv6(sim, "U5", {1: "OSC_RC", 2: "MASTER_CLK", 3: "POR_RAW_N", 4: "POR_INV",
                     5: "POR_INV", 6: "COLD_CLEAR_N", 9: "RESET_RC",
                     8: "RESET_RC_INV", 11: "RESET_RC_INV", 10: "RESET_RC_READY",
                     13: GND, 12: None, 14: VCC, 7: GND}, "HC14", skip=(2,))
    Oscillator(sim, "OSC", "MASTER_CLK", master_hz)
    Source(sim, "OSC_RC_NODE", "OSC_RC", 0)   # RC node; gate 1 is behavioural

    # ---- U6 Johnson counter, Section 5.5 -------------------------------
    six = "six_state" in mut     # proposed fix: 3-stage Johnson, RD window ends early
    HCT74(sim, "U6", {1: "COLD_CLEAR_N", 2: "QC_N" if six else "QB_N", 3: "MASTER_CLK", 4: VCC,
                      5: "Q_A", 6: "QA_N", 7: GND, 8: "QB_N", 9: "Q_B", 10: VCC,
                      11: "MASTER_CLK", 12: "Q_A", 13: "COLD_CLEAR_N", 14: VCC})

    # ---- U7 PHI2 buffer, Section 5.4 -----------------------------------
    Inv6(sim, "U7", {1: "QA_N", 2: "PHI2", 3: GND, 5: GND, 9: GND, 11: GND,
                     13: GND, 14: VCC, 7: GND}, "AC14")

    # ---- U8 INV, Section 7.1 -------------------------------------------
    Inv6(sim, "U8", {1: A[15], 2: "nA15", 3: "RWB", 4: "nRWB", 5: "RAM_CE_N",
                     6: "RAM_SELECT", 9: GND, 11: GND, 13: GND, 14: VCC, 7: GND},
         "HCT04")

    # ---- U9 PAGE ---------------------------------------------------------
    Nand8(sim, "U9", {1: "nA15", 2: A[14], 3: A[13], 4: A[12], 5: A[11], 6: A[10],
                      11: A[9], 12: A[8], 8: "IO_N", 14: VCC, 7: GND}, "HC30")

    # ---- U10 NAND --------------------------------------------------------
    u10 = {1: "nA15", 2: "IO_N", 3: "RAM_CE_N", 4: "EARLY_OK", 5: "nRWB",
           6: "WR_PHASE_N", 9: "Q_A", 10: "RWB", 8: "RD_WIN_N", 12: "Q_B",
           13: "RWB_CYCLE", 11: "HOLD_N", 14: VCC, 7: GND}
    if "hold_uses_live_rwb" in mut:   # drop the cycle register: hold on live RWB
        u10[13] = "RWB"
    if six:                           # U10 gate 3 freed; RD_WIN_N comes from U18 gate 2
        u10[9], u10[10], u10[8] = GND, GND, None
    Nand4x2(sim, "U10", u10, "HCT00")

    # ---- U11 AND ---------------------------------------------------------
    u11 = {1: "Q_A", 2: "QB_N", 3: "EARLY", 4: "EARLY", 5: "CPU_RESET_N",
           6: "EARLY_OK", 9: "COLD_CLEAR_N", 10: "RESET_RC_READY",
           8: "CPU_RESET_N", 12: "RD_WIN_N", 13: "HOLD_N", 11: "LATCH_OE_N",
           14: VCC, 7: GND}
    if "early_ok_no_reset" in mut:
        u11[5] = VCC
    And4x2(sim, "U11", u11, "HCT08")

    # ---- U12 OR ----------------------------------------------------------
    Or4x2(sim, "U12", {1: GND, 2: GND, 4: GND, 5: GND, 9: "Y2_N", 10: "WR_PHASE_N",
                       8: "WR_GPIO_N", 12: "WR_PHASE_N",
                       13: "WRITE_BLOCK" if bb9 else GND,
                       11: "RAM_WE_N", 14: VCC, 7: GND}, "HCT32")

    # ---- U13 PORT --------------------------------------------------------
    HCT138(sim, "U13", {1: A[0], 2: A[1], 3: A[2], 4: "IO_N", 5: GND, 6: VCC,
                        8: GND, 16: VCC, 15: "Y0_N", 14: "Y1_N", 13: "Y2_N",
                        12: "Y3_N", 11: "Y4_N", 10: None, 9: None, 7: None})

    # ---- U14 / U15 input buffers, Section 8 -----------------------------
    u14 = {1: "Y0_N", 19: "RD_WIN_N", 10: GND, 20: VCC}
    u15 = {1: "Y1_N", 19: "RD_WIN_N", 10: GND, 20: VCC}
    for i in range(8):
        u14[2 + i] = SW[i]
        u14[18 - i] = P_D[i]
        u15[18 - i] = P_D[i]
    for i, k in enumerate(KEYS):
        u15[2 + i] = f"KEY_{k}"
    if bb9:
        u15[7], u15[8], u15[9] = "MESSAGE_LOCK", "RESULT_LOCK", "FAULT"
    else:
        u15[7], u15[8], u15[9] = GND, GND, GND
    HCT541(sim, "U14", u14)
    HCT541(sim, "U15", u15)

    # ---- U16 GPIO latch ----------------------------------------------------
    HCT273(sim, "U16", {1: "CPU_RESET_N", 2: "DATA", 3: P_D[0], 4: P_D[1],
                        5: "CLK0", 6: "CLK1", 7: P_D[2], 8: P_D[3], 9: "CLK2",
                        10: GND, 11: "WR_GPIO_N", 12: "CLK3", 13: P_D[4],
                        14: P_D[5], 15: "LATCH", 16: "DISPLAY_ENABLE",
                        17: P_D[6], 18: P_D[7], 19: None, 20: VCC})

    # ---- U17 key conditioning ------------------------------------------
    Inv6(sim, "U17", {1: "KEYIN_ENTER", 2: "KEY_ENTER", 3: "KEYIN_NEXT",
                      4: "KEY_NEXT", 5: "KEYIN_BACK", 6: "KEY_BACK",
                      9: "KEYIN_APPROVE", 8: "KEY_APPROVE", 11: "KEYIN_ABORT",
                      10: "KEY_ABORT", 13: GND, 12: None, 14: VCC, 7: GND}, "HC14")

    # ---- U18 display blanking -------------------------------------------
    Nand3x3(sim, "U18", {1: "CPU_RESET_N", 2: "DISPLAY_ENABLE",
                         13: "FAULT_N" if bb9 else VCC, 12: "DISPLAY_OE_N",
                         3: "Q_A" if six else GND, 4: "QC_N" if six else GND,
                         5: "RWB" if six else GND, 6: "RD_WIN_N" if six else None,
                         9: GND, 10: GND, 11: GND, 8: None, 7: GND, 14: VCC}, "HC10")

    # ---- U19 / U20 fanout + 220R ------------------------------------------
    u19 = {1: GND, 19: GND, 10: GND, 20: VCC}
    u20 = {1: GND, 19: GND, 10: GND, 20: VCC}
    for b, (ip, op) in enumerate([(2, 18), (4, 16), (6, 14), (8, 12)]):
        u19[ip], u19[op] = f"CLK{b}", f"U19_{op}"
        u20[ip], u20[op] = "DISPLAY_OE_N", f"U20_{op}"
        SeriesR(sim, f"R_SRCLK{b}", {1: f"U19_{op}", 2: f"B{b}_SRCLK"})
        SeriesR(sim, f"R_OE{b}", {1: f"U20_{op}", 2: f"B{b}_OE_N"})
    for b, (ip, op) in enumerate([(11, 9), (13, 7), (15, 5), (17, 3)]):
        u19[ip], u19[op] = "LATCH", f"U19_{op}"
        u20[ip], u20[op] = "CPU_RESET_N", f"U20_{op}"
        SeriesR(sim, f"R_RCLK{b}", {1: f"U19_{op}", 2: f"B{b}_RCLK"})
        SeriesR(sim, f"R_SRCLR{b}", {1: f"U20_{op}", 2: f"B{b}_SRCLR_N"})
        SeriesR(sim, f"R_DATA{b}", {1: "DATA", 2: f"B{b}_SER"})
    HCT244(sim, "U19", u19)
    HCT244(sim, "U20", u20)

    # ---- U21..U36 HC595 + SC56-11 digits ----------------------------------
    seg_q = {"a": 15, "b": 1, "c": 2, "d": 3, "e": 4, "f": 5, "g": 6, "dp": 7}
    digits = []
    for b in range(4):
        for j in range(4):
            u = 21 + 4 * b + j
            dig = 4 * b + j
            ser = f"B{b}_SER" if j == 0 else f"U{u - 1}_QH"
            pins = {8: GND, 16: VCC, 9: f"U{u}_QH" if j < 3 else None,
                    10: f"B{b}_SRCLR_N", 11: f"B{b}_SRCLK", 12: f"B{b}_RCLK",
                    13: f"B{b}_OE_N", 14: ser}
            dpins = {3: GND, 8: GND}
            for s, qp in seg_q.items():
                pins[qp] = f"U{u}_Q{s}"
                SeriesR(sim, f"R_U{u}_{s}", {1: f"U{u}_Q{s}", 2: f"D{dig}_{s}"})
                dpins[SC56Digit.SEG_PIN[s]] = f"D{dig}_{s}"
            HC595(sim, f"U{u}", pins)
            digits.append(SC56Digit(sim, f"D{dig}", dpins))

    # ---- BB9 lock board, Section 9.2 ----------------------------------------
    if bb9:
        HCT74(sim, "U37", {1: "COLD_CLEAR_N", 13: "COLD_CLEAR_N", 2: VCC, 4: VCC,
                           10: VCC, 12: VCC, 14: VCC, 3: "SET_MESSAGE_N",
                           5: "MESSAGE_LOCK", 6: None, 7: GND, 8: None,
                           9: "RESULT_LOCK", 11: "SET_RESULT_N"})
        HCT74(sim, "U38", {1: "COLD_CLEAR_N", 2: "FAULT_NEXT", 3: "PHI2", 4: VCC,
                           10: VCC, 13: VCC, 14: VCC, 5: "FAULT", 6: "FAULT_N",
                           7: GND, 11: GND, 12: GND, 8: None, 9: None})
        HCT157(sim, "U39", {1: A[13], 2: "MESSAGE_LOCK", 3: "RESULT_LOCK",
                            4: "SELECTED_LOCK", 5: GND, 6: GND, 8: GND, 10: GND,
                            11: GND, 13: GND, 14: GND, 15: GND, 7: None, 9: None,
                            12: None, 16: VCC})
        And4x2(sim, "U40", {1: "RAM_SELECT", 2: A[14], 3: "HIGH_RAM",
                            4: "HIGH_RAM", 5: "SELECTED_LOCK", 6: "PROTECTED",
                            9: "PROTECTED", 10: "nRWB", 8: "PROT_WRITE",
                            12: "PROT_WRITE", 13: "CPU_RESET_N", 11: "BAD_WRITE",
                            7: GND, 14: VCC}, "HCT08")
        Or4x2(sim, "U41", {1: "FAULT", 2: "BAD_WRITE", 3: "FAULT_NEXT",
                           4: "FAULT", 5: "PROTECTED", 6: "WRITE_BLOCK",
                           9: "Y3_N", 10: "WR_PHASE_N", 8: "SET_MESSAGE_N",
                           12: "Y4_N", 13: "WR_PHASE_N", 11: "SET_RESULT_N",
                           7: GND, 14: VCC}, "HCT32")
        for led in ["MESSAGE_LOCK", "RESULT_LOCK", "FAULT"]:
            LoadOnly(sim, f"LED_{led}", {1: led})

    # ---- BB10 read latch + cycle register, Section 6.4 -----------------------
    u44 = {1: "LATCH_OE_N", 10: GND, 11: "EARLY", 20: VCC}
    for i in range(8):
        u44[2 + i] = P_D[i]
        u44[19 - i] = C_D[i]
    HCT573(sim, "U44", u44)
    u45 = {1: "COLD_CLEAR_N", 2: "RWB", 3: "Q_A", 4: VCC,
           5: "RWB_CYCLE", 6: None, 7: GND, 8: None, 9: None,
           10: VCC, 13: VCC, 11: GND, 12: GND, 14: VCC}
    if six:                           # spare flip-flop B becomes Johnson stage C
        u45.update({13: "COLD_CLEAR_N", 12: "Q_B", 11: "MASTER_CLK", 10: VCC,
                    9: "Q_C", 8: "QC_N"})
    HCT74(sim, "U45", u45)

    # ---- harness sources ------------------------------------------------------
    sim.master_per_cpu = 6 if six else 4
    if reset_delay_ns is None:
        reset_delay_ns = 30 * sim.master_per_cpu * 1e9 / master_hz     # ~30 CPU clocks (time-compressed RC)
    h = Harness(sim, digits, reset_delay_ns, por_ns)
    return sim, h


class Harness:
    """Behavioural stand-ins for U42 supervisor, RESET_RC, keys and switches."""

    def __init__(self, sim, digits, reset_delay_ns, por_ns):
        self.sim = sim
        self.digits = digits
        self.por = Source(sim, "U42", "POR_RAW_N", 0)
        self.reset_btn = Source(sim, "RESET_BTN", "RESET_BTN_N", "Z")
        sim.net("RESET_BTN_N").pullup_ohms = 100e3
        sim.net("RESET_BTN_N").cap_pf = 1.0
        self.reset_rc = ResetRC(sim, "RESET_RC_MODEL", "COLD_CLEAR_N", "RESET_BTN_N",
                                "RESET_RC", reset_delay_ns)
        self.switches = [Source(sim, f"DIPSW{i}", f"SW{i}", "Z") for i in range(8)]
        self.keys = {k: Source(sim, f"KEYSW_{k}", f"KEYIN_{k}", 1) for k in KEYS}
        self.por_ns = por_ns
        sim.check_from = por_ns / 2      # supplies settled, POR still asserted
        for p in sim.parts.values():
            p.start(0.0)
        for n in sim.nets.values():
            if n.pullup_ohms and not n.drivers:
                sim.schedule(0.0, lambda t, n=n: n.resolve(t))
        self.por.set("Z", por_ns)       # supervisor releases; 4.7k pull-up

    # switches: raw port bit is 0 when the switch is closed
    def set_switch_byte(self, raw):
        for i, s in enumerate(self.switches):
            s.set("Z" if (raw >> i) & 1 else 0)

    def press_key(self, k, down=True):
        self.keys[k].set(0 if down else 1)

    def press_reset(self, down=True):
        self.reset_btn.set(0 if down else "Z")

    def cold_clear(self, low_ns=10_000):
        t = self.sim.now
        self.por.set(0, t)
        self.por.set("Z", t + low_ns)

    def display(self):
        """Return list of 16 segment masks (None for unknown) left to right."""
        return [d.lit() for d in self.digits]
