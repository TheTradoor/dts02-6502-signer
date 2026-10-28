"""Power dip (supervisor reset) during an SRAM write strobe."""

import unittest

from sim import corners
from sim.brownout import dip_during_write


class TestBrownout(unittest.TestCase):
    def check_recovery(self, r):
        self.assertEqual(r["write_during_reset"], [])          # no strobe starts while held
        self.assertEqual(tuple(r["after"].values()), (0, 0, 0))  # locks / fault cleared
        # after recovery the fresh boot ran with no further violations
        self.assertEqual(r["violations_total"] - len(r["violations_at_dip"]), 0)

    def test_dip_random_delays(self):
        # Any seed: recovery is clean, and the only effects at the dip are the
        # F12 kinds (the in-flight write or the next address), all within a few
        # hundred nanoseconds of the supervisor asserting reset.
        allowed = {"addr-during-write", "invalid-sample", "setup", "hold", "pulse-width"}
        for seed in (2, 3):
            with self.subTest(seed=seed):
                r = dip_during_write(40, seed=seed)
                self.check_recovery(r)
                t_dip = r["t_we"] + 40
                for v in r["violations_at_dip"]:
                    self.assertIn(v.kind, allowed)
                    self.assertLess(v.time - t_dip, 500)

    def test_dip_can_disturb_next_address_at_corner(self):
        # F12: the cold clear drops PHI2 before the SRAM write strobe ends, so
        # the next cycle's address can be written with indeterminate data.
        r = dip_during_write(40, seed=2, corner=corners.brownout_write_corner)
        self.check_recovery(r)
        kinds = {v.kind for v in r["violations_at_dip"]}
        self.assertIn("addr-during-write", kinds)


if __name__ == "__main__":
    unittest.main()
