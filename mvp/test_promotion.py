"""快照与自动晋升的测试：判定逻辑必须能判红。"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mvp.contracts import Kind, Status, Verdict
from mvp.llm import Shot
from mvp.persistence import RunStore
from mvp.promotion import apply_promotions, evaluate
from mvp.snapshot import SnapshotStore
from mvp.store import ExperienceItem, ExperienceStore

SCORED = Verdict(Status.PASS, Kind.SCORED, 0.7, "self-check")
DECISIVE = Verdict(Status.PASS, Kind.DECISIVE, 1.0, "manual")


def _item(shot_id: str, verdict=SCORED, tag: str = "survey") -> ExperienceItem:
    return ExperienceItem(Shot(shot_id, tag, set(), f"经验 {shot_id}"),
                          "TASK-1", "v1", verdict)


class TestSnapshot(unittest.TestCase):
    def test_commit_increments_vid_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            snaps = SnapshotStore(d)
            v1 = snaps.commit("vf0", ["A"], ttl_days=30, source="initial")
            v2 = snaps.commit("vf0", ["A", "B"], ttl_days=7)
            self.assertEqual((v1.vid, v2.vid), ("v1", "v2"))
            self.assertEqual(snaps.load("v2").experience_ids, ("A", "B"))
            self.assertEqual(snaps.latest().vid, "v2")

    def test_ttl_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            snap = SnapshotStore(d).commit("vf0", [], ttl_days=1)
            created = datetime.fromisoformat(snap.created_at)
            self.assertFalse(snap.is_expired(created + timedelta(hours=23)))
            self.assertTrue(snap.is_expired(created + timedelta(days=1, seconds=1)))


class TestPromotion(unittest.TestCase):
    def test_two_passes_promote(self) -> None:
        store = ExperienceStore()
        store.add(_item("A"))
        usage = [{"shot_ids": ["A"], "outcome": "pass"},
                 {"shot_ids": ["A"], "outcome": "pass"}]
        d = {x.shot_id: x for x in evaluate(store, usage)}
        self.assertTrue(d["A"].promoted)
        self.assertEqual(d["A"].reason, "2/2 次复用通过")

    def test_one_use_is_not_enough(self) -> None:
        store = ExperienceStore()
        store.add(_item("A"))
        d = {x.shot_id: x for x in evaluate(store, [{"shot_ids": ["A"], "outcome": "pass"}])}
        self.assertFalse(d["A"].promoted)
        self.assertIn("< 2", d["A"].reason)

    def test_any_failure_blocks(self) -> None:
        store = ExperienceStore()
        store.add(_item("A"))
        usage = [{"shot_ids": ["A"], "outcome": "pass"},
                 {"shot_ids": ["A"], "outcome": "fail"}]
        d = {x.shot_id: x for x in evaluate(store, usage)}
        self.assertFalse(d["A"].promoted)
        self.assertIn("失败", d["A"].reason)

    def test_decisive_items_are_skipped(self) -> None:
        store = ExperienceStore()
        store.add(_item("A", verdict=DECISIVE))
        usage = [{"shot_ids": ["A"], "outcome": "pass"}] * 3
        self.assertEqual(evaluate(store, usage), [])

    def test_apply_writes_experience_and_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sink = RunStore(Path(d) / "run")
            snaps = SnapshotStore(Path(d) / "snap")
            store = ExperienceStore()
            store.add(_item("A"))
            usage = [{"shot_ids": ["A"], "outcome": "pass"}] * 2
            promoted, snap = apply_promotions(evaluate(store, usage), store, sink,
                                              snaps, "vf0")
            self.assertIsNotNone(snap)
            self.assertEqual(snap.experience_ids, ("A",))
            self.assertEqual(len(promoted), 1)
            self.assertEqual(promoted[0].verdict.kind, Kind.DECISIVE)
            self.assertEqual(sink.counts()["experience"], 1)
            # 晋升后内环检索应该能拿到
            self.assertEqual([s.id for s in sink.load_experience().retrieve("survey")], ["A"])


if __name__ == "__main__":
    unittest.main()