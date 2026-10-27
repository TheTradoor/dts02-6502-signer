"""Brownout / power-dip injection.

A supply dip below the MCP130 trip point pulls POR_RAW_N low, which clears
the phase generator, the cycle register, the locks and the fault latch
asynchronously and holds the CPU in reset.  This module injects that event at
a chosen offset into an SRAM write strobe (the worst case) and reports what
the board did.  It models the digital consequences only: the analog region
between the logic's 4.5 V minimum and the supervisor trip (4.35-4.60 V) is
outside a digital simulation (spec Section 5.2).
"""

from .runner import Bench


def dip_during_write(offset_ns, seed=1, low_ns=20_000, scenario="ram", nth_write=3,
                     mutations=(), corner=None, cap_pf=None):
    """Run `scenario`, pull POR low `offset_ns` after the nth SRAM write strobe
    begins, release it after low_ns, and let the firmware reboot."""
    b = Bench(scenario=scenario, seed=seed, mutations=list(mutations), cap_pf=cap_pf)
    if corner:
        corner(b)
    b.watch_edges("RAM_WE_N", "COLD_CLEAR_N", "CPU_RESET_N", "FAULT", "MESSAGE_LOCK")
    ram = b.ram
    b.run_to("done")                       # first, complete session (writes + locks if any)
    t_first_done = b.sim.now
    # second session up to the nth write strobe of the reboot
    b.cold_clear()
    b.h.set_switch_byte(~{"ram": 1, "msg_frozen": 4}[scenario] & 0xFF)
    start = b.sim.now
    target = []
    mixed = []          # WE strobes of writes whose data has both 0 and 1 bits

    def on_we(net, t, old, new):
        req = b.cpu.req
        if new == 0 and old == 1 and req[0] == "W" and req[2] not in (0x00, 0xFF) and t > start:
            mixed.append((t, req[1], req[2]))
    b.sim.nets["RAM_WE_N"].watchers.append(on_we)

    def stop():
        if len(mixed) >= nth_write:
            target.append(mixed[nth_write - 1][0])
            return True
        return False

    b.sim.run_until(b.sim.now + 400_000 * b.cpu_period, stop=stop)
    t_we = target[0]
    writes_before = len(ram.writes)
    viol_before = len(b.sim.violations)
    t_dip = t_we + offset_ns
    b.sim.run_until(t_dip)
    b.h.cold_clear(low_ns)
    t_release = t_dip + low_ns
    b.sim.run_until(t_release)
    # nothing may be written while cold clear is asserted
    cc_low = [(t, n) for t, o, n in b.edge_log["COLD_CLEAR_N"] if t >= t_dip]
    cc_fall = next((t for t, n in cc_low if n == 0), t_dip)
    we_during = [t for t in b.falling("RAM_WE_N", cc_fall + 1, t_release)]
    hit = [w for w in ram.writes[writes_before:] if w[0] >= t_we]
    dip_viol = b.sim.violations[viol_before:]
    b.run_to("done")
    return dict(bench=b, offset_ns=offset_ns, t_we=t_we, target=mixed[nth_write - 1], write_during_reset=we_during,
                interrupted_write=hit[:1], violations_at_dip=dip_viol,
                after=dict(fault=b.net("FAULT"), msg=b.net("MESSAGE_LOCK"),
                           res=b.net("RESULT_LOCK")),
                violations_total=len(b.sim.violations), first_done=t_first_done)
