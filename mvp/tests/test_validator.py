"""回放验证器：候选规则能不能从 candidate 进 active，由回放读数判。

判据取自《双引擎逆向推理设计文档》§7（support ≥ 5、counter = 0）与 §8 P2
（回放通过率 > 80%、误伤率 < 5%）。这里只验机制，不验真实业务数据。
"""
from __future__ import annotations

import unittest

from mvp.core.contracts import Judgment, Status, Step, Trace
from mvp.core.rules import R1, All, Fact, Rule, RuleStore
from mvp.core.tasks import build_tasks, ground_truth
from mvp.loop.validator import (
    MIN_SUPPORT,
    Sample,
    gate,
    replay,
    replay_all,
    replay_and_activate,
    replay_shortcut,
    validate_candidates,
)

BASE = {
    "amount": 3000, "has_director_approval": True, "city_tier": 1,
    "hotel_per_night": 500, "nights": 1, "transport_class": "经济舱",
    "meal_days": 2, "meal_total": 150, "invoice_dates": ["2026-01-01"],
    "trip_start": "2026-01-01", "trip_end": "2026-01-02",
    "submitted_month": "2026-01", "has_note": False, "same_day_dup": False,
}


def data(**overrides):
    d = dict(BASE)
    d.update(overrides)
    return d


def sample(sid: str, labels: set[str] | frozenset[str], **overrides) -> Sample:
    return Sample(sid, data(**overrides), frozenset(labels))


def labeled_tasks(n: int = 200) -> list[Sample]:
    """带真值标签的样本。标签是外部的，不是规则自己算的。"""
    return [Sample(t.id, t.__dict__, frozenset(ground_truth(t)))
            for t in build_tasks(n)]


def trace(tid: str, digest: str, outcome: Status) -> Trace:
    return Trace(
        task_id=tid,
        steps=[Step(node_id=f"{tid}#0", level=0, action="extract",
                    input_hash=digest, output=[], judgment=Judgment.OK,
                    cost=1, latency_ms=1)],
        outcome=outcome,
    )


class TestReplayMatrix(unittest.TestCase):
    def test_confusion_counts(self) -> None:
        samples = [
            # 命中且真值也有 → tp
            sample("a", {"R1"}, amount=8000, has_director_approval=False),
            sample("b", {"R1"}, amount=9000, has_director_approval=False),
            # 命中但真值说没错 → fp（误伤）
            sample("c", set(), amount=6000, has_director_approval=False),
            # 没命中且真值说没错 → tn
            sample("d", set(), amount=3000),
            # 没命中但真值说有 → fn（漏报）
            sample("e", {"R1"}, amount=8000, has_director_approval=True),
        ]
        r = replay(RuleStore().get("R1"), samples)
        self.assertEqual((r.tp, r.fp, r.fn, r.tn), (2, 1, 1, 1))
        self.assertEqual(r.support, 2)
        self.assertEqual(r.counter, 1)
        self.assertEqual(r.decided, 5)
        self.assertAlmostEqual(r.accuracy, 3 / 5)
        self.assertAlmostEqual(r.false_alarm_rate, 1 / 2)
        self.assertAlmostEqual(r.miss_rate, 1 / 3)

    def test_unknown_abstains_and_is_not_false_alarm(self) -> None:
        samples = [Sample("u", data(amount=8000, has_director_approval=None),
                          frozenset())]
        r = replay(RuleStore().get("R1"), samples)
        self.assertEqual(r.unknown, 1)
        self.assertEqual(r.decided, 0)
        self.assertEqual(r.counter, 0)
        self.assertEqual(r.false_alarm_rate, 0.0)

    def test_hand_rules_reproduce_ground_truth(self) -> None:
        reports = replay_all(RuleStore().all(), labeled_tasks())
        self.assertEqual(len(reports), 7)
        for r in reports:
            self.assertEqual(r.fp, 0, r.name)
            self.assertEqual(r.fn, 0, r.name)
            self.assertGreater(r.support, 0, r.name)


