"""闭环演示的读数：冷启动 → 学规则 → 符号优先，三个数必须同时成立。"""
from __future__ import annotations

import unittest

from mvp.demos.self_improve import SYMBOLIC_COST, run_demo


class TestSelfImprove(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = run_demo()

    def test_learns_and_activates_all_rules(self) -> None:
        self.assertEqual(self.result["learned"], 7)
        self.assertEqual(sorted(self.result["activated"]),
                         [f"R{i}" for i in range(1, 8)])

    def test_symbolic_path_is_cheaper_and_better(self) -> None:
        r = self.result
        self.assertEqual(r["symbolic_success"], 1.0)
        self.assertEqual(r["symbolic_cost"], SYMBOLIC_COST)
        self.assertEqual(r["fallback"], 0)
        self.assertLess(r["symbolic_cost"], r["baseline_cost"])
        self.assertGreaterEqual(r["symbolic_success"], r["baseline_success"])

    def test_cold_start_is_not_perfect(self) -> None:
        """冷启动要是满分会掩盖学习效果，这个前提得成立。"""
        self.assertLess(self.result["cold_success"], 1.0)


if __name__ == "__main__":
    unittest.main()