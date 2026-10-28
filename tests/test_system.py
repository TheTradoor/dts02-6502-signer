"""Section 11.4 lock / reset-recovery rows, display chain, power-up reset."""

import unittest

from sim.firmware import FONT
from sim.runner import Bench


def status(b):
    return (b.net("FAULT"), b.net("RESULT_LOCK"), b.net("MESSAGE_LOCK"))


class TestPowerUp(unittest.TestCase):
    def test_cold_clear_then_cpu_reset_with_clocks(self):
        b = Bench(scenario="idle", seed=21)
        b.watch_edges("COLD_CLEAR_N", "CPU_RESET_N", "PHI2")
        b.run_cycles(80)
        cc = b.rising("COLD_CLEAR_N")[-1]
        cr = b.rising("CPU_RESET_N")[-1]
        self.assertLess(cc, cr)
        clocks = [t for t in b.rising("PHI2") if cc < t < cr]
        self.assertGreaterEqual(len(clocks), 2)
        self.assertEqual(status(b), (0, 0, 0))
        self.assertEqual(b.violations(), [])

    def test_display_blank_and_gpio_clear_after_reset(self):
        b = Bench(scenario="idle", seed=22)
        b.run_cycles(20)
        self.assertEqual(b.net("DISPLAY_ENABLE"), 0)
        self.assertEqual(b.net("DISPLAY_OE_N"), 1)


class TestLocks(unittest.TestCase):
    def test_ram_banks_before_locking(self):
        b = Bench(scenario="ram", seed=23)
        b.run_to("done")
        self.assertEqual(b.ram.mem[0x0280], 0, "firmware-reported RAM errors")
        from sim.firmware import RAM_PROBES
        for i, a in enumerate(RAM_PROBES):
            want = 0x80 if a == 0x0300 else (2 * i) ^ 0xA5   # $0300 is reused by the walking-data test
            self.assertEqual(b.ram.mem[a], want, hex(a))
        self.assertEqual(b.violations(), [])

    def test_message_lock_blocks_and_faults(self):
        b = Bench(scenario="msg_fault", seed=24)
        b.watch_edges("RAM_WE_N")
        b.run_to("done")
        m = b.ram.mem
        self.assertEqual((m[0x4000], m[0x5FFF]), (0x11, 0x12))     # old data remains
        self.assertEqual(m[0x6000], 0x13)                           # FAULT blocks all writes
        self.assertEqual(m[0x0300], 0x00)                           # scratch write blocked
        self.assertEqual(status(b), (1, 0, 1))
        self.assertEqual(b.cpu.core.a & 0xE0, 0xA0)                 # $7F01 reports it
        self.assertEqual(b.net("DISPLAY_ENABLE"), 1)                # firmware enabled it...
        self.assertEqual(b.display_text(), " " * 16)                # ...hardware blanks it
        self.assertEqual(b.net("DISP_V"), 0)                        # by removing display power
        self.assertEqual(b.violations(), [])
        # CPU RESET: locks survive, boot enters ROM-only hold, RAM_WE_N stays high
        t = b.sim.now
        b.press_reset()
        b.run_to("hold", max_cycles=2000)
        b.run_cycles(100)
        self.assertEqual(status(b), (1, 0, 1))
        self.assertEqual(b.falling("RAM_WE_N", t), [])
        self.assertEqual(b.violations(), [])

    def test_result_lock(self):
        b = Bench(scenario="res_fault", seed=25)
        b.run_to("done")
        m = b.ram.mem
        self.assertEqual((m[0x4000], m[0x5FFF]), (0x21, 0x22))     # message bank still open
        self.assertEqual(m[0x0281], 0x40)                           # I/O writes did not fault
        self.assertNotEqual(m[0x6000], 0x23)
        self.assertNotEqual(m[0x7EFF], 0x24)
        self.assertEqual(status(b), (1, 1, 0))
        self.assertEqual(b.violations(), [])

    def test_mirrors_and_undriven_ports(self):
        b = Bench(scenario="mirrors", seed=26)
        b.run_to("done")
        m = b.ram.mem
        self.assertEqual(m[0x282], m[0x283])                        # $7F08 mirrors $7F00
        self.assertEqual(m[0x283], 0xFF)                            # no key pressed
        self.assertEqual([m[0x284], m[0x285], m[0x286]], [0xFF] * 3)
        self.assertEqual(m[0x287] & 0xE0, 0x20)                     # $7F0B set MESSAGE_LOCK
        self.assertEqual(b.violations(), [])


