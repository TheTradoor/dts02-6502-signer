"""Minimal two-pass 65C02 assembler for the test firmware.

Syntax: one statement per line, `label:` prefixes, `;` comments,
`.org $8000`, `.byte a, b, ...`, `.word x`, `NAME = expr`.
Operands: #imm, zp/abs (an explicit 2-hex-digit `$xx` literal selects zero
page), abs,X  abs,Y  (zp),Y, A (accumulator).  Expressions: $hex, %bin,
decimal, 'c', labels/constants, + and -, <expr (low byte), >expr (high byte).
Byte and word values must fit their unsigned encoded width; use <expr or
>expr to explicitly select a byte.
"""

import re

OPS = {
    "LDA": {"imm": 0xA9, "zp": 0xA5, "abs": 0xAD, "absx": 0xBD, "absy": 0xB9, "indy": 0xB1},
    "LDX": {"imm": 0xA2, "zp": 0xA6, "abs": 0xAE},
    "LDY": {"imm": 0xA0, "zp": 0xA4, "abs": 0xAC},
    "STA": {"zp": 0x85, "abs": 0x8D, "absx": 0x9D, "absy": 0x99, "indy": 0x91},
    "STX": {"zp": 0x86, "abs": 0x8E},
    "STY": {"zp": 0x84, "abs": 0x8C},
    "STZ": {"zp": 0x64, "abs": 0x9C},
    "CMP": {"imm": 0xC9, "zp": 0xC5, "abs": 0xCD, "absx": 0xDD, "absy": 0xD9},
    "CPX": {"imm": 0xE0},
    "CPY": {"imm": 0xC0},
    "AND": {"imm": 0x29, "abs": 0x2D},
    "ORA": {"imm": 0x09, "abs": 0x0D},
    "EOR": {"imm": 0x49, "abs": 0x4D, "absx": 0x5D},
    "ADC": {"imm": 0x69},
    "SBC": {"imm": 0xE9},
    "BIT": {"abs": 0x2C},
    "INC": {"zp": 0xE6},
    "DEC": {"zp": 0xC6},
    "JMP": {"abs": 0x4C},
    "JSR": {"abs": 0x20},
}
IMPLIED = {"INX": 0xE8, "INY": 0xC8, "DEX": 0xCA, "DEY": 0x88, "TAX": 0xAA,
           "TXA": 0x8A, "TAY": 0xA8, "TYA": 0x98, "TXS": 0x9A, "TSX": 0xBA,
           "SEI": 0x78, "CLI": 0x58, "CLD": 0xD8, "SEC": 0x38, "CLC": 0x18,
           "NOP": 0xEA, "RTS": 0x60, "PHA": 0x48, "PLA": 0x68, "PHP": 0x08,
           "PLP": 0x28, "BRK": 0x00, "RTI": 0x40}
ACCUM = {"ASL": 0x0A, "LSR": 0x4A, "ROL": 0x2A, "ROR": 0x6A}
BRANCH = {"BPL": 0x10, "BMI": 0x30, "BVC": 0x50, "BVS": 0x70, "BCC": 0x90,
          "BCS": 0xB0, "BNE": 0xD0, "BEQ": 0xF0, "BRA": 0x80}
SIZE = {"imm": 2, "zp": 2, "abs": 3, "absx": 3, "absy": 3, "indy": 2}


class AsmError(Exception):
    pass


