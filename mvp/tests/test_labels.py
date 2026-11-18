"""标签台账：挖掘的标签从「写死在测试里的真值」变成「落盘的验收结果」。

为什么要这一步：真值在真实域里来自验证器判定、人工复核或下游反馈，不会
以 Python 函数的形式存在于挖掘器里。挖掘只读台账，写台账的是验收环节。

合成域里台账的写入者是真值引擎（`ground_truth`）；换到真实域，写入者换成
人工复核/下游回调，挖掘侧一行不改。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mvp.core.persistence import RunStore
from mvp.core.tasks import build_tasks, ground_truth
from mvp.core.store import ExperienceStore
from mvp.core.verifier import SetMatchVerifier
from mvp.loop.backward import describe
from mvp.loop.learner import learn, learn_from_ledger
from mvp.loop.runner import build_experience, run_path
from mvp.loop.validator import Sample


def temp_store() -> tuple[RunStore, tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory()
    return RunStore(Path(tmp.name)), tmp


class TestLedger(unittest.TestCase):
    def test_round_trip(self) -> None:
        sink, tmp = temp_store()
        self.addCleanup(tmp.cleanup)
        sink.append_label("T1", ["R1", "R2"], source="verifier")
        self.assertEqual(sink.load_labels(), {"T1": {"R1", "R2"}})

    def test_latest_record_wins_but_history_kept(self) -> None:
        sink, tmp = temp_store()
        self.addCleanup(tmp.cleanup)
        sink.append_label("T1", ["R1"], source="verifier")
        sink.append_label("T1", ["R2"], source="human")
        self.assertEqual(sink.load_labels(), {"T1": {"R2"}})
        # append-only：两条都在文件里
        self.assertEqual(len(sink._read(sink.labels_file)), 2)

    def test_counts_include_labels(self) -> None:
        sink, tmp = temp_store()
        self.addCleanup(tmp.cleanup)
        sink.append_label("T1", ["R1"])
        self.assertEqual(sink.counts()["labels"], 1)


class TestMiningFromLedger(unittest.TestCase):
    def _run(self, sink: RunStore, tasks, truth) -> None:
        run_path("A", tasks, truth, SetMatchVerifier(truth),
                 build_experience(), use_shots=False, sink=sink)

    def test_unlabeled_traces_are_skipped(self) -> None:
        sink, tmp = temp_store()
        self.addCleanup(tmp.cleanup)
        tasks = build_tasks(5)
        truth = {t.id: ground_truth(t) for t in tasks}
        self._run(sink, tasks, truth)
        sink.append_label(tasks[0].id, sorted(truth[tasks[0].id]))
        learned, report = learn_from_ledger(sink)
        self.assertEqual(report["samples"], 1)
        self.assertEqual(report["skipped_traces"], 4)

    def test_low_label_coverage_refuses_to_learn(self) -> None:
        """台账只覆盖一小部分轨迹时不许学——样本不够，学出来的是噪声。"""
        sink, tmp = temp_store()
        self.addCleanup(tmp.cleanup)
        tasks = build_tasks(10)
        truth = {t.id: ground_truth(t) for t in tasks}
        self._run(sink, tasks, truth)
        sink.append_label(tasks[0].id, sorted(truth[tasks[0].id]))
        learned, report = learn_from_ledger(sink)
        self.assertEqual(learned, [])
        self.assertIn("覆盖", report["reason"])

    def test_learning_from_ledger_matches_direct_learning(self) -> None:
        sink, tmp = temp_store()
        self.addCleanup(tmp.cleanup)
        tasks = build_tasks(150)
        truth = {t.id: ground_truth(t) for t in tasks}
        self._run(sink, tasks, truth)
        for task in tasks:
            sink.append_label(task.id, sorted(truth[task.id]), source="truth-engine")

        from_ledger, report = learn_from_ledger(sink)
        direct = learn([Sample(t.id, t.__dict__, frozenset(truth[t.id]))
                        for t in tasks])
        self.assertEqual(report["skipped_traces"], 0)
        self.assertEqual(
            [(x.rule.id, describe(x.rule.condition)) for x in from_ledger],
            [(x.rule.id, describe(x.rule.condition)) for x in direct])


if __name__ == "__main__":
    unittest.main()