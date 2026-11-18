"""哨兵探针（§8）：按比例抽任务绕过收敛走探索，比对两条路径的答案。

它防的是「收敛高效地把事做错」：验证器乐观或快照过期时，收敛自己发现不了。
这里用「永远判过」的验证器 + 被改坏的索引模拟那个状态，看探针能不能抓到。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mvp.core.contracts import Kind, Status, Verdict
from mvp.core.persistence import RunStore
from mvp.core.llm import SimulatedLLM
from mvp.core.store import ExperienceStore
from mvp.core.tasks import build_tasks, ground_truth, structural_key
from mvp.core.verifier import SetMatchVerifier
from mvp.loop.probe import ProbeResult, probe_batch, probe_task, should_probe
from mvp.core.snapshot import SnapshotStore
from mvp.loop.sentry import inspect
from mvp.loop.runner import build_index


class AlwaysPass:
    """乐观验证器：任何答案都判过——模拟验证器漂移/快照过期。"""
    vid = "vf-permissive"

    def check(self, task_id, predicted):
        return Verdict(Status.PASS, Kind.DECISIVE, 1.0, "assume_ok")


def temp_store() -> tuple[RunStore, tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory()
    return RunStore(Path(tmp.name)), tmp


class TestSelection(unittest.TestCase):
    def test_deterministic(self) -> None:
        self.assertEqual(should_probe("T-1", rate=0.5),
                         should_probe("T-1", rate=0.5))

    def test_rate_is_roughly_honoured(self) -> None:
        ids = [f"T-{i}" for i in range(2000)]
        hits = sum(1 for i in ids if should_probe(i, rate=0.1))
        self.assertGreater(hits, 120)
        self.assertLess(hits, 280)


class TestProbeAlarm(unittest.TestCase):
    def test_disagreement_raises_alarm(self) -> None:
        sink, tmp = temp_store()
        self.addCleanup(tmp.cleanup)
        for i in range(10):
            sink.append_probe(ProbeResult(f"T{i}", (), (), i >= 2, 1))
        alarms = inspect(sink, SnapshotStore(tmp.name + "/snaps"))
        codes = [a.code for a in alarms]
        self.assertIn("probe_disagree", codes)

    def test_clean_probes_raise_no_alarm(self) -> None:
        sink, tmp = temp_store()
        self.addCleanup(tmp.cleanup)
        for i in range(10):
            sink.append_probe(ProbeResult(f"T{i}", (), (), True, 1))
        alarms = inspect(sink, SnapshotStore(tmp.name + "/snaps"))
        self.assertNotIn("probe_disagree", [a.code for a in alarms])


class TestProbe(unittest.TestCase):
    def setUp(self) -> None:
        self.tasks = build_tasks(30)
        self.truth = {t.id: ground_truth(t) for t in self.tasks}
        self.index = build_index(self.tasks, self.truth, k=20)
        self.store = ExperienceStore()

    def test_agrees_when_cache_is_correct(self) -> None:
        task = self.tasks[0]
        result = probe_task(task, self.truth, AlwaysPass(), self.store,
                            self.index, llm=SimulatedLLM(self.truth, p_base=1.0, p_shot=1.0))
        self.assertTrue(result.agree, result)

    def test_catches_corrupted_cache(self) -> None:
        task = self.tasks[0]
        key = structural_key(task)
        self.index[key] = {"R9"}          # 快照过期：一条不存在的规则
        result = probe_task(task, self.truth, AlwaysPass(), self.store,
                            self.index, llm=SimulatedLLM(self.truth, p_base=1.0, p_shot=1.0))
        self.assertFalse(result.agree)
        self.assertEqual(result.cached, ("R9",))

    def test_probe_is_persisted(self) -> None:
        sink, tmp = temp_store()
        self.addCleanup(tmp.cleanup)
        result = probe_task(self.tasks[0], self.truth, AlwaysPass(), self.store,
                            self.index, llm=SimulatedLLM(self.truth, p_base=1.0, p_shot=1.0))
        sink.append_probe(result)
        loaded = sink.load_probes()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["task_id"], self.tasks[0].id)
        self.assertEqual(sink.counts()["probes"], 1)

    def test_batch_reports_difference_rate(self) -> None:
        corrupted = {structural_key(t) for t in self.tasks[:3]}
        for key in corrupted:
            self.index[key] = {"R9"}
        results, summary = probe_batch(
            self.tasks, self.truth, AlwaysPass(), self.store, self.index,
            rate=1.0, llm=SimulatedLLM(self.truth, p_base=1.0, p_shot=1.0))
        self.assertEqual(summary["probed"], len(self.tasks))
        # 任务集里有重复指纹：改坏 3 个指纹会波及所有共用它们的任务
        expected = sum(1 for t in self.tasks
                       if structural_key(t) in corrupted)
        self.assertEqual(summary["disagree"], expected)
        self.assertGreater(summary["diff_rate"], 0.0)
        self.assertEqual(len(results), len(self.tasks))


if __name__ == "__main__":
    unittest.main()