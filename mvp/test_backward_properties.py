"""逆向链的不变量测试（随机化）。

固定 30 条任务只能证明「这几条对」。这里用随机生成的单据检查三条性质：

  1. 全字段：逆向结论必须和正向规则引擎（ground_truth）逐条一致
  2. 丢字段：只允许在 true / unknown 之间移动，**不能跨真值**——
       逆向说「违规」⇒ 补全后必然违规；逆向说「不违规」⇒ 补全后必然不违规
  3. 读取有界：短路后读取次数不超过「叶子字段出现次数」——注意不是去重后的
     字段数：OR 分支各持一份条件，共享字段会被重复读（R2 就会读 3 次）

第 2 条是反向推理的核心承诺：部分信息下的确定结论，必须经得起补全。
"""
from __future__ import annotations

import random
import unittest

from mvp.backward import CLEAN, UNKNOWN, VIOLATED, check
from mvp.rules import RuleStore, rule_field_reads
from mvp.tasks import Expense, ground_truth

RULE_IDS = ("R1", "R2", "R3", "R4", "R5", "R6", "R7")
CITIES = [(1, "北京"), (2, "杭州")]
TRANSPORTS = ["经济舱", "二等座", "商务舱", "一等座"]


def rand_expense(rng: random.Random) -> Expense:
    tier, city = rng.choice(CITIES)
    start_day = rng.randint(1, 20)
    nights = rng.randint(1, 5)
    month = f"2026-{rng.randint(1, 4):02d}"
    start = f"{month}-{start_day:02d}"
    end = f"{month}-{start_day + nights:02d}"
    return Expense(
        id=f"GEN-{rng.randint(0, 9999)}",
        city_tier=tier, city=city,
        amount=float(rng.choice([800, 2600, 4200, 5200, 8000])),
        has_director_approval=rng.random() > 0.5,
        hotel_per_night=float(rng.choice([300, 500, 620, 700])),
        nights=nights,
        transport_class=rng.choice(TRANSPORTS),
        meal_days=nights + 1,
        meal_total=float(rng.choice([100, 250, 400, 600])),
        invoice_dates=[start] if rng.random() > 0.3 else [start, "2026-05-05"],
        trip_start=start, trip_end=end,
        submitted_month=month if rng.random() > 0.2 else "2026-06",
        has_note=rng.random() > 0.5,
        same_day_dup=rng.random() < 0.2,
    )


def as_dict(expense: Expense) -> dict:
    return {k: v for k, v in expense.__dict__.items() if k != "id"}


class TestBackwardInvariants(unittest.TestCase):
    def setUp(self) -> None:
        self.store = RuleStore()
        self.rng = random.Random(7)
        self.samples = [rand_expense(self.rng) for _ in range(120)]

    def test_complete_records_match_forward_engine(self) -> None:
        for expense in self.samples:
            truth = ground_truth(expense)
            for rule_id in RULE_IDS:
                verdict = check(self.store.get(rule_id), as_dict(expense))
                self.assertNotEqual(verdict.status, UNKNOWN)
                self.assertEqual(verdict.status == VIOLATED, rule_id in truth,
                                 f"{expense.id} {rule_id}")

    def test_missing_fields_never_cross_truth(self) -> None:
        for expense in self.samples:
            data = as_dict(expense)
            for rule_id in RULE_IDS:
                rule = self.store.get(rule_id)
                complete = check(rule, data).status
                for _ in range(3):
                    partial = dict(data)
                    for field in self.rng.sample(list(partial), k=self.rng.randint(1, 3)):
                        partial[field] = None
                    got = check(rule, partial).status
                    if got == VIOLATED:
                        self.assertEqual(complete, VIOLATED,
                                         f"{expense.id} {rule_id}：部分信息判违规，补全后却不违规")
                    if got == CLEAN:
                        self.assertEqual(complete, CLEAN,
                                         f"{expense.id} {rule_id}：部分信息判不违规，补全后却违规")

    def test_reads_bounded_by_declared_fields(self) -> None:
        for expense in self.samples:
            data = as_dict(expense)
            for rule_id in RULE_IDS:
                rule = self.store.get(rule_id)
                verdict = check(rule, data)
                self.assertLessEqual(verdict.reads, rule_field_reads(rule),
                                     f"{rule_id} 读了 {verdict.reads} 次"
                                     f"（上界 {rule_field_reads(rule)}）")


if __name__ == "__main__":
    unittest.main()