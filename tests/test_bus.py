"""Gate-level bus behaviour: diagnostic ROM, Section 11.2 handoff items,
Monte Carlo over delays and clock corners, and negative controls."""

import collections
import unittest

from sim import checks, corners, specdoc
from sim.board import P_D
from sim.board import build
from sim.handoff import measure
from sim.runner import Bench

DIAG = specdoc.diagnostic_rom()
CORNERS = (127e3, 380e3, 890e3)       # oscillator spread from sim/analog.py


def diag_bench(**kw):
    sym = {"loop": 0x800A, "trap": 0x8016}
    return Bench(image=DIAG, symbols=sym, **kw)


class TestDiagnosticRom(unittest.TestCase):
    def test_copies_switches_to_ram(self):
        b = diag_bench(seed=1)
        b.run_to("loop")
        for raw in (0xFF, 0x00, 0xA5, 0x5A):
            b.h.set_switch_byte(raw)
            b.run_cycles(60)
            self.assertEqual(b.ram.mem[0x0200], raw)
            self.assertEqual(b.cpu.core.a, raw)
        self.assertEqual(b.violations(), [])

    def test_boot_reads_reset_vector_then_8000(self):
        b = diag_bench(seed=2)
        b.run_cycles(80)
        cyc = [(rw, a) for _, rw, a, _, _ in b.cpu.cycles]
        i = cyc.index(("R", 0xFFFC))
        self.assertEqual(cyc[i + 1], ("R", 0xFFFD))
        self.assertEqual(cyc[i + 2], ("R", 0x8000))
        # nothing was written while CPU_RESET_N was low
        self.assertTrue(all(rw == "R" for rw, _ in cyc[:i]))

    def test_monte_carlo_delays_and_clock_corners(self):
        bad = []
        for f in CORNERS:
            for seed in range(8):
                b = diag_bench(seed=100 + seed, master_hz=f)
                b.h.set_switch_byte((seed * 37) & 0xFF)
                b.run_cycles(120)
                if b.violations() or b.ram.mem[0x200] != (seed * 37) & 0xFF:
                    bad.append((f, seed, b.violations()[:3]))
        self.assertEqual(bad, [])


class TestHandoff(unittest.TestCase):
    """Section 11.2 items 1-6 and 8, measured on the RAM-test scenario."""

    @classmethod
    def setUpClass(cls):
        cls.r = measure(dict(seed=11), scenario="ram")

    def test_no_violations(self):
        self.assertEqual(self.r["violations"], [])

    def test_item8_never_two_drivers(self):
        self.assertEqual(self.r["P_D_max_drivers"], 1)
        self.assertEqual(self.r["C_D_max_drivers"], 1)

    def test_item2_p_d_quiet_two_master_periods(self):
        self.assertGreater(self.r["P_D_min_gap_ns"], 1.9 * self.r["master_period_ns"])

    def test_item5_c_d_gap_at_least_one_master_period(self):
        self.assertGreater(self.r["C_D_min_gap_ns"], 0.95 * self.r["master_period_ns"])

    def test_item1_read_window(self):
        self.assertEqual(self.r["item1_rdwin_outside_phi2"], 0)
        self.assertEqual(self.r["item1_rdwin_outside_rwb"], 0)

    def test_item3_write_buffer_window(self):
        self.assertEqual(self.r["item3_u4_outside_phi2"], 0)
        self.assertEqual(self.r["item3_u4_outside_write"], 0)

    def test_item4_read_data_held_after_phi2_fall(self):
        self.assertGreater(self.r["item4_min_cd_hold_after_fall_ns"], 2000)

    def test_item6_cycle_register_changes_at_phi2_rise(self):
        self.assertEqual(self.r["item6_rwb_cycle_off_edge"], 0)

    def test_one_gpio_strobe_per_store_one_period_early(self):
        self.assertEqual(self.r["gpio_writes"], self.r["gpio_rising_edges"])
        lo, hi = self.r["gpio_edge_lead_before_phi2_fall_ns"]
        m = self.r["master_period_ns"]
        self.assertGreater(lo, 0.9 * m)
        self.assertLess(hi, 1.1 * m)


