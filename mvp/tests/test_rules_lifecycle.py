"""规则生命周期：candidate → active → retired，以及容量上限。

对齐《双引擎逆向推理设计文档》§7：
  迁移条件  support ≥ 5、counter = 0
  容量上限  单域 ≤ 300 条，超限强制先做退役评估
  retired   不是删除，保留可查
"""
from __future__ import annotations

import unittest

from mvp.core.rules import CAPACITY, MIN_SUPPORT, Rule, RuleError, RuleStore, All, Fact


def rule(rid: str = "RX", desc: str = "测试规则") -> Rule:
    return Rule(rid, desc, Fact("总是成立", (), lambda f: True))


class TestLifecycle(unittest.TestCase):
    def test_default_rules_are_active(self) -> None:
        store = RuleStore()
        self.assertEqual(len(store.all()), 7)
        self.assertEqual(store.candidates(), ())

    def test_candidate_is_not_used_by_engine(self) -> None:
        store = RuleStore()
        store.register(rule())
        self.assertEqual(len(store.candidates()), 1)
        self.assertNotIn("RX", {r.id for r in store.all()})

    def test_activate_with_enough_support(self) -> None:
        store = RuleStore()
        store.register(rule())
        store.activate("RX", support=MIN_SUPPORT, counter=0)
        self.assertIn("RX", {r.id for r in store.all()})

    def test_activate_rejects_low_support(self) -> None:
        store = RuleStore()
        store.register(rule())
        with self.assertRaises(RuleError):
            store.activate("RX", support=MIN_SUPPORT - 1, counter=0)

    def test_activate_rejects_any_counter(self) -> None:
        store = RuleStore()
        store.register(rule())
        with self.assertRaises(RuleError):
            store.activate("RX", support=MIN_SUPPORT, counter=1)

    def test_retire_keeps_record_and_removes_from_active(self) -> None:
        store = RuleStore()
        store.retire("R1", reason="误伤超标")
        self.assertNotIn("R1", {r.id for r in store.all()})
        self.assertEqual([r.id for r in store.retired()], ["R1"])
        self.assertEqual(store.get("R1").status, "retired")

    def test_capacity_forces_retirement_before_activating(self) -> None:
        store = RuleStore((), capacity=2)
        store.register(rule("RA"))
        store.register(rule("RB"))
        store.activate("RA", support=MIN_SUPPORT, counter=0)
        store.activate("RB", support=MIN_SUPPORT, counter=0)
        store.register(rule("RC"))
        with self.assertRaises(RuleError):
            store.activate("RC", support=MIN_SUPPORT, counter=0)
        store.retire("RA", reason="腾位置")
        store.activate("RC", support=MIN_SUPPORT, counter=0)
        self.assertEqual({r.id for r in store.all()}, {"RB", "RC"})

    def test_capacity_defaults_to_300(self) -> None:
        self.assertEqual(CAPACITY, 300)


if __name__ == "__main__":
    unittest.main()