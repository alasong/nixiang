"""路由：只读聚合统计，决定一个任务族走哪条路径。

契约 §2 对路由的约束是「只读聚合统计，不读内容」——它们看「这个族有多少条
成功轨迹」，不看「某条轨迹长什么样」。所以这里只用 scene_tag、usage 结果、
成本计数，不碰经验文本。

路由只有三种结论：
    hit      该族复用历史可靠，走内环命中
    disable  该族复用失败率高，停用复用，回模型并告警
    model    样本不足或未沉淀，走模型（不凭一两次结果就切路径）

阈值是拍的（min_uses=3 / min_pass_rate=0.9），属契约 §7 的 B 级参数。
"""
from __future__ import annotations

from dataclasses import dataclass

MIN_USES = 3
MIN_PASS_RATE = 0.9


@dataclass(frozen=True)
class FamilyStats:
    tag: str
    uses: int
    passed: int

    @property
    def pass_rate(self) -> float:
        return self.passed / self.uses if self.uses else 0.0


@dataclass(frozen=True)
class RouteDecision:
    tag: str
    path: str
    reason: str
    stats: FamilyStats


def aggregate(usage: list[dict], store) -> dict[str, FamilyStats]:
    """按族聚合使用结果。usage 只带 shot_id，族名从经验库里查，不读文本。"""
    tag_of = {it.shot.id: it.shot.scene_tag for it in store.all_items()}
    buckets: dict[str, list[str]] = {}
    for rec in usage:
        for shot_id in rec.get("shot_ids", []):
            tag = tag_of.get(shot_id, "unknown")
            buckets.setdefault(tag, []).append(rec.get("outcome", "unknown"))
    return {
        tag: FamilyStats(tag, len(outcomes),
                         sum(1 for o in outcomes if o == "pass"))
        for tag, outcomes in buckets.items()
    }


def route(stats: FamilyStats, min_uses: int = MIN_USES,
          min_pass_rate: float = MIN_PASS_RATE) -> RouteDecision:
    if stats.uses < min_uses:
        return RouteDecision(stats.tag, "model",
                             f"样本 {stats.uses} 次 < {min_uses}，不足以判断", stats)
    if stats.pass_rate < min_pass_rate:
        return RouteDecision(stats.tag, "disable",
                             f"通过率 {stats.pass_rate:.0%} < {min_pass_rate:.0%}，"
                             f"停用该族复用", stats)
    return RouteDecision(stats.tag, "hit",
                         f"{stats.passed}/{stats.uses} 次复用通过", stats)


def route_all(usage: list[dict], store) -> list[RouteDecision]:
    return [route(s) for s in sorted(aggregate(usage, store).values(),
                                     key=lambda s: s.tag)]