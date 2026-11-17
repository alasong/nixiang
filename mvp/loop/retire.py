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
from datetime import datetime, timezone

from ..core.contracts import Kind

MIN_USES = 2
MAX_FAIL_RATE = 0.3
STALE_DAYS = 90


def _age_days(stamp: str, now: datetime) -> int | None:
    try:
        when = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (now - when).days


@dataclass(frozen=True)
class Decision:
    shot_id: str
    uses: int
    failed: int
    retire: bool
    reason: str


def evaluate(store, usage: list[dict] | None = None, min_uses: int = MIN_USES,
             max_fail_rate: float = MAX_FAIL_RATE,
             stale_days: int = STALE_DAYS,
             now: datetime | None = None) -> list[Decision]:
    """两条淘汰判据：

      1. 失败率：使用 ≥ min_uses 且失败率 ≥ max_fail_rate
      2. 时间：decisive 且**从未被使用**，沉淀已超 stale_days

    时间规则刻意保守：只杀「一次都没被用过」的。低频但正确的经验若按
    “最近使用距今”杀，会被误伤。
    """
    usage = usage or []
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
        fail_rule = uses >= min_uses and rate >= max_fail_rate

        age_rule = False
        age = _age_days(item.logged_at, now or datetime.now(timezone.utc)) \
            if item.logged_at else None
        if (not fail_rule and uses == 0 and age is not None
                and item.verdict.kind == Kind.DECISIVE and age >= stale_days):
            age_rule = True

        ok = fail_rule or age_rule
        if fail_rule:
            reason = f"{failed}/{uses} 次复用失败（{rate:.0%} ≥ {max_fail_rate:.0%}）"
        elif age_rule:
            reason = f"沉淀 {age} 天从未被使用（≥ {stale_days} 天）"
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