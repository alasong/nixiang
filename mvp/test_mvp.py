"""MVP 单元测试。对应基线声明 §3 的「单元门」。

运行：python3 -m unittest mvp.test_mvp -v
"""
from __future__ import annotations

import unittest

from mvp.contracts import Judgment, Kind, Status, Step, Trace, Verdict, fingerprint
from mvp.demo_dual_loop import cutoff_matches, parse_cutoff
from mvp.llm import Shot, SimulatedLLM
from mvp.runner import build_experience, build_index, run_path_cached
from mvp.store import ExperienceStore, ExperienceItem
from mvp.tasks import build_tasks, ground_truth, structural_key
from mvp.verifier import SetMatchVerifier


class TestVerifier(unittest.TestCase):
    def setUp(self) -> None:
        self.truth = {"T1": {"R1", "R3"}}
        self.v = SetMatchVerifier(self.truth)

    def test_exact_match_passes(self) -> None:
        r = self.v.check("T1", {"R1", "R3"})
        self.assertEqual(r.status, Status.PASS)
        self.assertEqual(r.kind, Kind.DECISIVE)
        self.assertEqual(r.confidence, 1.0)

    def test_missing_fails(self) -> None:
        r = self.v.check("T1", {"R1"})
        self.assertEqual(r.status, Status.FAIL)
        self.assertIn("R3", r.detail)

    def test_extra_fails(self) -> None:
        r = self.v.check("T1", {"R1", "R3", "R5"})
        self.assertEqual(r.status, Status.FAIL)
        self.assertIn("R5", r.detail)

    def test_no_ground_truth_is_unknown(self) -> None:
        r = self.v.check("NOPE", set())
        self.assertEqual(r.status, Status.UNKNOWN)


class TestExperienceStore(unittest.TestCase):
    def test_empty_store_returns_empty(self) -> None:
        store = ExperienceStore()
        self.assertEqual(store.retrieve("tier1-high"), [])

    def test_exact_tag_ranks_first(self) -> None:
        store = build_experience()
        shots = store.retrieve("tier2-high", k=2)
        self.assertEqual(shots[0].scene_tag, "tier2-high")

    def test_low_confidence_excluded(self) -> None:
        store = ExperienceStore()
        store.add(ExperienceItem(
            shot=Shot("X", "tier1-high", {"R1"}, "x"),
            trace_id="t", snapshot_vid="v0",
            verdict=Verdict(Status.PASS, Kind.SCORED, 0.6, "scored"),
        ))
        self.assertEqual(store.retrieve("tier1-high"), [])


class TestContract(unittest.TestCase):
    def test_trace_cost_accumulates(self) -> None:
        t = Trace(task_id="T")
        t.steps.append(Step("a", 0, "x", "h", [], Judgment.OK, 100, 1))
        t.steps.append(Step("b", 0, "x", "h", [], Judgment.RETRY, 250, 1))
        self.assertEqual(t.total_cost, 350)

    def test_verdict_has_three_states(self) -> None:
        self.assertEqual({s.value for s in Status}, {"pass", "fail", "unknown"})


class TestTasks(unittest.TestCase):
    def test_structural_key_ignores_id(self) -> None:
        tasks = build_tasks(n=30, seed=42)
        keyed: dict[str, list[str]] = {}
        for t in tasks:
            keyed.setdefault(structural_key(t), []).append(t.id)
        dup_groups = [ids for ids in keyed.values() if len(ids) > 1]
        self.assertTrue(dup_groups, "应存在重复结构任务")
        for ids in dup_groups:
            self.assertTrue(len(ids) >= 2)

    def test_ground_truth_shape(self) -> None:
        t = build_tasks(n=5, seed=1)[0]
        gt = ground_truth(t)
        self.assertTrue(gt.issubset({"R1", "R2", "R3", "R4", "R5", "R6", "R7"}))


class TestHitPath(unittest.TestCase):
    def test_full_index_gives_high_hit_rate(self) -> None:
        tasks = build_tasks()
        truth = {t.id: ground_truth(t) for t in tasks}
        verifier = SetMatchVerifier(truth)
        store = build_experience()
        idx = build_index(tasks, truth, k=999)
        r = run_path_cached("t", tasks, truth, verifier, store, idx)
        self.assertGreaterEqual(r.stats["hit_rate"], 0.99)
        self.assertLess(r.stats["avg_cost"], 100)

    def test_empty_index_falls_back_to_model(self) -> None:
        tasks = build_tasks()
        truth = {t.id: ground_truth(t) for t in tasks}
        verifier = SetMatchVerifier(truth)
        store = build_experience()
        r = run_path_cached("t", tasks, truth, verifier, store, {})
        self.assertEqual(r.stats["hit_rate"], 0.0)
        self.assertGreater(r.stats["avg_cost"], 500)


class TestSimulatedLLM(unittest.TestCase):
    def test_shot_reduces_error_rate(self) -> None:
        truth = {"T1": {"R1", "R2", "R3", "R4", "R5", "R6", "R7"}}
        text = "T1。"
        covered = [Shot("s", "t", {"R1", "R2", "R3", "R4", "R5", "R6", "R7"}, "x")]

        llm_no = SimulatedLLM(truth, seed=3)
        llm_yes = SimulatedLLM(truth, seed=3)
        err_no = err_yes = 0
        for _ in range(200):
            if llm_no.extract(text, []).rules != {"R1", "R2", "R3", "R4", "R5", "R6", "R7"}:
                err_no += 1
            if llm_yes.extract(text, covered).rules != {"R1", "R2", "R3", "R4", "R5", "R6", "R7"}:
                err_yes += 1
        self.assertGreater(err_no, err_yes)

    def test_more_shots_cost_more_tokens(self) -> None:
        truth = {"T1": set()}
        llm = SimulatedLLM(truth, seed=1)
        a = llm.extract("T1。", [])
        b = llm.extract("T1。", [Shot("s", "t", {"R1"}, "x")] * 3)
        self.assertGreater(b.total_tokens, a.total_tokens)


class TestFreshnessParsing(unittest.TestCase):
    """时效核验的解析：页面里两种截止日写法都要认，认不出必须返回 None 而不是瞎猜。"""

    def test_uppercase_label_form(self) -> None:
        self.assertEqual(parse_cutoff("<p>DATA CUTOFF: NOV 3, 2026</p>"), "NOV 3, 2026")

    def test_prose_form(self) -> None:
        self.assertEqual(parse_cutoff("After our data cutoff of Nov 3, 2026, METR published"),
                         "Nov 3, 2026")

    def test_missing_date_is_none(self) -> None:
        self.assertIsNone(parse_cutoff("no date here"))

    def test_cutoff_matches_exact_day(self) -> None:
        self.assertTrue(cutoff_matches("NOV 3, 2026"))
        self.assertTrue(cutoff_matches("Nov 3, 2026"))
        self.assertFalse(cutoff_matches("Nov 4, 2026"))
        self.assertFalse(cutoff_matches("garbage"))


if __name__ == "__main__":
    unittest.main()
