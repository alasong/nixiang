"""淘汰机制的测试：判据、墓碑、加载过滤、幂等。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mvp.contracts import Kind, Status, Verdict
from mvp.llm import Shot
from mvp.persistence import RunStore
from mvp.retire import apply, evaluate
from mvp.store import ExperienceItem

DECISIVE = Verdict(Status.PASS, Kind.DECISIVE, 1.0, "ok")


def item(shot_id: str) -> ExperienceItem:
    return ExperienceItem(Shot(shot_id, "fam", set(), f"经验 {shot_id}"),
                          "T1", "v1", DECISIVE)


class TestRetire(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.sink = RunStore(Path(self.tmp.name) / "run")
        for sid in ("A", "B"):
            self.sink.append_experience(item(sid))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_high_failure_rate_retires(self) -> None:
        usage = [{"shot_ids": ["A"], "outcome": "pass"},
                 {"shot_ids": ["A"], "outcome": "fail"}]
        decisions = {d.shot_id: d for d in evaluate(self.sink.load_experience(), usage)}
        self.assertTrue(decisions["A"].retire)
        self.assertIn("50%", decisions["A"].reason)

    def test_low_failure_rate_kept(self) -> None:
        usage = [{"shot_ids": ["A"], "outcome": "pass"}] * 3 + \
                [{"shot_ids": ["A"], "outcome": "fail"}]
        decisions = {d.shot_id: d for d in evaluate(self.sink.load_experience(), usage)}
        self.assertFalse(decisions["A"].retire)
        self.assertIn("保留", decisions["A"].reason)

    def test_insufficient_uses_kept(self) -> None:
        usage = [{"shot_ids": ["A"], "outcome": "fail"}]
        decisions = {d.shot_id: d for d in evaluate(self.sink.load_experience(), usage)}
        self.assertFalse(decisions["A"].retire)
        self.assertIn("样本不足", decisions["A"].reason)

    def test_apply_filters_from_store_but_keeps_history(self) -> None:
        usage = [{"shot_ids": ["A"], "outcome": "fail"},
                 {"shot_ids": ["A"], "outcome": "fail"}]
        retired = apply(evaluate(self.sink.load_experience(), usage), self.sink)
        self.assertEqual(retired, ["A"])
        # 加载时被过滤，检索拿不到；但 experience.jsonl 行数不变
        self.assertEqual([s.id for s in self.sink.load_experience().retrieve("fam")], ["B"])
        self.assertEqual(self.sink.counts()["experience"], 2)
        self.assertEqual(self.sink.counts()["retired"], 1)

    def test_idempotent(self) -> None:
        usage = [{"shot_ids": ["A"], "outcome": "fail"}] * 2
        apply(evaluate(self.sink.load_experience(), usage), self.sink)
        again = apply(evaluate(self.sink.load_experience(), usage), self.sink)
        self.assertEqual(again, [])
        self.assertEqual(self.sink.counts()["retired"], 1)


if __name__ == "__main__":
    unittest.main()