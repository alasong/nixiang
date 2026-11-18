"""借鉴 goal-tree 落到看板的三件事：目标覆盖、开工前红检、失败阻断。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mvp import board

ROOT = Path(__file__).resolve().parents[2]
REAL = ROOT / "TASKS.md"

FAKE = """# T

| id | 任务 | 能力 | 覆盖 | 验收命令 | 期望 | 状态 | 尝试 | 证据 |
|---|---|---|---|---|---|---|---|---|
| T1 | 甲 | act | g1 | `python3 -c 'print(1)'` | rc=0 | todo | 0 | — |
| T2 | 乙 | act | g1 | `python3 -c 'raise SystemExit(3)'` | rc=0 | todo | 0 | — |

## 目标条目

| gid | 条目 |
|---|---|
| g1 | 目标一 |
"""


def fake_board(tc: unittest.TestCase) -> board.Board:
    tmp = tempfile.TemporaryDirectory()
    tc.addCleanup(tmp.cleanup)
    path = Path(tmp.name) / "TASKS.md"
    path.write_text(FAKE, encoding="utf-8")
    return board.load(path)


class TestGoalCoverage(unittest.TestCase):
    def test_goals_table_is_parsed(self) -> None:
        b = board.load(REAL)
        gids = [g.gid for g in b.goals]
        self.assertEqual(gids[:6], ["g0", "g1", "g2", "g3", "g4", "g5"])
        self.assertIn("g6", gids)
        self.assertEqual(board.get(b, "T6").covers, "g0")

    def test_no_gaps_on_real_board(self) -> None:
        b = board.load(REAL)
        result = board.coverage(b)
        self.assertEqual(result["gaps"], [])
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["items"]), len(b.goals))

    def test_gap_when_goal_not_claimed(self) -> None:
        b = fake_board(self)
        board.get(b, "T1").covers = "—"
        board.get(b, "T2").covers = "—"
        result = board.coverage(b)
        self.assertIn("g1", result["gaps"])
        self.assertFalse(result["ok"])

    def test_goal_done_requires_all_cards_done(self) -> None:
        b = fake_board(self)
        self.assertFalse(board.coverage(b)["goal_done"])
        board.finish(b, "T1", rc=0, note="手动验收")
        board.finish(b, "T2", rc=0, note="手动验收")
        self.assertTrue(board.coverage(b)["goal_done"])

    def test_add_rejects_unknown_goal(self) -> None:
        b = fake_board(self)
        with self.assertRaises(board.BoardError):
            board.add(b, title="x", capability="act", covers="g99",
                      command="python3 -c 'print(1)'", expect="rc=0")


class TestRedCheck(unittest.TestCase):
    def test_start_refuses_when_acceptance_already_passes(self) -> None:
        b = fake_board(self)
        with self.assertRaises(board.BoardError):
            board.start(b, "T1")

    def test_start_allows_when_acceptance_fails(self) -> None:
        b = fake_board(self)
        board.start(b, "T2")
        self.assertEqual(board.get(b, "T2").status, "doing")


class TestBlockedAfterFailures(unittest.TestCase):
    def test_three_failures_mark_blocked(self) -> None:
        b = fake_board(self)
        board.start(b, "T2")
        for _ in range(3):
            board.verify(b, "T2")
        saved = board.get(b, "T2")
        self.assertEqual(saved.status, "blocked")
        self.assertEqual(saved.attempts, 3)
        self.assertIn("3", saved.evidence)

    def test_success_after_failure_marks_done(self) -> None:
        b = fake_board(self)
        board.start(b, "T2")
        board.verify(b, "T2")
        self.assertEqual(board.get(b, "T2").attempts, 1)
        board.get(b, "T2").command = "python3 -c 'print(1)'"
        board.verify(b, "T2")
        self.assertEqual(board.get(b, "T2").status, "done")


if __name__ == "__main__":
    unittest.main()
