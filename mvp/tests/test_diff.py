"""双跑差异解释：§8 P4「差异样本 100% 可解释」。

只判机制：每条差异必须落进一个机器可判的归因码，落不进去的就是缺陷，
不能被一句「复用效果更好」糊过去。
"""
from __future__ import annotations

import unittest

from mvp.core.contracts import Judgment, Status, Step, Trace
from mvp.loop.evaluator import Diff, explain, summarize
from mvp.loop.runner import PathResult


def trace(task_id: str, outcome: Status, cost: int, hit: bool = False) -> Trace:
    if hit:
        step = Step(node_id=f"{task_id}#hit", level=2, action="rule_hit",
                    input_hash="k", output=[], judgment=Judgment.RULE_HIT,
                    cost=cost, latency_ms=2)
    else:
        step = Step(node_id=f"{task_id}#a1", level=0, action="extract",
                    input_hash="t", output=[], judgment=Judgment.OK,
                    cost=cost, latency_ms=180)
    return Trace(task_id=task_id, steps=[step], outcome=outcome)


def path(name: str, traces: list[Trace]) -> PathResult:
    return PathResult(name, traces, {"success_rate": 1.0, "avg_cost": 1.0})


class TestExplain(unittest.TestCase):
    def test_saved_cost_when_both_pass(self) -> None:
        base = path("A", [trace("t1", Status.PASS, 1000)])
        cand = path("C", [trace("t1", Status.PASS, 10, hit=True)])
        diffs = explain(base, cand)
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0].code, "saved_cost")
        self.assertIn("990", diffs[0].detail)

    def test_hit_rejected_when_verifier_vetoes_hit(self) -> None:
        """命中但验证器否决、降级重跑后仍错：不是复用误伤，是命中的结论被拒。"""
        base = path("A", [trace("t1", Status.PASS, 1000)])
        cand = path("C", [trace("t1", Status.FAIL, 10, hit=True)])
        diffs = explain(base, cand)
        self.assertEqual([d.code for d in diffs], ["hit_rejected"])

    def test_shots_mode_has_its_own_codes(self) -> None:
        base = path("A", [trace("t1", Status.PASS, 1000), trace("t2", Status.FAIL, 1000)])
        cand = path("B", [trace("t1", Status.FAIL, 1500), trace("t2", Status.PASS, 1500)])
        codes = [d.code for d in explain(base, cand, mode="shots")]
        self.assertEqual(codes, ["shot_harm", "shot_rescued"])

    def test_rescued_when_cached_path_passes(self) -> None:
        base = path("A", [trace("t1", Status.FAIL, 1000)])
        cand = path("C", [trace("t1", Status.PASS, 10, hit=True)])
        diffs = explain(base, cand)
        self.assertEqual([d.code for d in diffs], ["rescued"])

    def test_no_diff_when_outcome_and_cost_equal(self) -> None:
        base = path("A", [trace("t1", Status.PASS, 1000)])
        cand = path("C", [trace("t1", Status.PASS, 1000)])
        self.assertEqual(explain(base, cand), [])

    def test_unexpected_when_no_hit_but_outcome_differs(self) -> None:
        # 没走命中却和基线结论不同：同参数同模型，只可能是缺陷
        base = path("A", [trace("t1", Status.PASS, 1000)])
        cand = path("C", [trace("t1", Status.FAIL, 1000)])
        diffs = explain(base, cand)
        self.assertEqual([d.code for d in diffs], ["unexpected"])

    def test_summary_counts_every_diff(self) -> None:
        base = path("A", [
            trace("t1", Status.PASS, 1000),
            trace("t2", Status.PASS, 1000),
            trace("t3", Status.FAIL, 1000),
        ])
        cand = path("C", [
            trace("t1", Status.PASS, 10, hit=True),
            trace("t2", Status.FAIL, 10, hit=True),
            trace("t3", Status.PASS, 10, hit=True),
        ])
        s = summarize(explain(base, cand), total=3)
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["differ"], 3)
        self.assertEqual(s["explained"], 3)
        self.assertEqual(s["unexplained"], 0)
        self.assertEqual(s["by_code"], {"saved_cost": 1, "hit_rejected": 1, "rescued": 1})

    def test_no_double_count_when_task_missing_on_one_side(self) -> None:
        base = path("A", [trace("t1", Status.PASS, 1000)])
        cand = path("C", [])
        self.assertEqual(explain(base, cand), [])


if __name__ == "__main__":
    unittest.main()