"""逆向链的测试：三态语义、短路、与正向规则引擎的一致性。"""
from __future__ import annotations

import unittest

from mvp.loop.backward import BackwardEngine, check, full_read_cost
from mvp.core.rules import RuleStore, rule_fields
from mvp.core.tasks import build_tasks, ground_truth

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


class TestConsistency(unittest.TestCase):
    def test_matches_forward_rule_engine(self) -> None:
        engine = BackwardEngine()
        for task in build_tasks(30):
            self.assertEqual(engine.violated(task.__dict__), ground_truth(task), task.id)

    def test_no_unknown_when_all_fields_present(self) -> None:
        engine = BackwardEngine()
        for task in build_tasks(30):
            self.assertEqual(engine.unknown(task.__dict__), set(), task.id)

    def test_reads_less_than_unpruned_baseline(self) -> None:
        engine = BackwardEngine()
        tasks = build_tasks(30)
        actual = sum(sum(v.reads for v in engine.check_all(t.__dict__).values())
                     for t in tasks)
        self.assertLess(actual, full_read_cost() * len(tasks))


class TestTriState(unittest.TestCase):
    def setUp(self) -> None:
        self.store = RuleStore()

    def test_and_short_circuits_on_false(self) -> None:
        # 金额 3000 已证伪 R1，审批字段缺失也不影响结论，且不该去读它
        v = check(self.store.get("R1"), data(amount=3000, has_director_approval=None))
        self.assertEqual(v.status, "clean")
        self.assertEqual(v.reads, 1)
        self.assertEqual(v.missing, ())

    def test_and_unknown_when_needed_field_missing(self) -> None:
        v = check(self.store.get("R1"), data(amount=8000, has_director_approval=None))
        self.assertEqual(v.status, "unknown")
        self.assertIn("has_director_approval", v.missing)

    def test_or_violated_when_first_branch_holds(self) -> None:
        v = check(self.store.get("R2"), data(city_tier=1, hotel_per_night=700))
        self.assertEqual(v.status, "violated")

    def test_or_clean_when_below_cap(self) -> None:
        self.assertEqual(check(self.store.get("R2"), data(city_tier=1, hotel_per_night=500)).status,
                         "clean")
        self.assertEqual(check(self.store.get("R2"), data(city_tier=2, hotel_per_night=350)).status,
                         "clean")

    def test_or_unknown_when_price_missing(self) -> None:
        v = check(self.store.get("R2"), data(city_tier=1, hotel_per_night=None))
        self.assertEqual(v.status, "unknown")

    def test_rule_fields_are_declared(self) -> None:
        self.assertEqual(rule_fields(self.store.get("R1")), ("amount", "has_director_approval"))
        self.assertIn("invoice_dates", rule_fields(self.store.get("R5")))


if __name__ == "__main__":
    unittest.main()