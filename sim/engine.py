"""Event-driven, pin-level digital simulator with tri-state nets.

Values on a net are 0, 1, X (driven conflict / unknown) or Z (nothing drives
the net and no pull-up has yet brought it to a valid level).  Every driver is
identified by (part, pin); a net records every interval where two or more
drivers are enabled at once, which is the bus-contention check the spec's
Section 6.5 argument is about.

Pull-ups are modelled as an RC: when the last driver releases a net that was
low, the net stays a valid 0 until it crosses VIL, is Z (indeterminate) until
it crosses VIH, and only then reads 1.  Thresholds are deliberately the worst
of the HCT/CMOS families on the bus so that "valid" means valid for every
receiver.
"""

import heapq
import itertools
import math

X = "X"
Z = "Z"

VDD = 5.0
VIL_WORST = 0.8   # lowest VIL of any receiver (HCT)
VIH_WORST = 3.5   # highest VIH of any receiver (CMOS 0.7*VDD)


def v_not(a):
    if a == 0:
        return 1
    if a == 1:
        return 0
    return X


def v_and(*vals):
    if any(v == 0 for v in vals):
        return 0
    if all(v == 1 for v in vals):
        return 1
    return X


def v_or(*vals):
    if any(v == 1 for v in vals):
        return 1
    if all(v == 0 for v in vals):
        return 0
    return X


def v_nand(*vals):
    return v_not(v_and(*vals))


class Violation:
    __slots__ = ("time", "kind", "where", "detail")

    def __init__(self, time, kind, where, detail):
        self.time, self.kind, self.where, self.detail = time, kind, where, detail

    def __repr__(self):
        return f"[{self.time/1000:10.3f} us] {self.kind:<14} {self.where}: {self.detail}"


class Net:
    __slots__ = ("name", "value", "drivers", "loads", "pullup_ohms", "cap_pf",
                 "version", "last_change", "watchers", "sim", "overlap_since",
                 "overlap_set", "supply", "edges")

    def __init__(self, sim, name):
        self.sim = sim
        self.name = name
        self.value = Z
        self.drivers = {}          # driver id -> value, enabled drivers only
        self.loads = []            # parts evaluated on change
        self.pullup_ohms = None
        self.cap_pf = None
        self.version = 0
        self.last_change = -1e18
        self.watchers = []         # callables(net, t, old, new)
        self.overlap_since = None
        self.overlap_set = None
        self.supply = None
        self.edges = 0

    @property
    def tau_ns(self):
        return self.pullup_ohms * self.cap_pf * 1e-3  # ohm * pF = ps -> ns

    def _set(self, t, value):
        if value == self.value:
            return
        old = self.value
        self.value = value
        self.last_change = t
        self.edges += 1
        sim = self.sim
        if sim.vcd is not None:
            sim.vcd.change(t, self, value)
        for w in self.watchers:
            w(self, t, old, value)
        for p in self.loads:
            sim.schedule_eval(p, t)

    def resolve(self, t):
        sim = self.sim
        d = self.drivers
        n = len(d)
        if self.name in sim.driver_watch:
            sim.driver_log.setdefault(self.name, []).append((t, tuple(sorted(k[0] for k in d))))
        if n >= 2:
            if self.overlap_since is None:
                self.overlap_since = t
                self.overlap_set = tuple(sorted(d))
                vals = set(d.values())
                kind = "contention" if len(vals) > 1 or X in vals else "overlap"
                sim.violation(t, kind, self.name, "drivers " + ", ".join(
                    f"{k}={v}" for k, v in sorted(d.items())))
        else:
            self.overlap_since = None
        if n == 0:
            self.version += 1
            if self.pullup_ohms is None:
                self._set(t, Z)
                return
            if self.value == 1:
                return
            # released from 0 / X / Z: RC rise toward VDD from (worst case) 0 V
            tau = self.tau_ns
            t_low = tau * math.log(VDD / (VDD - VIL_WORST))
            t_high = tau * math.log(VDD / (VDD - VIH_WORST))
            v = self.version
            if self.value == 0:
                sim.schedule(t + t_low, self._pull, v, Z)
            else:
                self._set(t, Z)
            sim.schedule(t + t_high, self._pull, v, 1)
            return
        self.version += 1
        vals = set(d.values())
        if len(vals) == 1:
            self._set(t, next(iter(vals)))
        else:
            self._set(t, X)

    def _pull(self, t, version, value):
        if version == self.version and not self.drivers:
            self._set(t, value)

    def is_valid(self):
        return self.value == 0 or self.value == 1


