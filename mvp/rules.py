"""规则库：把 7 条硬编码规则变成可查询的数据。

依据 v0.1 §3 的 L2 规则层（事实库 · 规则库 · 候选池 · 验证器 · 仲裁）。这里先落
规则库本身：条件是 AND-OR 树，叶子是 Fact —— 每个 Fact 声明自己依赖哪些字段、
怎么判。字段依赖是显式的，逆向展开才能知道「要证这条，需要取哪几个事实」。

**一致性由测试兜底**：同一批任务上，RuleStore 的结论必须逐条等于
`tasks.ground_truth`。谁改坏了语义，`test_backward` 立刻变红。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

TRUE = "true"
FALSE = "false"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Fact:
    """叶子条件：依赖 fields 里的字段，全部取到才求值。"""
    name: str
    fields: tuple[str, ...]
    test: Callable[[Mapping[str, object]], bool]

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class All:
    """合取：全部成立才算成立。"""
    children: tuple
    name: str = "AND"


@dataclass(frozen=True)
class Any:
    """析取：任一成立即成立（分支）。"""
    children: tuple
    name: str = "OR"


@dataclass(frozen=True)
class Rule:
    id: str
    desc: str
    condition: object


def _fact(name: str, fields: tuple[str, ...]) -> Callable[..., Callable]:
    def deco(fn: Callable[[Mapping[str, object]], bool]) -> Fact:
        return Fact(name, fields, fn)
    return deco


R1 = Rule("R1", "单笔报销超过 5000 元且无总监审批", All((
    _fact("金额>5000", ("amount",))(lambda f: f["amount"] > 5000),
    _fact("无总监审批", ("has_director_approval",))(lambda f: not f["has_director_approval"]),
)))

R2 = Rule("R2", "住宿单价超城市上限", Any((
    All((_fact("一线城市", ("city_tier",))(lambda f: f["city_tier"] == 1),
         _fact("单价>600", ("hotel_per_night",))(lambda f: f["hotel_per_night"] > 600))),
    All((_fact("非一线", ("city_tier",))(lambda f: f["city_tier"] != 1),
         _fact("单价>400", ("hotel_per_night",))(lambda f: f["hotel_per_night"] > 400))),
)))

R3 = Rule("R3", "交通舱位非经济舱或二等座",
           _fact("舱位不合规", ("transport_class",))(
               lambda f: f["transport_class"] not in ("经济舱", "二等座")))

R4 = Rule("R4", "餐补超过 100 元/天",
           _fact("餐补超限", ("meal_total", "meal_days"))(
               lambda f: f["meal_total"] > 100 * f["meal_days"]))

R5 = Rule("R5", "发票日期不在行程内",
           _fact("有发票越界", ("invoice_dates", "trip_start", "trip_end"))(
               lambda f: any(d < f["trip_start"] or d > f["trip_end"]
                             for d in f["invoice_dates"])))

R6 = Rule("R6", "同日重复提交",
           _fact("同日重复", ("same_day_dup",))(lambda f: bool(f["same_day_dup"])))

R7 = Rule("R7", "跨月报销且无说明", All((
    _fact("跨月", ("submitted_month", "trip_start"))(
        lambda f: f["submitted_month"] != f["trip_start"][:7]),
    _fact("无说明", ("has_note",))(lambda f: not f["has_note"]),
)))

RULES: tuple[Rule, ...] = (R1, R2, R3, R4, R5, R6, R7)


class RuleStore:
    """只读规则库。按 id 查询；按规则取字段依赖（给成本估计用）。"""

    def __init__(self, rules: tuple[Rule, ...] = RULES) -> None:
        self._rules = {r.id: r for r in rules}

    def all(self) -> tuple[Rule, ...]:
        return tuple(self._rules.values())

    def get(self, rule_id: str) -> Rule:
        return self._rules[rule_id]

    def ids(self) -> tuple[str, ...]:
        return tuple(self._rules)


def rule_field_reads(rule: Rule) -> int:
    """不短路时的读取次数上界 = 叶子里字段出现的总次数。

    OR 分支各自持有一份条件，共享字段会被重复读——所以上界不能按去重后的
    字段数算，否则 R2 这类规则会显得「越界」。
    """
    total = 0

    def walk(node) -> None:
        nonlocal total
        if isinstance(node, Fact):
            total += len(node.fields)
        else:
            for child in node.children:
                walk(child)

    walk(rule.condition)
    return total


def rule_fields(rule: Rule) -> tuple[str, ...]:
    """一条规则依赖的全部字段（按出现顺序去重）。逆向展开的输入。"""
    seen: list[str] = []

    def walk(node) -> None:
        if isinstance(node, Fact):
            for f in node.fields:
                if f not in seen:
                    seen.append(f)
        else:
            for child in node.children:
                walk(child)

    walk(rule.condition)
    return tuple(seen)