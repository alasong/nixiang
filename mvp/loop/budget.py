"""预算守恒：一张树，单向扣减，禁止透支，未用完归还。

对齐契约 §4：

    budget(L2) ⊇ Σ budget(L1) ⊇ Σ budget(L0)

  - 单池记账：只在这棵树上记账，不许可各层各记一份
  - 自下扣减：叶子花掉的额度同时记到所有祖先，父池永远知道真实消耗
  - 禁止透支：任何一次扣减让某个祖先超额，直接拒绝（不是"先花再算"）
  - 超支归还：子池没用完的额度可以 release 回父池，父池可再切分

两个量要分清：
    reserved   已切给子池的额度，切出去就不能再切（守恒的来源）
    spent      实际消耗，从叶子向上累积（自下扣减的来源）
"""
from __future__ import annotations

from dataclasses import dataclass, field


class Overdraft(RuntimeError):
    """扣减会让某个池超额。宁可直接拒绝，也不允许事后对账。"""


@dataclass
class Budget:
    name: str
    limit: int
    spent: int = 0
    reserved: int = 0
    parent: "Budget | None" = None
    children: list["Budget"] = field(default_factory=list)
    released: bool = False

    @property
    def free(self) -> int:
        """还能切给子池的额度。切分只能从这里出，保证守恒。"""
        return self.limit - self.reserved

    @property
    def remaining(self) -> int:
        return self.limit - self.spent

    def spend(self, amount: int) -> None:
        """自下扣减：先检查整条祖先链，全部允许才记。"""
        if amount < 0:
            raise ValueError("金额不能为负")
        node: Budget | None = self
        while node is not None:
            if node.spent + amount > node.limit:
                raise Overdraft(
                    f"池 {node.name} 超额：已用 {node.spent} + {amount} > {node.limit}")
            node = node.parent
        node = self
        while node is not None:
            node.spent += amount
            node = node.parent

    def split(self, weights: list[float]) -> list["Budget"]:
        """按权重切分子池。总额严格等于当前空闲额度（余数给最后一个）。"""
        if not weights or min(weights) < 0:
            raise ValueError("权重必须非空且非负")
        free = self.free
        if free <= 0:
            raise Overdraft(f"池 {self.name} 没有空闲额度可切分")
        total = sum(weights)
        children: list[Budget] = []
        used = 0
        for index, weight in enumerate(weights):
            if index == len(weights) - 1:
                amount = free - used          # 余数给最后一个，避免零头浪费
            else:
                amount = int(free * weight / total) if total else 0
            child = Budget(f"{self.name}.{index}", amount, parent=self)
            used += amount
            children.append(child)
        self.reserved += used
        self.children.extend(children)
        return children

    def release(self) -> int:
        """把本池未用完的额度归还父池，返回归还数量。"""
        if self.parent is None or self.released:
            return 0
        self.released = True
        self.parent.reserved -= self.limit
        return self.limit - self.spent


def demo() -> None:
    root = Budget("L2", 1000)
    l1a, l1b = root.split([0.7, 0.3])
    leaf1, leaf2 = l1a.split([0.5, 0.5])
    print("预算切分与守恒（契约 §4）")
    print("=" * 62)
    print(f"L2 {root.limit} → L1: {l1a.limit} + {l1b.limit}"
          f"（reserved {root.reserved}，守恒 ≤ {root.limit}）")
    print(f"L1a {l1a.limit} → 叶子: {leaf1.limit} + {leaf2.limit}")

    leaf1.spend(120)
    print(f"叶子花 120 → L1a 已用 {l1a.spent}，L2 已用 {root.spent}（自下扣减）")

    try:
        leaf2.spend(leaf2.limit + 1)
    except Overdraft as e:
        print(f"叶子超自身额度 → 拒：{e}")

    l1b.spend(250)
    try:
        l1a.spend(l1a.remaining + 10)       # 父池额度不足，哪怕叶子还有空间
    except Overdraft as e:
        print(f"父池额度不足 → 拒：{e}")

    back = l1b.release()
    print(f"L1b 归还 {back}（未用额度回到 L2，可再切分）")
    (again,) = root.split([1.0])
    print(f"L2 重新切出 {again.limit}")

    print(f"\n终态：L2 已用 {root.spent} / {root.limit}，"
          f"空闲 {root.free}，切出合计 {root.reserved}")


if __name__ == "__main__":
    demo()