class Event:
    __slots__ = ("cancelled",)

    def __init__(self):
        self.cancelled = False


class Simulator:
    def __init__(self, rng):
        self.rng = rng
        self.nets = {}
        self.parts = {}
        self.queue = []
        self.seq = itertools.count()
        self.now = 0.0
        self.violations = []
        self.vcd = None
        self._pending_eval = {}
        self._drv_pending = {}     # driver id -> list of Event
        self.max_violations = 10000
        self.check_from = 0.0      # ignore power-up transients before this time
        self.driver_watch = set()  # nets whose driver sets are logged
        self.driver_log = {}
        self.pin_remap = None      # {(part, pin): net} from a physical layout

    # ---- construction -------------------------------------------------
    def net(self, name):
        n = self.nets.get(name)
        if n is None:
            n = Net(self, name)
            self.nets[name] = n
        return n

    def add_part(self, part):
        assert part.name not in self.parts, part.name
        self.parts[part.name] = part
        return part

    # ---- scheduling ---------------------------------------------------
    def schedule(self, t, fn, *args):
        ev = Event()
        heapq.heappush(self.queue, (t, next(self.seq), ev, fn, args))
        return ev

    def schedule_eval(self, part, t):
        # coalesce evaluations of the same part at the same instant
        key = (part.name, t)
        if key in self._pending_eval:
            return
        self._pending_eval[key] = True
        self.schedule(t, self._do_eval, part, key)

    def _do_eval(self, t, part, key):
        del self._pending_eval[key]
        part.evaluate(t)

    def drive(self, net, drv_id, value, t):
        """Schedule driver drv_id to drive `value` (0/1/X, or Z=release) at t.

        Inertial cancellation: a new transaction cancels pending transactions
        of the same driver scheduled at or after it."""
        pend = self._drv_pending.get(drv_id)
        now = self.now
        keep = []
        if pend:
            for item in pend:
                ev, et = item
                if et >= t:
                    ev.cancelled = True
                elif et >= now and not ev.cancelled:
                    keep.append(item)
        ev = self.schedule(t, self._apply_drive, net, drv_id, value)
        keep.append((ev, t))
        self._drv_pending[drv_id] = keep

    def _apply_drive(self, t, net, drv_id, value):
        d = net.drivers
        if value == Z:
            if drv_id not in d:
                return
            del d[drv_id]
        else:
            if d.get(drv_id) == value:
                return
            d[drv_id] = value
        net.resolve(t)

    def violation(self, t, kind, where, detail):
        if t < self.check_from:
            return
        if len(self.violations) < self.max_violations:
            self.violations.append(Violation(t, kind, where, detail))

    # ---- running ------------------------------------------------------
    def run_until(self, t_end=None, stop=None):
        q = self.queue
        while q:
            t, _, ev, fn, args = q[0]
            if t_end is not None and t > t_end:
                self.now = t_end
                return False
            heapq.heappop(q)
            if ev.cancelled:
                continue
            self.now = t
            fn(t, *args)
            if stop is not None and stop():
                return True
        return False

    # ---- timing-check helpers ----------------------------------------
    def check_sample(self, t, nets, setup, hold, where):
        """Check that each net is valid and unchanged in [t-setup, t+hold]."""
        for n in nets:
            if not n.is_valid():
                self.violation(t, "invalid-sample", where, f"{n.name}={n.value}")
            elif n.last_change > t - setup:
                self.violation(t, "setup", where,
                               f"{n.name} changed {t - n.last_change:.1f} ns before edge (need {setup})")
        if hold > 0:
            snap = [(n, n.last_change) for n in nets]
            self.schedule(t + hold, self._hold_check, snap, where, hold)

    def _hold_check(self, t, snap, where, hold):
        for n, lc in snap:
            if n.last_change != lc:
                self.violation(t, "hold", where,
                               f"{n.name} changed within {hold} ns after edge")
