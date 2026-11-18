"""真实模型小样本测量：同样任务下，1 份候选 vs N 份候选 + 评审选择。

只依赖可注入的 provider / judge：测试用 fake，真实读数用
`tools/real_measure.py`（DeepSeek，需要 key）。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .core.paths import ROOT, RUNS_DIR
from .core.persistence import RunStore
from .handle import TaskSpec, handle_candidates


@dataclass(frozen=True)
class Item:
    name: str
    task: str
    verify: str
    out: str


@dataclass
class Measurement:
    name: str
    single_pass: bool
    best_pass: bool
    single_score: int
    best_score: int
    best_index: int
    wall_ms: int


def measure(items: list[Item], provider, judge, n: int = 3,
            store: RunStore | None = None, cwd: str | Path = ROOT,
            timeout: float = 60.0) -> list[Measurement]:
    """每个任务生成 n 份候选，用 judge 打分；single=第 1 份，best=N 份里最高。"""
    if n < 1:
        raise ValueError("n 必须 ≥ 1")
    store = store or RunStore(RUNS_DIR / "measure")
    rows: list[Measurement] = []
    for item in items:
        contents = provider.generate(item.task, n)
        if not contents:
            raise ValueError(f"{item.name}：provider 没有返回候选")
        result = handle_candidates(
            TaskSpec(text=item.task, verify=item.verify), item.out, contents,
            judge=judge, store=store, cwd=cwd, timeout=timeout)
        first = next((step for step in result.trace.steps
                      if step.action == "candidate"), None)
        single_pass = bool(first is not None and first.output.get("rc") == 0)
        rows.append(Measurement(
            name=item.name,
            single_pass=single_pass,
            best_pass=result.passed,
            single_score=result.single_score,
            best_score=result.best_score,
            best_index=result.selected,
            wall_ms=result.wall_ms))
    return rows


def summarize(rows: list[Measurement]) -> dict:
    if not rows:
        return {"tasks": 0, "single_pass": 0, "best_pass": 0,
                "mean_delta": 0.0, "mean_wall_ms": 0.0}
    single_fail = sum(1 for row in rows if not row.single_pass)
    rescued = sum(1 for row in rows if not row.single_pass and row.best_pass)
    return {
        "tasks": len(rows),
        "single_pass": sum(1 for row in rows if row.single_pass),
        "best_pass": sum(1 for row in rows if row.best_pass),
        "single_fail": single_fail,
        "rescued": rescued,
        "rescue_rate": (rescued / single_fail) if single_fail else 0.0,
        "mean_delta": sum(row.best_score - row.single_score for row in rows) / len(rows),
        "mean_wall_ms": sum(row.wall_ms for row in rows) / len(rows),
    }