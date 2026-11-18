"""文本看板的读写与状态机：解析 TASKS.md、推进卡片、跑验收。"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from mvp import board

ROOT = Path(__file__).resolve().parents[2]
REAL = ROOT / "TASKS.md"


def temp_board(tc: unittest.TestCase) -> board.Board:
    tmp = tempfile.TemporaryDirectory()
    tc.addCleanup(tmp.cleanup)
    path = Path(tmp.name) / "TASKS.md"
    shutil.copy(REAL, path)
    return board.load(path)


class TestParse(unittest.TestCase):
    def test_parses_real_board(self) -> None:
        b = board.load(REAL)
        self.assertEqual([c.id for c in b.cards],
                         ["T1", "T2", "T3", "T4", "T5"])
        self.assertEqual(b.cards[0].status, "done")
        self.assertIn("R1", b.cards[0].expect)
        self.assertIn("mvp.handle", b.cards[1].command)

    def test_render_round_trip(self) -> None:
        b = board.load(REAL)
        again = board.parse(board.render(b), REAL)
        self.assertEqual([(c.id, c.status) for c in again.cards],
                         [(c.id, c.status) for c in b.cards])

    def test_rejects_unknown_status(self) -> None:
        text = board.render(board.load(REAL)).replace("| todo |", "| 进行中 |", 1)
        with self.assertRaises(board.BoardError):
            board.parse(text, REAL)


class TestTransitions(unittest.TestCase):
    def test_start_requires_single_doing(self) -> None:
        b = temp_board(self)
        board.start(b, "T2")
        self.assertEqual(board.get(b, "T2").status, "doing")
        with self.assertRaises(board.BoardError):
            board.start(b, "T3")

    def test_add_respects_open_limit(self) -> None:
        b = temp_board(self)
        # 现有 4 个未完成（T2-T5），加 1 个到 5，再加就该拒绝
        board.add(b, title="第六件事", capability="act",
                  command="python3 -c 'print(1)'", expect="rc=0")
        with self.assertRaises(board.BoardError):
            board.add(b, title="第七件事", capability="act",
                      command="python3 -c 'print(1)'", expect="rc=0")

    def test_verify_success_marks_done(self) -> None:
        b = temp_board(self)
        card = board.add(b, title="输出 R1 的假任务", capability="judge",
                         command="python3 -c \"print('R1')\"",
                         expect="rc=0，输出含 `R1`")
        board.verify(b, card.id)
        saved = board.get(b, card.id)
        self.assertEqual(saved.status, "done")
        self.assertIn("rc=0", saved.evidence)

    def test_verify_failure_keeps_doing_with_evidence(self) -> None:
        b = temp_board(self)
        card = board.add(b, title="必然失败", capability="act",
                         command="python3 -c 'raise SystemExit(3)'",
                         expect="rc=0")
        board.verify(b, card.id)
        saved = board.get(b, card.id)
        self.assertNotEqual(saved.status, "done")
        self.assertIn("rc=3", saved.evidence)

    def test_done_requires_evidence(self) -> None:
        b = temp_board(self)
        with self.assertRaises(board.BoardError):
            board.finish(b, "T2", rc=0, note="")

    def test_persisted_after_save_and_reload(self) -> None:
        b = temp_board(self)
        card = board.add(b, title="落盘检查", capability="act",
                         command="python3 -c 'print(1)'", expect="rc=0")
        board.save(b)
        again = board.load(b.path)
        self.assertEqual(board.get(again, card.id).title, "落盘检查")


if __name__ == "__main__":
    unittest.main()