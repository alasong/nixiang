"""路由与哨兵的测试：阈值要能判红，告警要能漏检检查。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mvp.core.contracts import Kind, Status, Verdict
from mvp.core.llm import Shot
from mvp.core.persistence import RunStore
from mvp.demos.route_and_sentry import build_fixture
from mvp.loop.router import FamilyStats, aggregate, route
from mvp.loop.sentry import inspect
from mvp.core.snapshot import SnapshotStore
from mvp.core.store import ExperienceItem, ExperienceStore


class TestRouter(unittest.TestCase):
    def test_aggregate_by_tag(self) -> None:
        store = ExperienceStore()
        store.add(ExperienceItem(Shot("A1", "fam-a", set(), "x"), "T1", "v1",
                                 Verdict(Status.PASS, Kind.DECISIVE, 1.0, "")))
        usage = [{"shot_ids": ["A1"], "outcome": "pass"},
                 {"shot_ids": ["A1"], "outcome": "fail"},
                 {"shot_ids": ["A1"], "outcome": "pass"}]
        stats = aggregate(usage, store)["fam-a"]
        self.assertEqual((stats.uses, stats.passed), (3, 2))

    def test_insufficient_data_routes_to_model(self) -> None:
        d = route(FamilyStats("fam", uses=2, passed=2))
        self.assertEqual(d.path, "model")
        self.assertIn("不足以判断", d.reason)

    def test_high_pass_rate_routes_to_hit(self) -> None:
        d = route(FamilyStats("fam", uses=3, passed=3))
        self.assertEqual(d.path, "hit")

    def test_low_pass_rate_disables(self) -> None:
        d = route(FamilyStats("fam", uses=3, passed=1))
        self.assertEqual(d.path, "disable")
        self.assertIn("停用", d.reason)


class TestSentry(unittest.TestCase):
    def test_fixture_raises_expected_alarms(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sink, snaps = build_fixture(Path(d))
            codes = {a.code for a in inspect(sink, snaps)}
            self.assertIn("snapshot_expired", codes)
            self.assertIn("usage_fail_rate", codes)
            self.assertIn("dormant_experience", codes)

    def test_clean_run_has_no_alarm(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sink = RunStore(Path(d) / "run")
            snaps = SnapshotStore(Path(d) / "snap")
            snaps.commit("vf0", ["A1"], ttl_days=30)
            sink.append_experience(ExperienceItem(
                Shot("A1", "fam", set(), "x"), "T1", "v1",
                Verdict(Status.PASS, Kind.DECISIVE, 1.0, "")))
            sink.append_usage(["A1"], "T2", "pass")
            self.assertEqual(inspect(sink, snaps), [])

    def test_pipeline_gap_when_experience_without_usage(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sink = RunStore(Path(d) / "run")
            snaps = SnapshotStore(Path(d) / "snap")
            snaps.commit("vf0", ["A1"], ttl_days=30)
            sink.append_experience(ExperienceItem(
                Shot("A1", "fam", set(), "x"), "T1", "v1",
                Verdict(Status.PASS, Kind.DECISIVE, 1.0, "")))
            codes = {a.code for a in inspect(sink, snaps)}
            self.assertIn("pipeline_gap", codes)


if __name__ == "__main__":
    unittest.main()