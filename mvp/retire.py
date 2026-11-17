"""淘汰：把复用检验里表现不可靠的经验移出可用集合。

判据（数据驱动、可审计）：

    同一经验被使用 ≥ min_uses 次，且失败率 ≥ max_fail_rate  → 淘汰

动作 = 追加一条墓碑（`retired.jsonl`），**不改历史 experience 行**；
加载经验库时按墓碑过滤。淘汰不是删除，历史仍可回溯——这是 append-only
的代价，也是它的价值。

只按失败率判，不按时间判：`ExperienceItem` 没带时间字段，本轮不做「长期不用
自动回收」，避免用文件 mtime 冒充业务时间。
"""
from __future__ import annotations

from dataclasses import dataclass

MIN_USES = 2
MAX_FAIL_RATE = 0.3


@dataclass(frozen=True)
class Decision:
    shot_id: str
    uses: int
    failed: int
    retire: bool
    reason: str


def evaluate(store, usage: list[dict], min_uses: int = MIN_USES,
             max_fail_rate: float = MAX_FAIL_RATE) -> list[Decision]:
    history: dict[str, list[str]] = {}
    for rec in usage:
        for shot_id in rec.get("shot_ids", []):
            history.setdefault(shot_id, []).append(rec.get("outcome", "unknown"))

    decisions: list[Decision] = []
    for item in store.all_items():
        outcomes = history.get(item.shot.id, [])
        uses = len(outcomes)
        failed = sum(1 for o in outcomes if o != "pass")
        rate = failed / uses if uses else 0.0
        ok = uses >= min_uses and rate >= max_fail_rate
        if ok:
            reason = f"{failed}/{uses} 次复用失败（{rate:.0%} ≥ {max_fail_rate:.0%}）"
        elif uses < min_uses:
            reason = f"使用 {uses} 次 < {min_uses}，样本不足"
        else:
            reason = f"失败率 {rate:.0%} < {max_fail_rate:.0%}，保留"
        decisions.append(Decision(item.shot.id, uses, failed, ok, reason))
    return decisions


def apply(decisions: list[Decision], sink) -> list[str]:
    """写墓碑。淘汰后经验库加载时自然过滤，不重写历史。"""
    retired: list[str] = []
    for d in decisions:
        if not d.retire:
            continue
        sink.append_retire([d.shot_id], f"retire: {d.reason}")
        retired.append(d.shot_id)
    return retired