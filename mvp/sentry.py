"""哨兵：主动探针，抓「渐进失效」。

融合系统的失效不报错：命中率慢慢掉、负证据慢慢涨、经验写进去没人用。
等到指标崩了再查，已经晚了几周。所以这里只做只读巡检，把可疑状态列成告警。

告警不是结论，只是「该人看一眼」的入口。等级：
    critical  已经在错（复用失败率高、快照过期还在用）
    warn      趋势不对（负证据占比高、经验从未被使用）
"""
from __future__ import annotations

from dataclasses import dataclass

from .snapshot import SnapshotStore

NEGATIVE_WARN = 0.2          # 负证据占轨迹比例
USAGE_FAIL_CRITICAL = 0.1    # 复用失败率


@dataclass(frozen=True)
class Alarm:
    level: str
    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level:>8}] {self.code}: {self.message}"


def inspect(sink, snapshots: SnapshotStore) -> list[Alarm]:
    """对一份运行目录做巡检。只读，不修数据。"""
    alarms: list[Alarm] = []

    latest = snapshots.latest()
    if latest is not None and latest.is_expired():
        alarms.append(Alarm("critical", "snapshot_expired",
                            f"最新快照 {latest.vid} 已过期（{latest.created_at}，"
                            f"TTL {latest.ttl_days} 天），继续复用等于用陈旧结论"))

    counts = sink.counts()
    if counts["traces"] and counts["negative"] / counts["traces"] > NEGATIVE_WARN:
        rate = counts["negative"] / counts["traces"]
        alarms.append(Alarm("warn", "negative_rate",
                            f"负证据 {counts['negative']}/{counts['traces']} "
                            f"（{rate:.0%}）超过 {NEGATIVE_WARN:.0%}"))

    usage = sink.load_usage()
    failed = [u for u in usage if u.get("outcome") != "pass"]
    if usage and len(failed) / len(usage) > USAGE_FAIL_CRITICAL:
        alarms.append(Alarm("critical", "usage_fail_rate",
                            f"复用失败 {len(failed)}/{len(usage)} "
                            f"（{len(failed) / len(usage):.0%}）"))

    used = {sid for u in usage for sid in u.get("shot_ids", [])}
    idle = [it.shot.id for it in sink.load_experience().high_confidence()
            if it.shot.id not in used]
    if idle:
        alarms.append(Alarm("warn", "dormant_experience",
                            f"{len(idle)} 条高置信经验从未被使用：{', '.join(sorted(idle))}"))

    if counts["experience"] and not usage:
        alarms.append(Alarm("warn", "pipeline_gap",
                            "经验库非空但没有任何复用记录——沉淀了，没接上"))

    return alarms