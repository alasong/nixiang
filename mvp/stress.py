"""跨域压测（M4）：多域任务 × 重复，输出可复现的通过数与墙钟。

范围要说清：这里换的是任务形状与验收命令（文档/数据/代码/文本/复合），
**不换领域知识、不测语义质量**。它回答"这套 harness 在不同任务形态上还转不转"，
不回答"写得好不好"。

用法：
    python3 -m mvp.stress --cases tasks/M4/cases.jsonl --repeat 3
退出码：全部通过 0，有失败 1。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from .core.paths import ROOT, RUNS_DIR
from .core.persistence import RunStore
from .handle import TaskSpec, handle

REQUIRED = ("name", "domain", "task", "verify")


@dataclass(frozen=True)
class Case:
    name: str
    domain: str
    task: str
    verify: str
    agents: int = 1


def load_cases(path: str | Path) -> list[Case]:
    """一行一个 case：name/domain/task/verify 必填，agents 可选。"""
    cases: list[Case] = []
    for line_no, line in enumerate(
            Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"第 {line_no} 行不是合法 JSON：{exc}") from exc
        missing = [key for key in REQUIRED if not obj.get(key)]
        if missing:
            raise ValueError(f"第 {line_no} 行缺字段：{', '.join(missing)}")
        cases.append(Case(
            name=str(obj["name"]), domain=str(obj["domain"]),
            task=str(obj["task"]), verify=str(obj["verify"]),
            agents=int(obj.get("agents", 1))))
    if not cases:
        raise ValueError("没有 case")
    return cases


def run(cases: list[Case], repeat: int = 1, store: RunStore | None = None,
        cwd: str | Path = ROOT, timeout: float = 60.0) -> list[tuple[Case, object]]:
    """逐个 case 重复跑；每次都走 handle（分解/执行/验收/轨迹）。"""
    if repeat < 1:
        raise ValueError("repeat 必须 ≥ 1")
    store = store or RunStore(RUNS_DIR / "stress")
    results: list[tuple[Case, object]] = []
    for case in cases:
        for _ in range(repeat):
            spec = TaskSpec(text=case.task, verify=case.verify)
            results.append((case, handle(spec, store=store, cwd=cwd,
                                         timeout=timeout,
                                         agents=max(1, case.agents))))
    return results


def summarize(results: list[tuple[Case, object]]) -> dict:
    return {
        "cases": len({case.name for case, _ in results}),
        "runs": len(results),
        "passed": sum(1 for _, result in results if result.passed),
        "failed": sum(1 for _, result in results if not result.passed),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m mvp.stress")
    parser.add_argument("--cases", required=True, help="JSONL：name/domain/task/verify")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--runs", default="", help="产物目录，缺省 runs/stress")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    store = RunStore(Path(args.runs) if args.runs else RUNS_DIR / "stress")
    results = run(cases, repeat=args.repeat, store=store, timeout=args.timeout)
    summary = summarize(results)

    print(f"跨域压测：cases={summary['cases']} runs={summary['runs']}")
    for index, (case, result) in enumerate(results, 1):
        mark = "pass" if result.passed else "FAIL"
        print(f"  {index:02d} {case.domain:6s} {case.name:12s} {mark:4s} "
              f"wall={result.wall_ms}ms agents={result.agents_used}")
    print(f"汇总 cases={summary['cases']} runs={summary['runs']} "
          f"passed={summary['passed']} failed={summary['failed']}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())