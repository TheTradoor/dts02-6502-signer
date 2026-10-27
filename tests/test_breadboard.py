"""Hole-level breadboard layout: physical connectivity, harness rules, and
simulation of the netlist derived from the physical wiring."""

import collections
import unittest

from sim import checks, layout as LY, specdoc
from sim.runner import Bench

L = LY.generate()
PHYS = LY.physical_netlist(L)


def phys_bench(faults=(), layout=None, **kw):
    lay = layout or L
    remap, pull, con, cap = LY.physical_netlist(lay, faults=faults) if (faults or layout) else PHYS
    return Bench(physical=(remap, pull), **kw), con


class TestLayout(unittest.TestCase):
    def test_no_allocation_errors(self):
        self.assertEqual(L.errors, [])         # no double-used hole, full strip/rail, long leads

    def test_every_strip_within_five_holes(self):
        per = collections.Counter(LY.strip_key(h) for h in L.used if h[1] in LY.COLS_L + LY.COLS_R)
        self.assertLessEqual(max(per.values()), 5)

    def test_rail_halves_within_capacity(self):
        self.assertLessEqual(max(L.rail_count.values()), LY.RAIL_HOLES_PER_HALF)

    def test_physical_connectivity_equals_schematic(self):
        remap, pull, con, cap = PHYS
        self.assertEqual(con["shorts"], [])
        self.assertEqual(con["opens"], [])
        diff = {k: v for k, v in remap.items() if k in L.terminals and v != L.terminals[k]}
        self.assertEqual(diff, {})

    def test_erc_on_physical_netlist(self):
        b, _ = phys_bench(scenario="idle")
        errors, notes = checks.erc(b.sim)
        self.assertEqual(errors, [])

    def test_section_3_4_harness_rules(self):
        problems, targets, info, _ = LY.harness_checks(L)
        self.assertEqual(problems, [])

    def test_phi2_cpu_segment_within_target(self):
        _, _, info, _ = LY.harness_checks(L)
        self.assertLess(info["PHI2 U7->U1 segment mm"], 50)

    def test_finding_length_targets_not_met_by_spec_placement(self):
        # F9: Section 3.2 C_D/P_D length targets are unreachable with the
        # Section 3.3 placement; capacitance stays well inside the margin.
        _, targets, info, _ = LY.harness_checks(L)
        self.assertGreater(info["C_D max line mm"], 100)
        self.assertGreater(info["P_D max line mm"], 250)
        cap = PHYS[3]
        self.assertLess(max(cap[f"P_D{i}"] for i in range(8)), 120)
        self.assertLess(max(cap[f"C_D{i}"] for i in range(8)), 60)

    def test_finding_phi2_stub_to_bb9_and_buffer_fix(self):
        _, _, info, _ = LY.harness_checks(L)
        self.assertIn("BB9", info["PHI2 boards"])
        self.assertGreater(info["PHI2 total mm"], 300)
        fixed = LY.generate(phi2_fault_buffer=True)
        _, _, info2, _ = LY.harness_checks(fixed)
        self.assertEqual(info2["PHI2 boards"], ["BB1"])
        self.assertLess(info2["PHI2 total mm"], 60)


class TestPhysicalSimulation(unittest.TestCase):
    def test_diagnostic_rom(self):
        b, _ = phys_bench(image=specdoc.diagnostic_rom(), symbols={"loop": 0x800A}, seed=3)
        b.h.set_switch_byte(0x5A)
        b.run_to("loop")
        b.run_cycles(60)
        self.assertEqual(b.ram.mem[0x200], 0x5A)
        self.assertEqual(b.violations(), [])

    def test_mirrors_unmapped_reads(self):
        b, _ = phys_bench(scenario="mirrors", seed=3)
        b.run_to("done")
        self.assertEqual([b.ram.mem[a] for a in (0x284, 0x285, 0x286)], [0xFF] * 3)
        self.assertEqual(b.violations(), [])

    def test_message_lock_fault(self):
        b, _ = phys_bench(scenario="msg_fault", seed=4)
        b.run_to("done")
        self.assertEqual((b.net("FAULT"), b.net("MESSAGE_LOCK")), (1, 1))
        self.assertEqual(b.ram.mem[0x4000], 0x11)
        self.assertEqual(b.display_text(), " " * 16)
        self.assertEqual(b.violations(), [])

    def test_font_page(self):
        b, _ = phys_bench(scenario="font", seed=5)
        b.run_to("done")
        self.assertEqual(b.display_text(), "0123456789AbCdEF")
        self.assertEqual(b.violations(), [])

    def test_phi2_buffer_variant_keeps_fault_latch_working(self):
        b, _ = phys_bench(layout=LY.generate(phi2_fault_buffer=True), scenario="res_fault", seed=6)
        b.run_to("done")
        self.assertEqual((b.net("FAULT"), b.net("RESULT_LOCK")), (1, 1))
        self.assertEqual(b.ram.mem[0x0281], 0x40)
        self.assertEqual(b.violations(), [])


class TestBuildMistakes(unittest.TestCase):
    """Typical breadboard errors must be caught by the connectivity check, and
    where they matter functionally, by the simulation."""

    def diag(self, fault):
        b, con = phys_bench(faults=[fault], image=specdoc.diagnostic_rom(),
                            symbols={"loop": 0x800A}, seed=3)
        b.h.set_switch_byte(0x5A)
        try:
            b.run_to("loop", max_cycles=300)
            b.run_cycles(60)
        except TimeoutError:
            pass
        return b, con

    def test_wire_one_row_off_on_address_line(self):
        b, con = self.diag(("wire_row", "A5", 0, 1))
        self.assertIn(["A4", "A5"], con["shorts"])
        self.assertGreater(len(b.violations()), 0)

    def test_ic_seated_one_row_low(self):
        b, con = self.diag(("ic_shift", "U13", 1))
        self.assertTrue(con["shorts"])
        self.assertGreater(len(b.violations()), 0)

    def test_missing_rail_bridge(self):
        b, con = phys_bench(faults=[("drop_wire", "BB3", "R+")], scenario="idle")
        self.assertIn(("VCC", 2), con["opens"])
        errors, _ = checks.erc(b.sim)
        self.assertTrue(any("supply pin on VCC~1" in e for e in errors))

    def test_pullup_moved_to_neighbour_is_latent_but_caught(self):
        # P_D3's pull-up strip jumpered to P_D4: normal reads still work, the
        # connectivity check flags it, unmapped reads float bit 3.
        b, con = phys_bench(faults=[("wire_row", "P_D3", 0, 1)], scenario="mirrors", seed=3)
        self.assertTrue(con["shorts"])
        b.run_to("done")
        # the floating bit reads as an arbitrary value; the sample itself is flagged
        bad = [v for v in b.violations() if v.kind == "invalid-sample"]
        self.assertTrue(any("7F05" in v.where or "7F0D" in v.where or "7F02" in v.where
                            for v in bad), bad[:3])


if __name__ == "__main__":
    unittest.main()
