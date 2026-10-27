#!/bin/sh
# Fetch Klaus Dormann's 6502/65C02 functional test ROMs (GPL-3.0) used by
# tests/test_emu.py.  They are not vendored in this repository.
set -e
cd "$(dirname "$0")/.."
rm -rf third_party/klaus
git clone -q --depth 1 https://github.com/Klaus2m5/6502_65C02_functional_tests third_party/klaus
echo "fetched into third_party/klaus"
