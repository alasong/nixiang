"""文本看板的读写与状态机：解析 TASKS.md、推进卡片、跑验收。"""
from __future__ import annotations

import re
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


class TestParse(unittest.TestCase):
    def test_parses_real_board(self) -> None:
        b = board.load(REAL)
        self.assertEqual([c.id for c in b.cards],
                         ["T1", "T2", "T3", "T4", "T5", "T6"])
        self.assertEqual(b.cards[0].status, "done")
        self.assertIn("R1", b.cards[0].expect)
        self.assertIn("mvp.handle", b.cards[1].command)

    def test_render_round_trip(self) -> None:
        b = board.load(REAL)
        again = board.parse(board.render(b), REAL)
        self.assertEqual([(c.id, c.status) for c in again.cards],
                         [(c.id, c.status) for c in b.cards])

    def test_rejects_unknown_status(self) -> None:
        # 不假设板上一定有 todo 卡：取第一张卡的状态改成非法值
        text = re.sub(r"\| (?:todo|doing|done|blocked) \|", "| 进行中 |",
                      board.render(board.load(REAL)), count=1)
        self.assertIn("| 进行中 |", text)
        with self.assertRaises(board.BoardError):
            board.parse(text, REAL)


class TestTransitions(unittest.TestCase):
    def test_start_requires_single_doing(self) -> None:
        b = fake_board(self)
        board.start(b, "T2")          # T2 的验收失败，红检放行
        self.assertEqual(board.get(b, "T2").status, "doing")
        with self.assertRaises(board.BoardError):
            board.start(b, "T1")

    def test_add_respects_open_limit(self) -> None:
        b = fake_board(self)
        for i in range(3):
            board.add(b, title=f"加{i}", capability="act",
                      command="python3 -c 'print(1)'", expect="rc=0")
        with self.assertRaises(board.BoardError):
            board.add(b, title="越界", capability="act",
                      command="python3 -c 'print(1)'", expect="rc=0")

    def test_verify_success_marks_done(self) -> None:
        b = fake_board(self)
        board.verify(b, "T1")
        saved = board.get(b, "T1")
        self.assertEqual(saved.status, "done")
        self.assertIn("rc=0", saved.evidence)

    def test_verify_failure_keeps_doing_with_evidence(self) -> None:
        b = fake_board(self)
        board.verify(b, "T2")
        saved = board.get(b, "T2")
        self.assertNotEqual(saved.status, "done")
        self.assertIn("rc=3", saved.evidence)

    def test_done_requires_evidence(self) -> None:
        b = fake_board(self)
        with self.assertRaises(board.BoardError):
            board.finish(b, "T1", rc=0, note="")

    def test_persisted_after_save_and_reload(self) -> None:
        b = fake_board(self)
        board.get(b, "T1").title = "改过的标题"
        board.save(b)
        again = board.load(b.path)
        self.assertEqual(board.get(again, "T1").title, "改过的标题")


if __name__ == "__main__":
    unittest.main()
