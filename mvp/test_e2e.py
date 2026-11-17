"""端到端回路的测试：无人工步骤走完 H1 → 判红 → 维护。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mvp.e2e import run_e2e


class TestE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "e2e"
        self.result = run_e2e(self.dir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_artifacts_produced(self) -> None:
        self.assertGreater(self.result["traces"], 0)
        self.assertTrue((self.dir / "traces.jsonl").exists())
        self.assertEqual(self.result["exported"], self.result["tasks"])

    def test_h1_computed_from_own_traces(self) -> None:
        """合成域只有报销一个任务族，H1 必须判不成立——门要会拒绝，而不是永远放行。"""
        h1 = self.result["h1"]
        self.assertEqual(h1["n_items"], self.result["tasks"])
        self.assertFalse(h1["h1_ok"], h1["verdict"])
        self.assertIn("1 个簇", h1["verdict"])

    def test_c_path_passes_gate(self) -> None:
        self.assertTrue(self.result["gates"]["C"]["passed"],
                        self.result["gates"]["C"]["reasons"])

    def test_maintenance_runs_without_human(self) -> None:
        report = self.result["maintenance"]
        self.assertEqual(report.promoted, [])       # 这份目录还没 usage，不该凭空晋升
        self.assertEqual(report.retired, [])

    def test_overall_gate_rejects_single_family(self) -> None:
        self.assertFalse(self.result["overall"])
        self.assertTrue(self.result["gates"]["C"]["passed"])   # C 本身是过门的


if __name__ == "__main__":
    unittest.main()