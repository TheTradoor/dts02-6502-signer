"""Helpers that read facts straight out of spec.md so checks track the doc."""

import hashlib
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(ROOT, "spec.md")


def spec_text():
    with open(SPEC, encoding="utf-8") as f:
        return f.read()


def diagnostic_generator_source():
    """The Python block in Section 11.3."""
    text = spec_text()
    sec = text[text.index("### 11.3"):]
    m = re.search(r"```python\n(.*?)```", sec, re.S)
    return m.group(1)


def diagnostic_rom():
    """Execute the spec's generator in memory (no file written)."""
    src = diagnostic_generator_source()
    src = src.replace('output.write_bytes(rom)', 'pass')
    src = src.replace('print(output.name, len(rom), hashlib.sha256(rom).hexdigest())', 'pass')
    ns = {}
    exec(compile(src, "spec.md:11.3", "exec"), ns)
    return bytes(ns["rom"])


def expected_diagnostic_hash():
    m = re.search(r"Expected SHA-256 of this exact 32,768-byte diagnostic image: ([0-9a-f]{64})",
                  spec_text())
    return m.group(1)


def sha256(b):
    return hashlib.sha256(b).hexdigest()
