"""规则挖掘的测试：只挖「反复失败且从未成功」的指纹。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mvp.contracts import Judgment, Status, Step, Trace
from mvp.miner import mine, rejected
from mvp.persistence import RunStore


def write_case(sink: RunStore, task_id: str, digest: str, ok: bool) -> None:
    trace = Trace(task_id=task_id, verifier_vid="vf0")
    trace.steps = [Step("t#1", 2, "recall", digest, {"hit": True},
                        Judgment.OK if ok else Judgment.RETRY, 0, 0)]
    trace.outcome = Status.PASS if ok else Status.FAIL
    sink.append_trace(trace)
    if not ok:
        sink.append_negative(trace, reason="reuse_wrong")


class TestMiner(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.sink = RunStore(Path(self.tmp.name) / "run")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_mines_repeated_failure(self) -> None:
        write_case(self.sink, "T1", "BAD1", ok=False)
        write_case(self.sink, "T2", "BAD1", ok=False)
        cands = mine(self.sink)
        self.assertEqual([c.input_hash for c in cands], ["BAD1"])
        self.assertEqual((cands[0].support, cands[0].counter), (2, 0))

    def test_success_blocks_candidate(self) -> None:
        write_case(self.sink, "T1", "MIXED", ok=False)
        write_case(self.sink, "T2", "MIXED", ok=False)
        write_case(self.sink, "T3", "MIXED", ok=True)
        self.assertEqual(mine(self.sink), [])
        reasons = {c.input_hash: c.reason for c in rejected(self.sink)}
        self.assertIn("成功", reasons["MIXED"])

    def test_min_support_threshold(self) -> None:
        write_case(self.sink, "T1", "ONCE", ok=False)
        self.assertEqual(mine(self.sink), [])
        self.assertEqual([c.input_hash for c in mine(self.sink, min_support=1)], ["ONCE"])

    def test_empty_store(self) -> None:
        self.assertEqual(mine(self.sink), [])


if __name__ == "__main__":
    unittest.main()