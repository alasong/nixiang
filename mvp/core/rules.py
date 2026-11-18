"""规则库：把 7 条硬编码规则变成可查询的数据。

依据 v0.1 §3 的 L2 规则层（事实库 · 规则库 · 候选池 · 验证器 · 仲裁）。这里先落
规则库本身：条件是 AND-OR 树，叶子是 Fact —— 每个 Fact 声明自己依赖哪些字段、
怎么判。字段依赖是显式的，逆向展开才能知道「要证这条，需要取哪几个事实」。

**一致性由测试兜底**：同一批任务上，RuleStore 的结论必须逐条等于
`tasks.ground_truth`。谁改坏了语义，`test_backward` 立刻变红。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Mapping

TRUE = "true"
FALSE = "false"
UNKNOWN = "unknown"

CAPACITY = 300      # 单域规则库上限（双引擎 v0.1 §7）：超限必须先做退役评估
MIN_SUPPORT = 5     # candidate → active 的硬条件


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


class RuleError(RuntimeError):
    """规则生命周期操作被拒。宁可直接拒绝，也不让不合格规则进 active。"""


@dataclass(frozen=True)
class Rule:
    id: str
    desc: str
    condition: object
    status: str = "active"          # candidate | active | retired
    source: str = "human"           # "human" | "llm:<trace_id>"
    confidence: float = 1.0         # 来自回放统计
    hit_count: int = 0
    fail_count: int = 0


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
    """规则库：查询 + 生命周期。

    引擎只看到 active；candidate 与 retired 保留可查，不参与推理。
    迁移条件与容量上限在这里强制，调用方不能「先放进去再说」。
    """

    def __init__(self, rules: tuple[Rule, ...] = RULES,
                 capacity: int = CAPACITY) -> None:
        self._rules = {r.id: r for r in rules}
        self.capacity = capacity

    # -- 查询（引擎侧）

    def all(self) -> tuple[Rule, ...]:
        """活跃规则。candidate / retired 不参与推理。"""
        return tuple(r for r in self._rules.values() if r.status == "active")

    def get(self, rule_id: str) -> Rule:
        """按 id 查任意状态。要判「能不能用」看 status，不要只看 get 成功。"""
        return self._rules[rule_id]

    def ids(self) -> tuple[str, ...]:
        return tuple(r.id for r in self.all())

    def candidates(self) -> tuple[Rule, ...]:
        return tuple(r for r in self._rules.values() if r.status == "candidate")

    def retired(self) -> tuple[Rule, ...]:
        return tuple(r for r in self._rules.values() if r.status == "retired")

    def active_count(self) -> int:
        return len(self.all())

    # -- 生命周期（外环写，内环只读）

    def register(self, rule: Rule, source: str = "human") -> Rule:
        """登记为候选。同名 id 不允许覆盖，避免悄悄替换在用的规则。"""
        if rule.id in self._rules:
            raise RuleError(f"规则 {rule.id} 已存在，不能重复登记")
        stored = replace(rule, status="candidate", source=source)
        self._rules[rule.id] = stored
        return stored

    def activate(self, rule_id: str, support: int, counter: int) -> Rule:
        """candidate → active：§7 硬条件 + 容量上限。"""
        rule = self._require(rule_id)
        if rule.status != "candidate":
            raise RuleError(f"规则 {rule_id} 当前状态 {rule.status}，不是 candidate")
        if support < MIN_SUPPORT:
            raise RuleError(f"支持样本 {support} < {MIN_SUPPORT}，不能激活")
        if counter != 0:
            raise RuleError(f"有 {counter} 条误伤记录，不能激活")
        if self.active_count() >= self.capacity:
            raise RuleError(f"容量已满（{self.capacity}），先做退役评估再激活")
        updated = replace(rule, status="active", hit_count=support)
        self._rules[rule_id] = updated
        return updated

    def retire(self, rule_id: str, reason: str = "") -> Rule:
        """retired 不是删除：记录保留，随时可查「当时为什么下线」。"""
        rule = self._require(rule_id)
        updated = replace(rule, status="retired")
        self._rules[rule_id] = updated
        return updated

    def _require(self, rule_id: str) -> Rule:
        try:
            return self._rules[rule_id]
        except KeyError:
            raise RuleError(f"规则 {rule_id} 不存在") from None


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