"""Minimal VCD writer so simulated waveforms can be inspected in GTKWave."""

import re

DEFAULT_NETS = (["MASTER_CLK", "Q_A", "Q_B", "PHI2", "EARLY", "RWB", "RWB_CYCLE",
                 "RD_WIN_N", "HOLD_N", "LATCH_OE_N", "WR_PHASE_N", "RAM_WE_N",
                 "RAM_CE_N", "nA15", "IO_N", "WR_GPIO_N", "COLD_CLEAR_N",
                 "CPU_RESET_N", "MESSAGE_LOCK", "RESULT_LOCK", "FAULT",
                 "WRITE_BLOCK", "DISPLAY_OE_N"]
                + [f"A{i}" for i in range(16)] + [f"C_D{i}" for i in range(8)]
                + [f"P_D{i}" for i in range(8)])


class VCD:
    def __init__(self, path, nets=DEFAULT_NETS):
        self.f = open(path, "w")
        self.ids = {}
        self.f.write("$timescale 1ps $end\n$scope module dts02 $end\n")
        for i, n in enumerate(nets):
            ident = self._ident(i)
            self.ids[n] = ident
            safe = re.sub(r"[^A-Za-z0-9_]", "_", n)
            self.f.write(f"$var wire 1 {ident} {safe} $end\n")
        self.f.write("$upscope $end\n$enddefinitions $end\n")
        self.last_t = None

    @staticmethod
    def _ident(i):
        chars = "".join(chr(c) for c in range(33, 127))
        s = ""
        i += 1
        while i:
            i, r = divmod(i - 1, len(chars))
            s += chars[r]
        return s

    def change(self, t, net, value):
        ident = self.ids.get(net.name)
        if ident is None:
            return
        ps = int(round(t * 1000))
        if ps != self.last_t:
            self.f.write(f"#{ps}\n")
            self.last_t = ps
        v = {0: "0", 1: "1", "X": "x", "Z": "z"}[value]
        self.f.write(f"{v}{ident}\n")

    def close(self):
        self.f.close()
