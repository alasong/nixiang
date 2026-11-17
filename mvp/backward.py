"""逆向推理：目标驱动的 AND-OR 展开，部分信息下也能给出确定结论。

和正向（`tasks.ground_truth` 逐条算）的区别：

  正向  把 7 条规则挨个算一遍，每条规则依赖的字段全读。
  逆向  只展开与目标规则有关的条件树；AND 命中 FALSE 立刻停，OR 命中 TRUE 立刻停；
        字段缺失时按三态返回 unknown，不猜也不硬给结论。

两个真实价值（demo 里有读数）：
  1. 部分信息下的**确定否定**：金额 ≤ 5000 时，即使「是否总监审批」缺失，
     R1 也确定不成立 —— 缺失的字段根本不需要取。
  2. 剪枝：短路后少读字段，读取次数可以直接数出来。

仍缺（本轮不做）：模型兜底（unknown 时调外部的契约）、预算切分、规则挖掘。
"""
from __future__ import annotations

from dataclasses import dataclass

from .rules import All, Any, Fact, Rule, RuleStore, rule_fields

MISSING = object()

VIOLATED = "violated"
CLEAN = "clean"
UNKNOWN = "unknown"
_STATUS = {"true": VIOLATED, "false": CLEAN, "unknown": UNKNOWN}


class Facts:
    """事实读取器：记录读了哪些字段，缺的记 MISSING。"""

    def __init__(self, data: dict) -> None:
        self._data = data
        self.reads = 0
        self.missing: list[str] = []

    def get(self, field: str):
        self.reads += 1
        value = self._data.get(field)
        if value is None:
            self.missing.append(field)
            return MISSING
        return value


@dataclass(frozen=True)
class RuleVerdict:
    rule_id: str
    status: str
    reads: int
    missing: tuple[str, ...]

    @property
    def violated(self) -> bool:
        return self.status == VIOLATED


def evaluate(node, facts: Facts) -> str:
    """三态求值（Kleene），带短路。"""
    if isinstance(node, Fact):
        values = {}
        for field in node.fields:
            value = facts.get(field)
            if value is MISSING:
                return "unknown"
            values[field] = value
        return "true" if node.test(values) else "false"
    if isinstance(node, All):
        unknown = False
        for child in node.children:
            result = evaluate(child, facts)
            if result == "false":          # 合取被证伪，后面的条件不必再取
                return "false"
            unknown = unknown or result == "unknown"
        return "unknown" if unknown else "true"
    if isinstance(node, Any):
        unknown = False
        for child in node.children:
            result = evaluate(child, facts)
            if result == "true":           # 析取已成立，其余分支不必再试
                return "true"
            unknown = unknown or result == "unknown"
        return "unknown" if unknown else "false"
    raise TypeError(f"未知节点类型 {type(node)!r}")


def check(rule: Rule, data: dict) -> RuleVerdict:
    facts = Facts(data)
    status = _STATUS[evaluate(rule.condition, facts)]
    return RuleVerdict(rule.id, status, facts.reads, tuple(sorted(set(facts.missing))))


class BackwardEngine:
    """对一组成任务做逆向判定。只读事实，不调模型（v0.1 §3 的 L3 约束）。"""

    def __init__(self, store: RuleStore | None = None) -> None:
        self.store = store or RuleStore()

    def check_all(self, data: dict) -> dict[str, RuleVerdict]:
        return {r.id: check(r, data) for r in self.store.all()}

    def violated(self, data: dict) -> set[str]:
        return {rid for rid, v in self.check_all(data).items() if v.violated}

    def unknown(self, data: dict) -> set[str]:
        return {rid for rid, v in self.check_all(data).items() if v.status == UNKNOWN}


def describe(node) -> str:
    """把条件树摊平成一行，用于解释「这条规则为什么成立/不成立」。"""
    if isinstance(node, Fact):
        return str(node)
    sep = " ∧ " if isinstance(node, All) else " ∨ "
    return "(" + sep.join(describe(c) for c in node.children) + ")"


def full_read_cost(store: RuleStore | None = None) -> int:
    """不剪枝的基线：每条规则把它依赖的字段各读一次。"""
    store = store or RuleStore()
    return sum(len(rule_fields(r)) for r in store.all())


def demo() -> None:
    from .tasks import build_tasks, ground_truth

    tasks = build_tasks(30)
    engine = BackwardEngine()
    baseline = full_read_cost()

    mismatches = []
    reads = 0
    clean = 0
    for task in tasks:
        data = {k: v for k, v in task.__dict__.items()}
        verdicts = engine.check_all(data)
        reads += sum(v.reads for v in verdicts.values())
        clean += sum(1 for v in verdicts.values() if v.status == CLEAN)
        if {rid for rid, v in verdicts.items() if v.violated} != ground_truth(task):
            mismatches.append(task.id)

    print("逆向推理 demo（合成报销域，30 条任务）")
    print("=" * 68)
    print(f"与正向规则引擎一致：{len(tasks) - len(mismatches)}/{len(tasks)}"
          f"{'（不一致：' + ','.join(mismatches) + '）' if mismatches else ''}")
    print(f"确定否定（判定「不违规」）：{clean} 条")
    print(f"字段读取：逆向 {reads} 次 / 不剪枝基线 {baseline * len(tasks)} 次"
          f"（省 {1 - reads / (baseline * len(tasks)):.0%}）")
    print(f"R2 的展开：{describe(engine.store.get('R2').condition)}")

    print("\n部分信息下的三态：")
    for amount, note in ((3000, "R1 可由金额直接证伪"), (8000, "R1 需要审批字段")):
        data = {"amount": amount, "has_director_approval": None,
                "city_tier": 1, "hotel_per_night": 500, "nights": 1,
                "transport_class": "经济舱", "meal_days": 2, "meal_total": 150,
                "invoice_dates": ["2026-01-01"], "trip_start": "2026-01-01",
                "trip_end": "2026-01-02", "submitted_month": "2026-01",
                "has_note": False, "same_day_dup": False}
        v = check(engine.store.get("R1"), data)
        print(f"  amount={amount}、审批缺失 → R1 {v.status:<8} 读取 {v.reads} 次"
              f"  缺失字段 {list(v.missing) or '无'}   （{note}）")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="跑合成域演示（默认行为）")
    ap.parse_args()
    demo()