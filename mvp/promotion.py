"""自动晋升：把经得起重复检验的经验从 scored 升成 decisive。

判据（可被调用方覆盖，默认值写死在这里）：
  同一经验至少被使用 min_uses 次；至少这么多使用里全部通过；且没有失败记录。

为什么用"使用记录"而不是"验证器复判"：调研类任务没有 decisive 验证器，
能拿到的证据只有「多次独立使用都没出错」。这条判据可审计、可回放，
比人工拍一个 0.95 的置信度强。

晋升动作 = 追加一条 decisive 经验 + 提交新快照，快照里写明依据。
"""
from __future__ import annotations

from dataclasses import dataclass

from .contracts import Kind, Status, Verdict
from .snapshot import Snapshot, SnapshotStore
from .store import ExperienceItem, ExperienceStore

MIN_USES = 2
MIN_PASS_RATE = 1.0


@dataclass(frozen=True)
class Decision:
    shot_id: str
    uses: int
    passed: int
    promoted: bool
    reason: str


def evaluate(store: ExperienceStore, usage: list[dict],
             min_uses: int = MIN_USES,
             min_pass_rate: float = MIN_PASS_RATE) -> list[Decision]:
    """按使用记录判定每条非 decisive 经验能否晋升。"""
    history: dict[str, list[str]] = {}
    for rec in usage:
        for shot_id in rec.get("shot_ids", []):
            history.setdefault(shot_id, []).append(rec.get("outcome", "unknown"))

    decisions: list[Decision] = []
    for item in store.all_items():
        shot_id = item.shot.id
        if item.verdict.kind == Kind.DECISIVE:
            continue
        outcomes = history.get(shot_id, [])
        passed = sum(1 for o in outcomes if o == "pass")
        rate = passed / len(outcomes) if outcomes else 0.0
        enough = len(outcomes) >= min_uses
        clean = passed == len(outcomes)
        ok = enough and clean and rate >= min_pass_rate

        if ok:
            reason = f"{passed}/{len(outcomes)} 次复用通过"
        elif not enough:
            reason = f"使用 {len(outcomes)} 次 < {min_uses}"
        else:
            reason = f"{len(outcomes) - passed} 次失败，通过率 {rate:.0%}"
        decisions.append(Decision(shot_id, len(outcomes), passed, ok, reason))
    return decisions


def apply_promotions(decisions: list[Decision], store: ExperienceStore,
                     sink, snapshots: SnapshotStore, verifier_vid: str,
                     ttl_days: int = 30) -> tuple[list[ExperienceItem], Snapshot | None]:
    """把判定为可晋升的经验落成 decisive 条目并提交新快照。"""
    by_id = {it.shot.id: it for it in store.all_items()}
    targets = [d for d in decisions if d.promoted and d.shot_id in by_id]
    if not targets:
        return [], None

    snap = snapshots.commit(
        verifier_vid=verifier_vid,
        experience_ids=[d.shot_id for d in targets],
        ttl_days=ttl_days,
        source="auto-promotion: " + ", ".join(f"{d.shot_id}({d.reason})" for d in targets),
    )
    promoted: list[ExperienceItem] = []
    for d in targets:
        src = by_id[d.shot_id]
        item = ExperienceItem(
            shot=src.shot,
            trace_id=src.trace_id,
            snapshot_vid=snap.vid,
            verdict=Verdict(Status.PASS, Kind.DECISIVE, 1.0, f"自动晋升：{d.reason}"),
        )
        store.add(item)
        sink.append_experience(item)
        promoted.append(item)
    return promoted, snap