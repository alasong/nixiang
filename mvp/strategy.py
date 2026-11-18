"""策略记录（T20）与历史成功率估计（T23）。

shape：验收命令的形状（原子类型 + 路径槽位），同形状跨路径/内容可复用。
记录：每试一个 OR 分支就记一行成功/失败；下次同形状把成功率最高的分支放前面。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .backward_plan import split_conjunction

_LAST_TOKEN = re.compile(r"\s(\S+)\s*$")


def _kind(atom: str) -> str:
    if atom.startswith("grep"):
        return "grep"
    if atom.startswith("test -f"):
        return "test-f"
    return "other"


def _path(atom: str) -> str:
    match = _LAST_TOKEN.search(atom)
    return match.group(1) if match else atom


def shape_of(verify: str) -> tuple[str, ...]:
    """验收形状：不变量是原子类型 + 路径是否复用，不是具体路径/内容。"""
    slots: dict[str, str] = {}
    shape: list[str] = []
    for atom in split_conjunction(verify):
        atom = atom.strip()
        path = _path(atom)
        slot = slots.setdefault(path, f"P{len(slots) + 1}")
        shape.append(f"{_kind(atom)}:{slot}")
    return tuple(shape)


@dataclass
class Stats:
    attempts: int = 0
    success: int = 0

    @property
    def p(self) -> float:
        return self.success / self.attempts if self.attempts else 0.5


class StrategyStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _key(self, shape: tuple[str, ...]) -> str:
        return "|".join(shape)

    def record(self, shape: tuple[str, ...], branch: str, ok: bool) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"shape": self._key(shape), "branch": branch, "ok": bool(ok)}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def stats(self, shape: tuple[str, ...]) -> dict[str, Stats]:
        key = self._key(shape)
        out: dict[str, Stats] = {}
        if not self.path.exists():
            return out
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("shape") != key:
                continue
            item = out.setdefault(rec["branch"], Stats())
            item.attempts += 1
            item.success += 1 if rec.get("ok") else 0
        return out

    def best(self, shape: tuple[str, ...]) -> str | None:
        stats = self.stats(shape)
        if not stats:
            return None
        return max(stats, key=lambda name: (stats[name].p,
                                            stats[name].attempts, name))

    def p(self, shape: tuple[str, ...], branch: str) -> float:
        return self.stats(shape).get(branch, Stats()).p
