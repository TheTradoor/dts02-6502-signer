"""Layout measurements and build documents must retain their board geometry."""

from pathlib import Path
import tempfile
import unittest

from sim import layout as LY


def measurements(layout):
    _, pullups, _, cap = LY.physical_netlist(layout)
    return dict(wires=LY.wire_report(layout), pullups=pullups, capacitance=cap,
                harness=LY.harness_checks(layout))


def documents(layout):
    with tempfile.TemporaryDirectory() as outdir:
        LY.write_docs(layout, outdir)
        return {name: (Path(outdir) / name).read_bytes()
                for name in ("wiring.csv", "WIRING.md")}


class TestLayoutGeometry(unittest.TestCase):
    def setUp(self):
        self.custom_grid = dict(LY.GRID, BB10=(6, 0))

    def test_reports_retain_grid_after_generating_another_layout(self):
        for first, second in [(None, self.custom_grid), (self.custom_grid, None)]:
            layout = LY.generate(grid=first)
            before = measurements(layout)
            other = LY.generate(grid=second)
            self.assertNotEqual(before["capacitance"], measurements(other)["capacitance"])
            after = measurements(layout)
            for name in before:
                with self.subTest(custom_first=first is not None, report=name):
                    self.assertEqual(before[name], after[name])

    def test_documents_retain_grid_after_generating_another_layout(self):
        for first, second in [(None, self.custom_grid), (self.custom_grid, None)]:
            layout = LY.generate(grid=first)
            before = documents(layout)
            LY.generate(grid=second)
            after = documents(layout)
            for name in before:
                with self.subTest(custom_first=first is not None, document=name):
                    self.assertEqual(before[name], after[name])

    def test_caller_grid_changes_do_not_affect_layout(self):
        layout = LY.generate(grid=self.custom_grid)
        before = measurements(layout)
        docs_before = documents(layout)
        self.custom_grid["BB10"] = LY.GRID["BB10"]
        self.assertEqual(before, measurements(layout))
        self.assertEqual(docs_before, documents(layout))


if __name__ == "__main__":
    unittest.main()
