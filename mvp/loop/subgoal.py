"""L1 子目标层（最小实现）：局部重分解，不惊动上层。

对齐契约：

  §1   L1 执行子目标，失败时局部重分解；交棒条件是重分解深度达 D1。
  §2   L1 只读（继承父的 vid），可写失败日志（append-only）。
  §5   允许重分解，但必须是确定性规则：同失败 → 同分支（备选按固定顺序试）。

这里只做控制流，不做模型调用——action 是任意返回 bool 的可调用对象。
失败尝试逐条记进 `events`（append-only 的载体）；要不要写负证据由上层决定，
L1 不自己改世界。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

PASS = "pass"
HANDOFF = "handoff"


@dataclass(frozen=True)
class SubGoal:
    """一个子目标：先试 action，失败再按固定顺序试 alternatives（局部重分解）。"""
    id: str
    action: Callable[[], bool]
    alternatives: tuple[Callable[[], bool], ...] = ()


@dataclass
class SubGoalResult:
    goal_id: str
    outcome: str            # pass / handoff
    attempts: int
    branch: int             # 成功的分支序号；handoff 时为 -1
    events: list[dict] = field(default_factory=list)


def run_subgoal(goal: SubGoal, max_depth: int = 1) -> SubGoalResult:
    """先按原方案，再按固定顺序尝试重分解分支；都不行就交棒。"""
    events: list[dict] = []
    attempts = 1
    if goal.action():
        events.append({"branch": 0, "ok": True})
        return SubGoalResult(goal.id, PASS, attempts, 0, events)

    events.append({"branch": 0, "ok": False})
    # 深度上限约束的是「能不能重分解」，不是候选个数：D1≥1 时本层候选按序全试
    alternatives = goal.alternatives if max_depth >= 1 else ()
    for index, alternative in enumerate(alternatives, start=1):
        attempts += 1
        ok = alternative()
        events.append({"branch": index, "ok": ok})
        if ok:
            return SubGoalResult(goal.id, PASS, attempts, index, events)

    return SubGoalResult(goal.id, HANDOFF, attempts, -1, events)


def run_parent(goals: list[SubGoal], l2: Callable[[], bool],
               max_depth: int = 1) -> dict:
    """执行一层子目标。全部通过 → 不惊动 L2；有交棒 → 才调用 L2。"""
    results = [run_subgoal(g, max_depth=max_depth) for g in goals]
    if all(r.outcome == PASS for r in results):
        return {"outcome": PASS, "l2_called": False, "results": results}
    ok = l2()
    return {"outcome": PASS if ok else "fail", "l2_called": True, "results": results}