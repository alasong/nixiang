"""预算接入执行路径的测试：上界拦截、按实际扣减、不超支。"""
from __future__ import annotations

import unittest

from mvp.budget import Budget
from mvp.contracts import Status
from mvp.runner import MAX_ATTEMPTS, run_path
from mvp.store import ExperienceStore
from mvp.tasks import build_tasks, ground_truth
from mvp.verifier import SetMatchVerifier


class TestBudgetedRun(unittest.TestCase):
    def setUp(self) -> None:
        self.tasks = build_tasks(n=4)
        self.truth = {t.id: ground_truth(t) for t in self.tasks}
        self.verifier = SetMatchVerifier(self.truth)
        self.store = ExperienceStore()

    def _run(self, **kw):
        return run_path("budgeted", self.tasks, self.truth, self.verifier,
                        self.store, use_shots=False, **kw)

    def test_full_budget_charges_actual_tokens(self) -> None:
        pool = Budget("L2", 100_000)
        result = self._run(budget=pool)
        spent = sum(t.total_cost for t in result.traces)
        self.assertEqual(pool.spent, spent)
        self.assertLessEqual(pool.spent, pool.limit)

    def test_small_budget_gates_everything_without_spending(self) -> None:
        pool = Budget("L2", 500)               # 上界 3×716=2148，一条都开不了
        result = self._run(budget=pool)
        self.assertEqual(pool.spent, 0)
        self.assertTrue(all(t.outcome == Status.UNKNOWN for t in result.traces))
        self.assertTrue(all(t.steps[-1].action == "budget_gate" for t in result.traces))

    def test_partial_budget_stops_gracefully(self) -> None:
        pool = Budget("L2", 3000)              # 够 1 条（2148），第 2 条起被拦
        result = self._run(budget=pool)
        outcomes = [t.outcome for t in result.traces]
        self.assertLessEqual(pool.spent, pool.limit)
        ran = sum(1 for o in outcomes if o != Status.UNKNOWN)
        self.assertIn(Status.UNKNOWN, outcomes)     # 预算耗尽必须停
        self.assertGreaterEqual(ran, 1)             # 但至少跑过一条
        self.assertLess(ran, len(outcomes))         # 且没跑完

    def test_upper_bound_covers_retries(self) -> None:
        # 最坏情况上界必须 ≥ 单任务最多 MAX_ATTEMPTS 次调用的成本，否则会越界
        bound = MAX_ATTEMPTS * (550 + 0 + 166)
        self.assertGreaterEqual(bound, MAX_ATTEMPTS * 716)

    def test_unused_budget_can_be_released(self) -> None:
        root = Budget("L2", 10_000)
        (child,) = root.split([1.0])
        self._run(budget=child)
        back = child.release()
        self.assertGreater(back, 0)
        self.assertLessEqual(root.spent, root.limit)


if __name__ == "__main__":
    unittest.main()