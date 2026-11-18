"""AND-OR 图 + 证明树 + priority(n)（T19）。

- AND：验收里的合取原子；
- OR：同一组目标的不同做法（朴素全写 / 同路径先写后追加）；
- priority：1/cost，cost 小的先试（朴素 1.0、回退 2.0）；
- proof：记录试了哪些 OR、哪个被选中。
"""
from __future__ import annotations

from dataclasses import dataclass

from .backward_plan import plan_variants
from .handle import SubGoal


@dataclass(frozen=True)
class Branch:
    name: str
    subgoals: tuple[SubGoal, ...]
    cost: float

    @property
    def priority(self) -> float:
        return 1.0 / self.cost


@dataclass(frozen=True)
class Plan:
    branches: tuple[Branch, ...]
    depth: int
    atoms: int


def build_plan(verify: str, depth: int = 1) -> Plan | None:
    variants = plan_variants(verify, depth=depth)
    if not variants:
        return None
    names = ("naive", "dedup")
    costs = (1.0, 2.0)
    branches = [
        Branch(names[index] if index < len(names) else f"v{index}",
               tuple(variant),
               costs[index] if index < len(costs) else 1.0 + index)
        for index, variant in enumerate(variants)
    ]
    branches.sort(key=lambda branch: (-branch.priority, branch.name))
    return Plan(branches=tuple(branches), depth=depth,
                atoms=len(variants[0]))


def render_plan(plan: Plan, outcomes: list[bool],
                stats: dict[str, float] | None = None) -> str:
    lines = [f"plan: AND({plan.atoms} atoms) depth={plan.depth}"]
    chosen = False
    for index, (branch, ok) in enumerate(zip(plan.branches, outcomes), 1):
        status = "pass" if ok else "fail"
        if ok and not chosen:
            status += " (chosen)"
            chosen = True
        p_text = ""
        if stats is not None and branch.name in stats:
            p_text = f" p={stats[branch.name]:.2f}"
        lines.append(f"  OR[{index}] {branch.name} cost={branch.cost:.1f} "
                     f"priority={branch.priority:.2f}{p_text} -> {status}")
    lines.append(f"proof: atoms={plan.atoms} "
                 f"or_branches={len(plan.branches)} depth={plan.depth}")
    return "\n".join(lines)
