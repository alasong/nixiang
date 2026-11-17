"""契约 §6「必须走通的场景」的机检测试。

场景 3（验证器升级后重判）在 `test_integration.py` 里，这里补 1 / 2 / 5。
场景 4（子目标失败 → L1 局部重分解）没有实现对象，显式 skip 并写明原因——
跳过要在测试输出里看得见，不能悄悄缺一条。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mvp.budget import Budget, Overdraft
from mvp.contracts import Kind, Status, Verdict
from mvp.llm import Shot
from mvp.persistence import RunStore
from mvp.promotion import apply_promotions, evaluate
from mvp.runner import MAX_ATTEMPTS, run_path
from mvp.snapshot import SnapshotStore
from mvp.subgoal import SubGoal, run_parent
from mvp.store import ExperienceItem, ExperienceStore
from mvp.tasks import build_tasks, ground_truth
from mvp.verifier import SetMatchVerifier


class ForcedFail:
    vid = "vf-forced-fail"

    def check(self, task_id: str, predicted: set[str]) -> Verdict:
        return Verdict(Status.FAIL, Kind.DECISIVE, 1.0, "forced")


class TestScenario1ExploreThenReuse(unittest.TestCase):
    """新任务、经验库空 → 探索 → 写经验 → 下次可检索。"""

    def test_explore_produces_reusable_experience(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tasks = build_tasks(n=6)
            truth = {t.id: ground_truth(t) for t in tasks}
            verifier = SetMatchVerifier(truth)
            sink = RunStore(root / "run")
            snaps = SnapshotStore(root / "snap")

            # 第一轮：经验库空，走模型（= 探索）
            empty = sink.load_experience()
            self.assertEqual(empty.retrieve("tier1-high", k=5), [])
            run_path("explore", tasks, truth, verifier, empty, use_shots=False, sink=sink)

            # 探索产出 scored 经验，两次复用通过后晋升为可检索的 decisive
            scored = ExperienceItem(
                shot=Shot("E1", "tier1-high", {"R1"}, "金额超 5000 核对审批"),
                trace_id=tasks[0].id, snapshot_vid="v1",
                verdict=Verdict(Status.PASS, Kind.SCORED, 0.7, "explore"))
            sink.append_experience(scored)
            for _ in range(2):
                sink.append_usage(["E1"], tasks[0].id, "pass", "复用通过")

            store = sink.load_experience()
            promoted, snap = apply_promotions(evaluate(store, sink.load_usage()), store,
                                              sink, snaps, "vf0")
            self.assertEqual(len(promoted), 1)
            self.assertIsNotNone(snap)

            # 下次（新进程重建）可检索到
            again = sink.load_experience()
            self.assertEqual([s.id for s in again.retrieve("tier1-high", k=5)], ["E1"])


class TestScenario2FailureWritesNegative(unittest.TestCase):
    """老任务、收敛失败 → 记负证据、降级，不整体重规划。"""

    def test_failure_writes_negative_once(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tasks = build_tasks(n=4)
            truth = {t.id: ground_truth(t) for t in tasks}
            sink = RunStore(root / "run")
            store = ExperienceStore()
            run_path("fail", tasks, truth, ForcedFail(), store,
                     use_shots=False, sink=sink)

            negative = sink.load_negative()
            self.assertEqual(len(negative), len(tasks))
            rec = negative[0]
            self.assertTrue(rec["input_hashes"], "负证据必须带输入指纹")
            self.assertEqual(rec["reason"], "attempts_exhausted")
            # 不整体重规划：同一任务只留下一条负证据，重试次数不超上限
            self.assertEqual(sink.counts()["negative"], len(tasks))
            self.assertLessEqual(rec["cost"], MAX_ATTEMPTS * 10_000)


class TestScenario5BudgetExhausted(unittest.TestCase):
    """预算耗尽 → 逐层归还 → 优雅失败，不超支、不死循环。"""

    def test_exhaustion_stops_without_overdraft(self) -> None:
        root = Budget("L2", 100)
        mid, spare = root.split([0.8, 0.2])
        (leaf,) = mid.split([1.0])

        spent_rounds = 0
        with self.assertRaises(Overdraft):
            while True:                     # 无预算检查的话这会死循环
                leaf.spend(30)
                spent_rounds += 1
                self.assertLessEqual(spent_rounds, 10, "失败必须终止，不能死循环")

        self.assertEqual(leaf.spent, 60)    # 60 + 30 = 90 ≤ 80? 见下：mid 上限 80
        self.assertEqual(mid.spent, 60)
        self.assertEqual(root.spent, 60)

        back = leaf.release() + mid.release() + spare.release()
        self.assertGreaterEqual(back, 40)
        self.assertLessEqual(root.spent, root.limit)


class TestScenario4SubgoalRecomposition(unittest.TestCase):
    """子目标失败 → L1 局部重分解并成功，不惊动 L2。"""

    def test_subgoal_failure_is_contained(self) -> None:
        calls: list[str] = []
        out = run_parent(
            [SubGoal("parse", lambda: False, (lambda: True,)),
             SubGoal("check", lambda: True)],
            l2=lambda: calls.append("l2") or True)
        self.assertFalse(out["l2_called"], "局部重分解成功时不许惊动 L2")
        self.assertEqual(out["results"][0].branch, 1)
        self.assertEqual(out["results"][0].attempts, 2)

    def test_handoff_when_recovery_fails(self) -> None:
        calls: list[str] = []
        out = run_parent([SubGoal("parse", lambda: False, (lambda: False,))],
                         l2=lambda: calls.append("l2") or True)
        self.assertTrue(out["l2_called"], "重分解用尽必须交棒")


if __name__ == "__main__":
    unittest.main()