class TestReadWindowRace(unittest.TestCase):
    """Finding F1: at datasheet delay corners the read window outlives the
    CPU's address hold, so a newly selected device can enable while the old
    one still drives P_D.  The six-state Johnson variant removes it."""

    def overlaps(self, mutations, corner):
        b = diag_bench(seed=1, mutations=mutations)
        corner(b)
        b.sim.driver_watch.update(P_D)
        b.watch_edges("PHI2")
        b.h.set_switch_byte(0x96)
        b.run_cycles(150)
        falls = [t for t, o, n in b.edge_log["PHI2"] if o == 1 and n == 0]
        ov = []
        for n in P_D:
            for s, e, o in checks.driver_intervals(b.sim.driver_log.get(n, []),
                                                   b.sim.check_from, b.sim.now):
                if len(o) > 1:
                    ov.append((e - s, s - max(f for f in falls if f <= s), o))
        return b, ov

    def test_rev03_overlap_at_corner(self):
        b, ov = self.overlaps([], corners.read_window_trailing_edge)
        self.assertGreater(len(ov), 0)
        self.assertLess(max(d for d, _, _ in ov), 35)                 # tens of ns
        self.assertTrue(all(10 < after < 60 for _, after, _ in ov))   # just after PHI2 falls
        self.assertEqual(b.ram.mem[0x200], 0x96)                      # function unaffected

    def test_six_state_fix_has_no_overlap(self):
        b, ov = self.overlaps(["six_state"], corners.read_window_six_state_fix)
        self.assertEqual(ov, [])
        self.assertEqual(b.violations(), [])
        self.assertEqual(b.ram.mem[0x200], 0x96)


class TestMargins(unittest.TestCase):
    def test_runs_clean_far_above_clock_ceiling(self):
        b = diag_bench(seed=3, master_hz=8e6, por_ns=2000)
        b.h.set_switch_byte(0x3C)
        b.run_cycles(300)
        self.assertEqual(b.violations(), [])
        self.assertEqual(b.ram.mem[0x200], 0x3C)

    def test_unmapped_reads_tolerate_large_p_d_capacitance(self):
        b = Bench(scenario="mirrors", seed=2, cap_pf={"P_D": 400})
        b.run_to("done")
        self.assertEqual([b.ram.mem[a] for a in (0x284, 0x285, 0x286)], [0xFF] * 3)
        self.assertEqual(b.violations(), [])


class TestNegativeControls(unittest.TestCase):
    """Deliberately broken wiring must be detected, proving the checks bite."""

    def kinds(self, mutation):
        b = diag_bench(seed=3, mutations=[mutation])
        b.h.set_switch_byte(0x3C)
        b.run_cycles(150)
        return collections.Counter(v.kind for v in b.violations())

    def test_rev02_style_oe_from_rwb_contends(self):
        self.assertGreater(self.kinds("oe_from_rwb")["contention"], 0)

    def test_permanently_enabled_write_buffer_contends(self):
        self.assertGreater(self.kinds("u4_always_on")["contention"], 0)

    def test_writes_during_cpu_reset_detected(self):
        b = diag_bench(seed=4, mutations=["early_ok_no_reset"])
        b.watch_edges("RAM_WE_N")
        b.run_cycles(60)
        # first writes: CPU still in reset with RWB low at power-up -> WE strobes
        self.assertGreater(len(b.falling("RAM_WE_N", b.sim.check_from)), 0)

    def test_timing_checks_fire_at_16mhz_master(self):
        b = diag_bench(seed=3, master_hz=16e6, por_ns=2000)
        b.run_cycles(300)
        self.assertGreater(len(b.violations()), 0)


if __name__ == "__main__":
    unittest.main()
