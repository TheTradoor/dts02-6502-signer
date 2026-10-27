# DTS-02: 65C02 Ed25519 desktop signer, with a simulation of the design

`spec.md` is the DTS-02 revision 0.3 breadboard specification. DTS-02 is a small 65C02 computer meant to be a third, offline Ed25519 signer in a 2-of-3 Squads multisig. It is built from:

- a W65C02S CPU
- a 32 KiB SRAM and a 32 KiB OTP ROM
- 74HC/HCT glue logic
- DIP-switch and pushbutton input
- sixteen raw seven-segment digits, each driven statically by its own HC595
- hardware message and result bank locks, plus a fault latch

The spec covers the circuit, the breadboard layout, the bring-up plan and the firmware contract. It does not include signing firmware.

The `sim/` directory is a pure-Python (standard library only) simulation used to verify the spec. `FINDINGS.md` reports what the verification found.

## What the simulation models

- **Every IC at pin level.** `sim/board.py` copies the spec's DIP pin tables, so a wrong pin number in the spec becomes a wrong connection in the simulator. Models exist for HCT74, HCT541, HCT573, HCT138, HCT157, HCT273, HCT244, HC595, the gate packages, the AS6C62256 SRAM, the AT27C256R EPROM and the SC56-11 digit.
- **Event-driven timing with three-state nets.** Signals take the values 0/1/X/Z. Each part instance gets a fixed propagation delay drawn from datasheet ranges (`sim/timing.py`), so different seeds give a Monte Carlo spread of path skews.
- **Checks that run automatically:**
  - bus contention (two drivers enabled on one net)
  - setup and hold at every flip-flop, latch, SRAM write and CPU read
  - invalid or floating samples
  - write-pulse width
  - address changes during a write
  - RESB held for fewer than two clocks
- **Pull-ups as an RC.** A released bus line stays indeterminate until it crosses the worst receiver threshold.
- **The CPU.** A cycle-accurate W65C02S bus model, including dummy reads and reset sequence, with tADS, tMDS, tDHW, tDSR and tDHR timing.
- **Firmware.**
  - The spec's own Section 11.3 diagnostic ROM, taken directly from the Python block in `spec.md`.
  - A test ROM (`sim/firmware.py`, built by `sim/asm.py`) that implements the spec's Section 10.1 boot and reset state machine and a display driver. On a fresh start it runs the test scenario selected by the DIP switches.
- **Static checks.** An electrical rule check of the netlist, a re-check of the Section 3.3 placement table, BOM quantities against the netlist, a closed-form analog analysis (`sim/analog.py`), and an independent RFC 8032 implementation (`sim/ed25519_ref.py`).

Some parts are behavioral stand-ins: the RC oscillator, the MCP130 supervisor, the reset RC, the switches and the keys. The simulation is digital. It does not cover ringing, ground bounce, edge rates or supply droop, which still need the oscilloscope checks in Section 11.2 of the spec.

## Running it

Requires Python 3.9 or later and nothing else.

```sh
python3 -m sim check                        # static, document and analog checks (seconds)
python3 -m sim diag --switch 0xA5           # run the spec's diagnostic ROM on the board model
python3 -m sim run font                     # boot the test ROM; scenario chosen via the DIP switches
python3 -m sim run msg_fault --reset        # lock, fault, then press CPU RESET
python3 -m sim handoff                      # Section 11.2 bus-handoff measurements
python3 -m sim race                         # worst-case read-window corner: rev 0.3 vs proposed fix
python3 -m unittest discover -s tests -v    # full test suite: 48 tests, about 3.5 minutes
```

Scenarios for `run`: `idle`, `ram`, `msg_fault`, `res_fault`, `msg_frozen`, `record_ok`, `record_bad`, `mirrors`, `font`.

Options shared by `run`, `diag`, `handoff` and `race`:
- `--freq KHZ`: master clock, default 380
- `--seed N`: delay Monte Carlo draw
- `--six-state`: apply the read-window fix proposed in `FINDINGS.md`
- `--vcd out.vcd`: write a waveform you can open in GTKWave

Throughput is about 1,500 simulated CPU cycles per second.

## Layout

```
spec.md            the design under test (rev 0.3, verbatim)
FINDINGS.md        verification results and recommended changes
sim/engine.py      event kernel, nets, contention and timing checks
sim/parts.py       pin-level IC models
sim/cpu65c02.py    W65C02S core and bus-timing wrapper
sim/board.py       netlist transcribed from spec.md, plus fault-injection variants
sim/firmware.py    boot/test ROM source (Section 10.1 state machine, display driver, CRC-32)
sim/asm.py         small two-pass 65C02 assembler
sim/analog.py      oscillator, reset RC, pull-up, power and display arithmetic
sim/checks.py      ERC, placement, BOM, waveform measurement helpers
sim/handoff.py     Section 11.2 measurements
sim/corners.py     forced worst-case delay corners
tests/             unittest suite
```
