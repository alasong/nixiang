"""自举的测试：轨迹里长出任务清单，去重、跳过空描述、能跑通 H1 工具。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mvp.core.contracts import Judgment, Status, Step, Trace
from mvp.core.persistence import RunStore
from mvp.loop.self_bootstrap import collect_tasks, write_jsonl


def write_trace(sink: RunStore, task_id: str, text: str) -> None:
    trace = Trace(task_id=task_id, verifier_vid="vf0", outcome=Status.PASS,
                  task_text=text)
    trace.steps = [Step(f"{task_id}#s1", 0, "run", f"h-{task_id}",
                        {"ok": True}, Judgment.OK, 10, 0)]
    sink.append_trace(trace)


class TestSelfBootstrap(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "run"
        self.sink = RunStore(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_collects_and_dedupes(self) -> None:
        write_trace(self.sink, "T1", "核对差旅报销住宿标准")
        write_trace(self.sink, "T2", "核对差旅报销住宿标准")   # 重复文本
        write_trace(self.sink, "T3", "审查采购合同付款条款")
        tasks = collect_tasks([self.root / "traces.jsonl"])
        self.assertEqual(len(tasks), 2)
        self.assertEqual({t["id"] for t in tasks}, {"T1", "T3"})

    def test_skips_empty_text(self) -> None:
        write_trace(self.sink, "T1", "")
        self.assertEqual(collect_tasks([self.root / "traces.jsonl"]), [])

    def test_task_text_survives_round_trip(self) -> None:
        write_trace(self.sink, "T1", "生成经营报表按月汇总")
        trace = self.sink.load_traces()[0]
        self.assertEqual(trace.task_text, "生成经营报表按月汇总")

    def test_write_jsonl_shape(self) -> None:
        tasks = [{"id": "T1", "text": "核对住宿标准"}]
        out = write_jsonl(tasks, Path(self.tmp.name) / "sub" / "tasks.jsonl")
        lines = out.read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(lines[0]), tasks[0])

    def test_clean_trace_has_empty_text(self) -> None:
        """旧轨迹没有 task_text 时不报错，只是一条也导不出。"""
        trace = Trace(task_id="legacy", verifier_vid="vf0")
        self.assertEqual(trace.task_text, "")


if __name__ == "__main__":
    unittest.main()