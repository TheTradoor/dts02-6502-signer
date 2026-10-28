"""Convenience layer used by the tests and the CLI."""

import collections

from .board import build
from .firmware import SCENARIOS, boot_rom

_ROM_CACHE = {}


def rom():
    if "boot" not in _ROM_CACHE:
        _ROM_CACHE["boot"] = boot_rom()
    return _ROM_CACHE["boot"]


class Bench:
    """One simulated board plus helpers to drive and observe it."""

    def __init__(self, image=None, symbols=None, scenario=None, **kw):
        if image is None:
            image, symbols = rom()
        self.symbols = symbols or {}
        self.sim, self.h = build(image, **kw)
        self.cpu = self.sim.parts["U1"]
        self.ram = self.sim.parts["U2"]
        self.cpu_period = self.sim.master_per_cpu * 1e9 / kw.get("master_hz", 380e3)
        self.edge_log = collections.defaultdict(list)
        if scenario is not None:
            self.set_scenario(scenario)

    def set_scenario(self, name):
        """Test firmware reads its scenario number from the RAM mailbox $3F80."""
        self.ram.mem[0x3F80] = SCENARIOS[name]

    def set_columns(self, nibble):
        """Press row-1 keys so that, with every row driven low, port $7F00 reads
        $F0 | nibble (bit c clear = a key in column c).  Returns that byte."""
        labels = [self.h.keypad.LABELS[0][c] for c in range(4) if not (nibble >> c) & 1]
        self.h.press_keypad(*labels)
        return 0xF0 | (nibble & 0x0F)

    # -- observation -------------------------------------------------------
    def net(self, name):
        return self.sim.nets[name].value

    def watch_edges(self, *names):
        for n in names:
            log = self.edge_log[n]
            self.sim.nets[n].watchers.append(lambda net, t, old, new, log=log: log.append((t, old, new)))

    def rising(self, name, t0=0.0, t1=float("inf")):
        return [t for t, o, n in self.edge_log[name] if o != 1 and n == 1 and t0 <= t <= t1]

    def falling(self, name, t0=0.0, t1=float("inf")):
        return [t for t, o, n in self.edge_log[name] if o != 0 and n == 0 and t0 <= t <= t1]

    def violations(self):
        return list(self.sim.violations)

    def display_text(self):
        from .firmware import FONT
        inv = {m: "0123456789AbCdEF"[i] for i, m in enumerate(FONT)}
        inv[0] = " "
        out = []
        for m in self.h.display():
            out.append("?" if m is None else inv.get(m, "#"))
        return "".join(out)

    # -- running -------------------------------------------------------------
    def run_cycles(self, n):
        self.sim.run_until(self.sim.now + n * self.cpu_period)

    def run_to(self, label, max_cycles=200_000):
        addr = self.symbols[label] if isinstance(label, str) else label
        cpu = self.cpu
        t_end = self.sim.now + max_cycles * self.cpu_period
        hit = self.sim.run_until(t_end, stop=lambda: cpu.core.fetch_pc == addr and cpu.gen is not None)
        if not hit:
            raise TimeoutError(f"did not reach {label} (pc=${cpu.core.pc:04X}, "
                               f"halted={cpu.halted})")
        # let the current instruction's remaining cycles finish
        self.run_cycles(4)
        return self.sim.now

    def press_reset(self, hold_cycles=10):
        self.h.press_reset(True)
        self.run_cycles(hold_cycles)
        self.h.press_reset(False)

    def cold_clear(self):
        self.h.cold_clear()
        self.cpu.core.fetch_pc = None
