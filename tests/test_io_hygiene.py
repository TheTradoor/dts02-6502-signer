"""Firmware must store only to the canonical I/O ports $7F02-$7F04: A3-A7 are
not decoded, so any other $7Fxx store aliases a port (FINDINGS F8), and a
stray alias of $7F03/$7F04 would freeze a session.  (Idea from PR #9.)"""

import hashlib
import unittest

from sim import ed25519_ref as E, signer_fw as F, solana as SOL
from sim.emu import Machine
from sim.firmware import SCENARIOS, boot_rom
from sim.signer_harness import Fw

CANONICAL_WRITES = {0x7F02, 0x7F03, 0x7F04}
SEED = hashlib.sha256(b"DTS-02 public test fixture seed - never fund").digest()


class TestIoHygiene(unittest.TestCase):
    def test_signing_writes_only_canonical_ports(self):
        pk = E.public_key(SEED)
        msg = SOL.transfer_message(pk, E.public_key(b"\x03" * 32), 5000, bytes(32))
        for name, env in [("valid", F.envelope(msg, pk)),
                          ("wrong key", F.envelope(msg, E.public_key(b"\x01" * 32))),
                          ("bad crc", F.envelope(msg, pk)[:-1] + b"\x00")]:
            with self.subTest(name):
                fw = Fw(double_sign=False, verify=False)
                fw.put(F.SEED, SEED)
                fw.put(F.ENV, env)
                fw.m.clear_io_log()
                fw.call("sign_main")
                written = fw.m.io_addresses_written()
                self.assertLessEqual(written, CANONICAL_WRITES)
                if name != "bad crc":                     # rejected before anything is frozen
                    self.assertIn(0x7F03, written)

    def test_test_firmware_scenarios_write_only_canonical_ports(self):
        img, syms = boot_rom()
        for name, num in SCENARIOS.items():
            if name in ("mirrors", "keypad"):       # mirrors probes $7F0B on purpose; keypad never ends
                continue
            with self.subTest(name):
                m = Machine(rom=img)
                m.m.s = 0xFD
                m.load(0x3F80, bytes([num]))
                m.reset()
                m.run(stop=syms["done"], max_cycles=20_000_000)
                allowed = CANONICAL_WRITES | ({0x7F05} if name == "res_fault" else set())
                self.assertLessEqual(m.io_addresses_written(), allowed)   # res_fault probes $7F05

    def test_mirror_store_is_detected(self):
        rom = bytearray(32768)
        rom[0:6] = bytes([0xA9, 0x01, 0x8D, 0x0B, 0x7F, 0xDB])   # LDA #1; STA $7F0B; STP
        rom[0x7FFC:0x7FFE] = b"\x00\x80"
        m = Machine(rom=bytes(rom))
        m.reset()
        self.assertEqual(m.run(max_cycles=100), Machine.STP)
        self.assertEqual(m.io_addresses_written(), {0x7F0B})
        self.assertEqual(m.m.msg_lock, 1)                         # the alias really locks


if __name__ == "__main__":
    unittest.main()
