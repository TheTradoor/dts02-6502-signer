"""Deterministic worst-case delay corners for specific timing races."""


def read_window_trailing_edge(bench):
    """Slowest RD_WIN_N release vs fastest CPU address change + decode.

    RD_WIN_N = NAND(Q_A, RWB) (U10 pin 8) rises after raw Q_A falls; the CPU
    sees PHI2 fall one AC14 delay later and changes its address tAH after
    that; nA15 (U8) then re-selects ROM/RAM.  Spec Section 6.5 assumes the
    output enables are inactive before any chip-select change.
    """
    p = bench.sim.parts
    p["U10"]._delays[("tpd", 8)] = 30.0          # HCT00 max
    p["U7"]._delays[("tpd", 2)] = 1.5            # AC14 min
    p["U8"]._delays[("tpd", 2)] = 4.0            # HCT04 min (nA15)
    p["U10"]._delays[("tpd", 3)] = 4.0           # HCT00 min (RAM_CE_N)
    p["U13"]._delays.update({("y", i): 6.0 for i in range(8)})   # HCT138 min
    cpu = p["U1"]
    for i in range(16):
        cpu._delays[("ads", i)] = 10.0           # tAH min
    cpu._delays["rwb"] = 10.0


def read_window_six_state_fix(bench):
    """Same corner, applied to the six-state Johnson variant (RD_WIN_N from U18)."""
    read_window_trailing_edge(bench)
    bench.sim.parts["U18"]._delays[("tpd", 6)] = 30.0
