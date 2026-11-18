"""无偏采样（§8）：保留一部分流量纯随机进探索，防止「越成功样本越偏」。

问题：收敛命中率越高，进探索的任务越少，剩下的探索样本全是「没命中」的。
它们和到达流量不是同一分布——系统越成功，探索样本越偏，对没见过的族估计越错。

做法：按任务 id 的哈希抽固定比例，强制走探索，不经任何路由筛选。抽样确定
可复现，比例可测。偏倚用「探索样本 vs 全量流量」在族标签上的总变差距离度量。
"""
from __future__ import annotations

import unittest

from mvp.core.tasks import build_tasks
from mvp.loop.sampling import assign, bias, explore_share, selected, tv_distance


class TestSelection(unittest.TestCase):
    def test_deterministic_and_rate_honoured(self) -> None:
        ids = [f"T-{i}" for i in range(2000)]
        self.assertEqual(selected("T-7", 0.1), selected("T-7", 0.1))
        hits = sum(1 for i in ids if selected(i, 0.1))
        self.assertGreater(hits, 120)
        self.assertLess(hits, 280)

    def test_zero_and_full_rate(self) -> None:
        self.assertFalse(selected("T-1", 0.0))
        self.assertTrue(selected("T-1", 1.0))


class TestAssign(unittest.TestCase):
    def test_indexed_task_goes_converge_without_sampling(self) -> None:
        self.assertEqual(assign("T-1", indexed=True, rate=0.0), "converge")

    def test_unindexed_task_goes_explore(self) -> None:
        self.assertEqual(assign("T-1", indexed=False, rate=0.0), "explore")

    def test_sampled_task_forces_explore_even_if_indexed(self) -> None:
        # 找到一条会被抽中的任务 id
        task_id = next(f"T-{i}" for i in range(1000) if selected(f"T-{i}", 1.0))
        self.assertEqual(assign(task_id, indexed=True, rate=1.0), "explore")


class TestBias(unittest.TestCase):
    def setUp(self) -> None:
        self.tasks = build_tasks(200)

    def test_tv_distance_basics(self) -> None:
        self.assertAlmostEqual(tv_distance({"a": 1.0}, {"a": 1.0}), 0.0)
        self.assertAlmostEqual(tv_distance({"a": 1.0}, {"b": 1.0}), 1.0)
        self.assertAlmostEqual(tv_distance({"a": 0.5, "b": 0.5}, {"a": 1.0}), 0.5)

    def test_explore_share_counts(self) -> None:
        index = {t.id for t in self.tasks[:50]}
        share = explore_share(self.tasks, index, rate=0.0)
        self.assertEqual(share["converge"], 50)
        self.assertEqual(share["explore"], 150)

    def test_biased_coverage_makes_explore_skewed(self) -> None:
        """索引只覆盖高金额任务时，探索样本会偏到低金额族；抽样能把它拉回。"""
        index = {t.id for t in self.tasks if t.amount > 2000}
        off = bias(self.tasks, index, rate=0.0)
        on = bias(self.tasks, index, rate=0.2)
        self.assertGreater(off["tv"], on["tv"])
        self.assertGreater(off["explore_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()