class TestRecovery(unittest.TestCase):
    def reset_into(self, scenario, label, seed):
        b = Bench(scenario=scenario, seed=seed)
        b.run_to("done")
        t = b.sim.now
        n = len(b.ram.writes)
        b.press_reset()
        b.run_to(label, max_cycles=60_000)
        high = [(a, v) for _, a, v in b.ram.writes[n:] if a is None or a >= 0x4000]
        return b, t, high

    def test_message_frozen_reset_shows_c0(self):
        b, _, high = self.reset_into("msg_frozen", "idle", 31)
        self.assertEqual(b.display_text(), "C0" + " " * 14)
        self.assertEqual(high, [])
        self.assertEqual(status(b), (0, 0, 1))
        self.assertEqual(b.violations(), [])

    def test_valid_record_reset_shows_c2(self):
        b, _, high = self.reset_into("record_ok", "idle", 32)
        self.assertEqual(b.display_text(), "C2" + " " * 14)
        self.assertEqual(high, [])
        self.assertEqual(status(b), (0, 1, 0))

    def test_corrupt_record_reset_shows_c1(self):
        b, _, high = self.reset_into("record_bad", "idle", 33)
        self.assertEqual(b.display_text(), "C1" + " " * 14)
        self.assertEqual(high, [])

    def test_cold_clear_restores_fresh_state(self):
        b = Bench(scenario="msg_fault", seed=34)
        b.run_to("done")
        self.assertEqual(status(b), (1, 0, 1))
        b.cold_clear()
        b.set_scenario("idle")
        b.run_to("done")
        self.assertEqual(status(b), (0, 0, 0))
        self.assertEqual(b.violations(), [])


class TestDisplay(unittest.TestCase):
    def test_font_page_through_max7219_chain_and_pinout(self):
        b = Bench(scenario="font", seed=41)
        b.run_to("done")
        self.assertEqual(b.h.display(), FONT)
        self.assertEqual(b.display_text(), "0123456789AbCdEF")
        self.assertEqual(b.net("DISP_V"), 1)
        self.assertEqual(b.violations(), [])

    def test_display_unpowered_until_enabled_and_after_reset(self):
        b = Bench(scenario="font", seed=42)
        b.run_cycles(20)
        self.assertEqual((b.net("DISPLAY_ON"), b.net("DISP_V")), (0, 0))
        self.assertEqual(b.display_text(), " " * 16)
        b.run_to("done")
        self.assertEqual(b.display_text(), "0123456789AbCdEF")
        b.h.press_reset(True)
        b.run_cycles(30)
        self.assertEqual(b.net("DISP_V"), 0)                       # reset removes display power
        self.assertEqual(b.display_text(), " " * 16)
        self.assertTrue(all(c.powered is False for c in b.sim.parts.values() if c.kind == "MAX7219"))


class TestKeypad(unittest.TestCase):
    def test_hex_entry_and_multi_key_rejection(self):
        b = Bench(scenario="keypad", seed=9)
        b.run_to("kp_loop")

        def tap(*labels):
            b.h.press_keypad(*labels)
            b.run_cycles(2500)
            b.h.press_keypad()
            b.run_cycles(16000)                 # release + full page redraw

        def buf():
            return "".join("0123456789ABCDEF "[b.ram.mem[0x30 + i]] for i in range(16))
        for k in "1A*#0":
            tap(k)
        self.assertEqual(buf().strip(), "1AEF0")                    # * = E, # = F
        self.assertEqual(b.display_text().strip(), "1AEF0")
        tap("1", "2")                                               # two keys: rejected
        tap("1", "2", "4")                                          # L-shape ghosts key 5: rejected
        self.assertEqual(buf().strip(), "1AEF0")
        tap("D")
        self.assertEqual(b.display_text().strip(), "1AEF0d")
        self.assertEqual(b.violations(), [])


if __name__ == "__main__":
    unittest.main()