class Assembler:
    def __init__(self, source, org=0x8000, size=0x8000, fill=0xEA, symbols=None):
        self.source = source
        self.base = org
        self.size = size
        self.fill = fill
        self.symbols = dict(symbols or {})

    def eval(self, expr, final):
        expr = expr.strip()
        if expr.startswith("<"):
            return self.eval(expr[1:], final) & 0xFF
        if expr.startswith(">"):
            return (self.eval(expr[1:], final) >> 8) & 0xFF
        terms = re.split(r"(?<=[^+\-])\s*([+\-])\s*", expr)
        total, sign = 0, 1
        for tok in terms:
            if tok == "+":
                sign = 1
                continue
            if tok == "-":
                sign = -1
                continue
            total += sign * self.atom(tok.strip(), final)
        return total

    def atom(self, tok, final):
        if tok.startswith("$"):
            return int(tok[1:], 16)
        if tok.startswith("%"):
            return int(tok[1:], 2)
        if tok.startswith("'") and tok.endswith("'") and len(tok) == 3:
            return ord(tok[1])
        if tok.isdigit():
            return int(tok)
        if tok in self.symbols:
            return self.symbols[tok]
        if final:
            raise AsmError(f"undefined symbol {tok!r}")
        return 0x8000

    def mode(self, mnem, arg, final):
        arg = arg.strip()
        if arg.startswith("#"):
            return "imm", self.eval(arg[1:], final)
        m = re.fullmatch(r"\((.+)\)\s*,\s*[yY]", arg)
        if m:
            return "indy", self.eval(m.group(1), final)
        m = re.fullmatch(r"(.+?)\s*,\s*([xXyY])", arg)
        if m:
            return ("absx" if m.group(2) in "xX" else "absy"), self.eval(m.group(1), final)
        v = self.eval(arg, final)
        if re.fullmatch(r"\$[0-9A-Fa-f]{2}", arg) and "zp" in OPS[mnem]:
            return "zp", v
        if "abs" not in OPS[mnem] and "zp" in OPS[mnem]:
            return "zp", v
        return "abs", v

    def assemble(self):
        for final in (False, True):
            out = bytearray([self.fill] * self.size)
            pc = self.base
            for lineno, raw in enumerate(self.source.splitlines(), 1):
                line = raw.split(";", 1)[0].strip()
                try:
                    pc = self._line(line, pc, out, final)
                except AsmError as e:
                    raise AsmError(f"line {lineno}: {raw.strip()}: {e}") from None
        self.image = out
        return out

    @staticmethod
    def _check_range(value, bits, what):
        maximum = (1 << bits) - 1
        if not 0 <= value <= maximum:
            raise AsmError(f"{what} out of range ({value}; expected 0..{maximum})")

    def _put(self, out, pc, b):
        off = pc - self.base
        if not 0 <= off < self.size:
            raise AsmError(f"address ${pc:04X} outside image")
        out[off] = b & 0xFF

    def _line(self, line, pc, out, final):
        while True:
            m = re.match(r"([A-Za-z_]\w*):\s*(.*)", line)
            if not m:
                break
            if not final and m.group(1) in self.symbols:
                raise AsmError(f"duplicate label {m.group(1)}")
            self.symbols[m.group(1)] = pc
            line = m.group(2)
        if not line:
            return pc
        m = re.fullmatch(r"([A-Za-z_]\w*)\s*=\s*(.+)", line)
        if m:
            self.symbols[m.group(1)] = self.eval(m.group(2), final)
            return pc
        parts = line.split(None, 1)
        mnem = parts[0].upper()
        arg = parts[1] if len(parts) > 1 else ""
        if mnem == ".ORG":
            return self.eval(arg, True)
        if mnem in (".BYTE", ".WORD"):
            for item in self._split_args(arg):
                v = self.eval(item, final)
                if final:
                    self._check_range(v, 8 if mnem == ".BYTE" else 16, mnem)
                if mnem == ".BYTE":
                    self._put(out, pc, v)
                    pc += 1
                else:
                    self._put(out, pc, v)
                    self._put(out, pc + 1, v >> 8)
                    pc += 2
            return pc
        if mnem in IMPLIED:
            self._put(out, pc, IMPLIED[mnem])
            return pc + 1
        if mnem in ACCUM and arg.strip().upper() in ("", "A"):
            self._put(out, pc, ACCUM[mnem])
            return pc + 1
        if mnem in BRANCH:
            tgt = self.eval(arg, final)
            off = tgt - (pc + 2)
            if final and not -128 <= off <= 127:
                raise AsmError(f"branch out of range ({off})")
            self._put(out, pc, BRANCH[mnem])
            self._put(out, pc + 1, off & 0xFF)
            return pc + 2
        if mnem not in OPS:
            raise AsmError(f"unknown mnemonic {mnem}")
        mode, v = self.mode(mnem, arg, final)
        if mode not in OPS[mnem]:
            raise AsmError(f"{mnem} has no {mode} mode")
        if final:
            self._check_range(v, 8 * (SIZE[mode] - 1), f"{mode} operand")
        self._put(out, pc, OPS[mnem][mode])
        self._put(out, pc + 1, v)
        if SIZE[mode] == 3:
            self._put(out, pc + 2, v >> 8)
        return pc + SIZE[mode]

    @staticmethod
    def _split_args(arg):
        return [a for a in (x.strip() for x in arg.split(",")) if a]


def assemble(source, **kw):
    a = Assembler(source, **kw)
    img = a.assemble()
    return bytes(img), a.symbols
