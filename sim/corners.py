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
    """Same corner on rev 0.4, where RD_WIN_N comes from U18 gate 2."""
    read_window_trailing_edge(bench)
    bench.sim.parts["U18"]._delays[("tpd", 6)] = 30.0


def brownout_write_corner(bench):
    """Cold clear disables the write buffer U4 (via /Q_A) as fast as possible
    while the SRAM write strobe (via CPU_RESET_N / EARLY_OK) ends as late as
    possible: the only way a dip can corrupt the byte being written."""
    p = bench.sim.parts
    for k in [("q", 0, True)]:
        p["U6"]._delays[k] = 6.0                      # HCT74 CLR->Q min
    p["U6"]._delays[("qn_skew", 0, True)] = 0.0
    for i in range(8):
        p["U4"]._delays[("dis", i)] = 8.0             # HCT541 disable min
    p["U11"]._delays[("tpd", 8)] = 30.0               # CPU_RESET_N
    p["U11"]._delays[("tpd", 6)] = 30.0               # EARLY_OK
    p["U11"]._delays[("tpd", 3)] = 30.0               # EARLY
    p["U10"]._delays[("tpd", 6)] = 30.0               # WR_PHASE_N
    p["U12"]._delays[("tpd", 11)] = 30.0              # RAM_WE_N
