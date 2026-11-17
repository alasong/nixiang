"""L1 子目标层的测试：局部重分解、确定性、深度上限交棒。"""
from __future__ import annotations

import unittest

from mvp.loop.subgoal import HANDOFF, PASS, SubGoal, run_parent, run_subgoal


def fail() -> bool:
    return False


def ok() -> bool:
    return True


class TestSubGoal(unittest.TestCase):
    def test_first_action_succeeds(self) -> None:
        r = run_subgoal(SubGoal("g", ok))
        self.assertEqual((r.outcome, r.branch, r.attempts), (PASS, 0, 1))

    def test_local_redecomposition_succeeds(self) -> None:
        r = run_subgoal(SubGoal("g", fail, (ok,)))
        self.assertEqual((r.outcome, r.branch, r.attempts), (PASS, 1, 2))
        self.assertEqual([e["ok"] for e in r.events], [False, True])

    def test_depth_limit_zero_means_no_alternative(self) -> None:
        r = run_subgoal(SubGoal("g", fail, (ok,)), max_depth=0)
        self.assertEqual((r.outcome, r.attempts), (HANDOFF, 1))

    def test_exhausted_alternatives_hand_off(self) -> None:
        r = run_subgoal(SubGoal("g", fail, (fail, fail)))
        self.assertEqual(r.outcome, HANDOFF)
        self.assertEqual(r.attempts, 3)

    def test_deterministic_same_branch(self) -> None:
        goal = SubGoal("g", fail, (fail, ok))
        a, b = run_subgoal(goal), run_subgoal(goal)
        self.assertEqual((a.branch, a.attempts, a.events), (b.branch, b.attempts, b.events))


class TestParent(unittest.TestCase):
    def test_all_pass_does_not_disturb_l2(self) -> None:
        calls: list[str] = []
        out = run_parent([SubGoal("a", ok), SubGoal("b", ok)], lambda: calls.append("l2") or False)
        self.assertFalse(out["l2_called"])
        self.assertEqual(out["outcome"], PASS)
        self.assertEqual(calls, [])

    def test_recovered_subgoal_does_not_disturb_l2(self) -> None:
        calls: list[str] = []
        out = run_parent([SubGoal("a", fail, (ok,)), SubGoal("b", ok)],
                         lambda: calls.append("l2") or False)
        self.assertFalse(out["l2_called"])
        self.assertEqual(out["results"][0].branch, 1)

    def test_handoff_reaches_l2_when_recovery_fails(self) -> None:
        calls: list[str] = []
        out = run_parent([SubGoal("a", fail, (fail,))], lambda: calls.append("l2") or True)
        self.assertTrue(out["l2_called"])
        self.assertEqual(out["outcome"], PASS)


if __name__ == "__main__":
    unittest.main()