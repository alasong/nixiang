"""快照实体。

契约 §3.1 要求快照是「事实库 + 规则库 + 验证器版本」的不可变组合，并带 vid。
MVP 阶段还没事实库/规则库，这里先落最小可用形态：一次提交 = 验证器版本 +
经验 id 集合 + 时效。`snapshot_vid` 从此不再是一个裸字符串。

不可变：提交后文件不重写，新状态只能生成新 vid。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Snapshot:
    vid: str
    verifier_vid: str
    experience_ids: tuple[str, ...] = ()
    created_at: str = ""
    ttl_days: int = 30
    source: str = ""

    def expires_at(self) -> datetime:
        created = datetime.fromisoformat(self.created_at)
        return created + timedelta(days=self.ttl_days)

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or _now()) >= self.expires_at()

    def to_dict(self) -> dict:
        return {
            "vid": self.vid,
            "verifier_vid": self.verifier_vid,
            "experience_ids": list(self.experience_ids),
            "created_at": self.created_at,
            "ttl_days": self.ttl_days,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Snapshot":
        return cls(
            vid=d["vid"],
            verifier_vid=d.get("verifier_vid", ""),
            experience_ids=tuple(d.get("experience_ids") or ()),
            created_at=d.get("created_at", ""),
            ttl_days=int(d.get("ttl_days", 30)),
            source=d.get("source", ""),
        )


class SnapshotStore:
    """快照目录。提交即新 vid，旧文件不改。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, vid: str) -> Path:
        return self.root / f"{vid}.json"

    def list(self) -> list[Snapshot]:
        snaps = [Snapshot.from_dict(json.loads(p.read_text(encoding="utf-8")))
                 for p in sorted(self.root.glob("v*.json"))]
        return sorted(snaps, key=lambda s: s.created_at or "")

    def commit(self, verifier_vid: str, experience_ids: list[str],
               ttl_days: int = 30, source: str = "") -> Snapshot:
        """生成下一个 vid 并落盘。vid 单调递增，永不复用。"""
        existing = [int(p.stem[1:]) for p in self.root.glob("v*.json")
                    if p.stem[1:].isdigit()]
        vid = f"v{max(existing, default=0) + 1}"
        snap = Snapshot(
            vid=vid,
            verifier_vid=verifier_vid,
            experience_ids=tuple(sorted(experience_ids)),
            created_at=_now().isoformat(timespec="seconds"),
            ttl_days=ttl_days,
            source=source,
        )
        self._path(vid).write_text(
            json.dumps(snap.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8")
        return snap

    def latest(self) -> Snapshot | None:
        snaps = self.list()
        return snaps[-1] if snaps else None

    def load(self, vid: str) -> Snapshot:
        return Snapshot.from_dict(json.loads(self._path(vid).read_text(encoding="utf-8")))