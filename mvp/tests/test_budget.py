"""预算树的测试：守恒、禁止透支、归还。"""
from __future__ import annotations

import unittest

from mvp.loop.budget import Budget, Overdraft


class TestBudget(unittest.TestCase):
    def test_split_conserves_total(self) -> None:
        root = Budget("L2", 1000)
        a, b = root.split([0.7, 0.3])
        self.assertEqual(a.limit + b.limit, 1000)
        self.assertEqual(root.reserved, 1000)
        self.assertEqual(root.free, 0)

    def test_cannot_split_beyond_free(self) -> None:
        root = Budget("L2", 100)
        root.split([1.0])
        with self.assertRaises(Overdraft):
            root.split([1.0])

    def test_spend_propagates_to_ancestors(self) -> None:
        root = Budget("L2", 100)
        (mid,) = root.split([1.0])
        (leaf,) = mid.split([1.0])
        leaf.spend(30)
        self.assertEqual((leaf.spent, mid.spent, root.spent), (30, 30, 30))

    def test_overdraft_rejected_at_ancestor(self) -> None:
        root = Budget("L2", 100)
        left, right = root.split([0.5, 0.5])
        left.spend(50)
        right.spend(50)              # 合计正好 100
        with self.assertRaises(Overdraft):
            right.spend(1)           # 自身还有空间不行，root 已经没有

    def test_release_frees_capacity(self) -> None:
        root = Budget("L2", 100)
        mid, other = root.split([0.6, 0.4])
        mid.spend(10)
        back = mid.release()
        self.assertEqual(back, 50)
        self.assertEqual(root.reserved, 40)
        (again,) = root.split([1.0])
        self.assertEqual(again.limit, 60)

    def test_negative_amount_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Budget("x", 10).spend(-1)


if __name__ == "__main__":
    unittest.main()