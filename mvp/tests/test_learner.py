"""规则挖掘（P1）：从带真值样本里学「条件 → 结论」，再交给回放验证器判。

判据不预设「必须学出 7 条」：能学的学出来，学不出的（特征表达不了）如实报告。
学出来的规则必须**在训练集上零误伤**（纯合取），这条由测试兜底。
"""
from __future__ import annotations

import unittest

from mvp.core.rules import RuleStore
from mvp.core.tasks import build_tasks, ground_truth
from mvp.loop.backward import describe
from mvp.loop.learner import learn, learn_with_report
from mvp.loop.validator import Sample, gate, replay, replay_and_activate


def corpus(n: int = 200, seed: int = 42) -> list[Sample]:
    return [Sample(t.id, t.__dict__, frozenset(ground_truth(t)))
            for t in build_tasks(n, seed=seed)]


def by_id(learned) -> dict:
    return {item.rule.id: item for item in learned}


class TestLearn(unittest.TestCase):
    def test_recovers_core_rules(self) -> None:
        learned = by_id(learn(corpus()))
        # R1/R2/R3/R4/R6 的判据能被「字段 + 阈值」表达；R5/R7 需要日期区间、月份比较
        self.assertTrue({"R1", "R2", "R3", "R4", "R6"} <= set(learned),
                        f"只学到 {sorted(learned)}")

    def test_learned_rules_are_pure_on_training(self) -> None:
        train = corpus()
        for item in learn(train):
            r = replay(item.rule, train)
            self.assertEqual(r.counter, 0, f"{item.rule.id} 有误伤：{r.row()}")
            self.assertGreaterEqual(r.support, 5, item.rule.id)

    def test_generalizes_to_fresh_corpus(self) -> None:
        """阈值从数据里学，换一批样本仍要零误伤、零漏报——否则是记住了样本。"""
        train = by_id(learn(corpus(200, seed=42)))
        fresh = corpus(200, seed=7)
        for rid, item in train.items():
            r = replay(item.rule, fresh)
            self.assertEqual(r.counter, 0, f"{rid} 在留出集误伤：{r.row()}")
            self.assertEqual(r.fn, 0, f"{rid} 在留出集漏报：{r.row()}")

    def test_unlearnable_labels_are_not_emitted(self) -> None:
        learned, skipped = learn_with_report(corpus())
        by_label = {item.rule.id: item for item in learned}
        # 日期越界与跨月无说明需要区间/前缀特征，靠字段阈值表达不了，不能硬凑
        self.assertNotIn("R5", by_label)
        self.assertNotIn("R7", by_label)
        self.assertEqual(set(skipped), {"R5", "R7"})

    def test_learned_rules_activate_through_gate(self) -> None:
        train = corpus()
        learned = by_id(learn(train))
        store = RuleStore(())
        decision = replay_and_activate(store, learned["R1"].rule, train)
        self.assertTrue(decision.ok, decision.reasons)
        self.assertEqual(store.get("R1").status, "active")

    def test_learning_is_deterministic(self) -> None:
        a = [(item.rule.id, describe(item.rule.condition)) for item in learn(corpus())]
        b = [(item.rule.id, describe(item.rule.condition)) for item in learn(corpus())]
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()