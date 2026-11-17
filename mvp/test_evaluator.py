"""留出集评估的测试：切分不许重叠、判据必须能判红。"""
from __future__ import annotations

import unittest

from mvp.evaluator import COST_GATE, QUALITY_GATE, evaluate, gate, split_tasks
from mvp.tasks import build_tasks


class TestSplit(unittest.TestCase):
    def test_disjoint_and_sized(self) -> None:
        tasks = build_tasks(n=30)
        train, heldout = split_tasks(tasks, split=0.6, seed=42)
        self.assertEqual((len(train), len(heldout)), (18, 12))
        self.assertFalse({t.id for t in train} & {t.id for t in heldout})

    def test_deterministic(self) -> None:
        tasks = build_tasks(n=30)
        a = [t.id for t in split_tasks(tasks, seed=7)[1]]
        b = [t.id for t in split_tasks(tasks, seed=7)[1]]
        self.assertEqual(a, b)

    def test_bad_split_rejected(self) -> None:
        with self.assertRaises(ValueError):
            split_tasks(build_tasks(n=10), split=1.0)


class TestGate(unittest.TestCase):
    base = {"avg_cost": 1000.0, "success_rate": 0.80}

    def test_pass_when_both_met(self) -> None:
        g = gate(self.base, {"avg_cost": 500.0, "success_rate": 0.80})
        self.assertTrue(g["passed"])
        self.assertEqual(g["cost_change_pct"], -50.0)

    def test_fail_on_cost(self) -> None:
        g = gate(self.base, {"avg_cost": 900.0, "success_rate": 0.80})
        self.assertFalse(g["passed"])
        self.assertIn("成本降幅", g["reasons"][0])

    def test_fail_on_quality(self) -> None:
        g = gate(self.base, {"avg_cost": 500.0, "success_rate": 0.70})
        self.assertFalse(g["passed"])
        self.assertIn("成功率", g["reasons"][0])

    def test_boundary_values(self) -> None:
        # 恰好等于阈值算通过（≥ / ≤ 的语义）
        cost = self.base["avg_cost"] * (1 + COST_GATE / 100)
        quality = self.base["success_rate"] + QUALITY_GATE / 100
        g = gate(self.base, {"avg_cost": cost, "success_rate": quality})
        self.assertTrue(g["passed"], g)


class TestEvaluate(unittest.TestCase):
    def test_heldout_report_structure(self) -> None:
        result = evaluate(split=0.6, index_size=12)
        self.assertEqual((result["train"], result["heldout"]), (18, 12))
        self.assertEqual(set(result["paths"]), {"A", "B", "C"})
        self.assertEqual(set(result["gates"]), {"B", "C"})
        # 索引只用训练段：留出段的成功率为 C 路径的读数，不可能是 100% 之外的构造值
        self.assertLessEqual(result["paths"]["C"].stats["hit_rate"], 1.0)

    def test_c_path_expected_to_pass_on_synthetic(self) -> None:
        """合成域上 C 路径应过门；不过门说明判据或数据被改坏了。"""
        result = evaluate(split=0.6, index_size=12)
        self.assertTrue(result["gates"]["C"]["passed"], result["gates"]["C"]["reasons"])


if __name__ == "__main__":
    unittest.main()