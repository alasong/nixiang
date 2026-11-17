"""经验库与轨迹记录。

契约 3.3：经验按置信分层，低置信层只做审计与重判，不参与检索。
MVP 阶段经验是手工建的，全部标记为 decisive + 高置信。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import Status, Trace, Verdict
from .llm import Shot


@dataclass
class ExperienceItem:
    shot: Shot
    trace_id: str
    snapshot_vid: str
    verdict: Verdict
    logged_at: str = ""          # ISO 时间；空表示旧记录，时间规则跳过它


class ExperienceStore:
    """经验库。MVP 只实现高置信层的读写，分层接口保留。"""

    def __init__(self) -> None:
        self._items: list[ExperienceItem] = []

    def add(self, item: ExperienceItem) -> None:
        """契约 3.3：写入必须带 (trace_id, snapshot_vid, verdict) 三元组。"""
        self._items.append(item)

    def all_items(self) -> list[ExperienceItem]:
        return list(self._items)

    def high_confidence(self) -> list[ExperienceItem]:
        return [
            it for it in self._items
            if it.verdict.kind.value == "decisive" and it.verdict.status == Status.PASS
        ]

    def retrieve(self, scene_tag: str, k: int = 2) -> list[Shot]:
        """按场景标签检索。只读输入特征，不读答案。

        排序规则：标签完全相同优先，同城市等级次之，其余不取。
        检索不到返回空集，调用方必须能处理零样本（契约 3.3）。
        """
        tier = scene_tag.split("-")[0]
        scored: list[tuple[int, ExperienceItem]] = []
        for it in self.high_confidence():
            tag = it.shot.scene_tag
            if tag == scene_tag:
                scored.append((2, it))
            elif tag.split("-")[0] == tier:
                scored.append((1, it))
        scored.sort(key=lambda x: -x[0])
        return [it.shot for _, it in scored[:k]]

    def stats(self) -> dict:
        return {
            "total": len(self._items),
            "high_conf": len(self.high_confidence()),
        }


@dataclass
class Recorder:
    """轨迹记录器。契约 §2：append-only，不修改已有记录。"""
    traces: list[Trace] = field(default_factory=list)

    def add(self, trace: Trace) -> None:
        self.traces.append(trace)

    def stats(self) -> dict:
        if not self.traces:
            return {"n": 0}
        ok = sum(1 for t in self.traces if t.outcome == Status.PASS)
        cost = sum(t.total_cost for t in self.traces)
        attempts = sum(t.attempts for t in self.traces)
        return {
            "n": len(self.traces),
            "success": ok,
            "success_rate": round(ok / len(self.traces), 4),
            "total_cost": cost,
            "avg_cost": round(cost / len(self.traces), 1),
            "avg_attempts": round(attempts / len(self.traces), 2),
        }