class TestGate(unittest.TestCase):
    def test_rule_missing_a_condition_is_rejected(self) -> None:
        # 漏掉「无总监审批」：金额超 5000 就判违规，已审批的干净单会被误伤
        broken = Rule("R1", "坏规则：漏掉审批条件",
                      Fact("金额>5000", ("amount",),
                           lambda f: f["amount"] > 5000))
        r = replay(broken, labeled_tasks())
        self.assertGreater(r.fp, 0)
        self.assertLess(r.accuracy, 1.0)
        d = gate(r)
        self.assertFalse(d.ok)
        self.assertTrue(any("误伤" in reason for reason in d.reasons), d.reasons)

    def test_good_rule_is_activatable(self) -> None:
        r = replay(RuleStore().get("R1"), labeled_tasks())
        self.assertGreaterEqual(r.support, MIN_SUPPORT)
        d = gate(r)
        self.assertTrue(d.ok, d.reasons)

    def test_low_support_is_rejected(self) -> None:
        samples = [sample("a", {"R1"}, amount=8000, has_director_approval=False)]
        samples += [sample(f"c{i}", set(), amount=3000) for i in range(10)]
        r = replay(RuleStore().get("R1"), samples)
        self.assertLess(r.support, MIN_SUPPORT)
        d = gate(r)
        self.assertFalse(d.ok)
        self.assertTrue(any("支持样本" in reason for reason in d.reasons), d.reasons)

    def test_false_alarm_above_threshold_is_rejected(self) -> None:
        # 同样漏掉审批条件：10 条真违规 + 90 条 8000 元已审批的干净单都会被判违规
        loose = Rule("R1", "漏审批条件",
                     Fact("金额>5000", ("amount",),
                          lambda f: f["amount"] > 5000))
        samples = [sample(f"p{i}", {"R1"}, amount=8000,
                          has_director_approval=False) for i in range(10)]
        samples += [sample(f"n{i}", set(), amount=8000,
                           has_director_approval=True) for i in range(90)]
        r = replay(loose, samples)
        self.assertGreater(r.false_alarm_rate, 0.05)
        d = gate(r)
        self.assertFalse(d.ok)
        self.assertTrue(any("误伤率" in reason for reason in d.reasons), d.reasons)


class TestShortcutReplay(unittest.TestCase):
    def test_support_and_counter_from_traces(self) -> None:
        traces = [
            trace("t1", "H", Status.FAIL),
            trace("t2", "H", Status.FAIL),
            trace("t3", "X", Status.PASS),
            trace("t4", "X", Status.PASS),
        ]
        r = replay_shortcut("H", traces)
        self.assertEqual((r.support, r.counter), (2, 0))
        # 支持 2 < 5，还不够激活——这是 §7 的硬条件
        self.assertFalse(gate(r).ok)

    def test_shortcut_with_enough_support_is_activatable(self) -> None:
        traces = [trace(f"t{i}", "H", Status.FAIL) for i in range(5)]
        traces += [trace("ok", "X", Status.PASS)]
        r = replay_shortcut("H", traces)
        self.assertEqual(r.support, 5)
        self.assertEqual(r.counter, 0)
        self.assertTrue(gate(r).ok, gate(r).reasons)

    def test_shortcut_seen_in_success_is_rejected(self) -> None:
        traces = [
            trace("t1", "H", Status.FAIL),
            trace("t2", "H", Status.PASS),
        ]
        r = replay_shortcut("H", traces)
        self.assertEqual(r.counter, 1)
        self.assertFalse(gate(r).ok)


class TestReplayAndActivate(unittest.TestCase):
    """验证通过 → 激活；验证否决 → 停在 candidate。"""

    def _samples(self, target: str) -> list[Sample]:
        return [Sample(t.id, t.__dict__,
                       frozenset({target}) if "R1" in ground_truth(t) else frozenset())
                for t in build_tasks(200)]

    def test_valid_rule_is_activated(self) -> None:
        store = RuleStore(())
        cand = Rule("RX", "与 R1 同形", R1.condition)
        d = replay_and_activate(store, cand, self._samples("RX"))
        self.assertTrue(d.ok, d.reasons)
        self.assertEqual(store.get("RX").status, "active")
        self.assertIn("RX", {r.id for r in store.all()})

    def test_invalid_rule_stays_candidate(self) -> None:
        store = RuleStore(())
        # 漏掉审批条件：已审批的超额单会被误伤
        cand = Rule("RX", "漏审批条件",
                    Fact("金额>5000", ("amount",), lambda f: f["amount"] > 5000))
        d = replay_and_activate(store, cand, self._samples("RX"))
        self.assertFalse(d.ok)
        self.assertEqual(store.get("RX").status, "candidate")
        self.assertNotIn("RX", {r.id for r in store.all()})


class TestCandidateGate(unittest.TestCase):
    """挖掘提名、回放判红：两条轨迹走不同的判据，不能被同一条捷径糊过去。"""

    def _sink(self, failures: int):
        import tempfile
        from pathlib import Path

        from mvp.core.persistence import RunStore

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sink = RunStore(Path(tmp.name))
        for i in range(failures):
            tr = trace(f"t{i}", "H", Status.FAIL)
            sink.append_trace(tr)
            sink.append_negative(tr, reason="reuse_wrong")
        return sink

    def test_two_supports_are_not_enough_to_activate(self) -> None:
        decisions = validate_candidates(self._sink(2))
        self.assertEqual(len(decisions), 1)
        self.assertFalse(decisions[0].ok)

    def test_five_supports_activate(self) -> None:
        decisions = validate_candidates(self._sink(5))
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].report.support, 5)
        self.assertTrue(decisions[0].ok, decisions[0].reasons)


if __name__ == "__main__":
    unittest.main()