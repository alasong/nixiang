"""轨迹的事实字段：挖掘的原料必须能被回落。

没有它，挖掘只能吃「人手喂的样本表」；有了它，系统自己跑出来的轨迹就能回流
成下一轮的训练集。这是 P1 从「离线样本」走到「自产原料」的那一步。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mvp.core.contracts import Status, Trace
from mvp.core.persistence import RunStore
from mvp.core.paths import RUNS_DIR
from mvp.core.store import ExperienceStore
from mvp.core.tasks import build_tasks, ground_truth, task_facts
from mvp.core.verifier import SetMatchVerifier
from mvp.loop.backward import describe
from mvp.loop.learner import learn, traces_to_samples
from mvp.loop.validator import Sample
from mvp.loop.runner import build_experience, run_path


def temp_store() -> RunStore:
    tmp = tempfile.TemporaryDirectory()
    run = RunStore(Path(tmp.name))
    return run, tmp


class TestTraceFacts(unittest.TestCase):
    def test_round_trip_keeps_facts(self) -> None:
        sink, tmp = temp_store()
        self.addCleanup(tmp.cleanup)
        trace = Trace(task_id="T1", facts={"amount": 8000, "city_tier": 1})
        sink.append_trace(trace)
        loaded = sink.load_traces()[0]
        self.assertEqual(loaded.facts, {"amount": 8000, "city_tier": 1})

    def test_old_trace_without_facts_loads_empty(self) -> None:
        sink, tmp = temp_store()
        self.addCleanup(tmp.cleanup)
        line = {"task_id": "OLD", "snapshot_vid": "v0", "verifier_vid": "vf0",
                "outcome": "pass", "attempts": 1, "total_cost": 0, "steps": []}
        sink.traces_file.write_text(json.dumps(line) + "\n", encoding="utf-8")
        self.assertEqual(sink.load_traces()[0].facts, {})

    def test_run_path_records_task_facts(self) -> None:
        sink, tmp = temp_store()
        self.addCleanup(tmp.cleanup)
        tasks = build_tasks(3)
        truth = {t.id: ground_truth(t) for t in tasks}
        run_path("A", tasks, truth, SetMatchVerifier(truth),
                 ExperienceStore(), use_shots=False, sink=sink)
        traces = sink.load_traces()
        self.assertEqual(len(traces), 3)
        for trace in traces:
            self.assertIn("amount", trace.facts)
            self.assertIn("city_tier", trace.facts)


class TestMiningFromTraces(unittest.TestCase):
    def test_traces_to_samples_skips_traces_without_facts(self) -> None:
        with_facts = Trace(task_id="T1", facts={"amount": 3000})
        without = Trace(task_id="T2")
        samples, skipped = traces_to_samples(
            [with_facts, without], labels={"T1": {"R1"}, "T2": {"R1"}})
        self.assertEqual([s.sample_id for s in samples], ["T1"])
        self.assertEqual(skipped, 1)

    def test_learning_from_traces_matches_direct_learning(self) -> None:
        sink, tmp = temp_store()
        self.addCleanup(tmp.cleanup)
        tasks = build_tasks(120)
        truth = {t.id: ground_truth(t) for t in tasks}
        run_path("A", tasks, truth, SetMatchVerifier(truth),
                 build_experience(), use_shots=False, sink=sink)
        traces = sink.load_traces()
        samples, _ = traces_to_samples(traces, labels=truth)

        from_traces = [(x.rule.id, describe(x.rule.condition))
                       for x in learn(samples)]
        direct = [(x.rule.id, describe(x.rule.condition))
                  for x in learn([Sample(t.id, task_facts(t), frozenset(truth[t.id]))
                                  for t in tasks])]
        self.assertEqual(from_traces, direct)
        self.assertTrue(from_traces, "轨迹里一条规则都没学出来")


if __name__ == "__main__":
    unittest.main()