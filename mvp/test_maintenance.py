"""维护回路的测试：四个动作按序执行、结果正确、可重复执行。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mvp.maintenance import build_fixture, run_maintenance
from mvp.persistence import RunStore
from mvp.snapshot import SnapshotStore


class TestMaintenance(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "run"
        self.sink, self.snaps = build_fixture(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_first_run_promotes_and_retires(self) -> None:
        report = run_maintenance(self.sink, self.snaps)
        self.assertEqual(report.promoted, ["C"])
        self.assertEqual(report.retired, ["B"])
        self.assertTrue(report.snapshot, "晋升必须提交新快照")

    def test_alarms_reported_before_promotion(self) -> None:
        report = run_maintenance(self.sink, self.snaps)
        codes = {a.code for a in report.alarms}
        self.assertIn("snapshot_expired", codes)     # 体检时快照已过期
        self.assertIn("usage_fail_rate", codes)

    def test_routes_reflect_family_stats(self) -> None:
        report = run_maintenance(self.sink, self.snaps)
        paths = {d.tag: d.path for d in report.routes}
        self.assertEqual(paths["fam-a"], "hit")
        self.assertEqual(paths["fam-d"], "model")     # 样本不足

    def test_second_run_is_idempotent(self) -> None:
        run_maintenance(self.sink, self.snaps)
        before = self.sink.counts()
        report = run_maintenance(self.sink, self.snaps)
        self.assertEqual(report.promoted, [])
        self.assertEqual(report.retired, [])
        after = self.sink.counts()
        self.assertEqual(before["experience"], after["experience"])
        self.assertEqual(before["retired"], after["retired"])
        self.assertFalse(report.changed)

    def test_retired_item_leaves_retrieval(self) -> None:
        run_maintenance(self.sink, self.snaps)
        ids = {s.id for s in self.sink.load_experience().retrieve("fam-b")}
        self.assertNotIn("B", ids, "淘汰的经验不能再被检索到")


if __name__ == "__main__":
    unittest.main()