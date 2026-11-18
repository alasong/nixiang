"""质量漂移探针（T24）：重跑已知通过的任务，失败即漂移。

用途：验证器/规则/执行器改动后，确认原本通过的任务没有悄悄变红。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core.paths import ROOT, RUNS_DIR
from .core.persistence import RunStore
from .handle import TaskSpec, handle
from .stress import load_cases

DEFAULT_CASES = ROOT / "tasks" / "M4" / "cases.jsonl"


def check(cases, store: RunStore | None = None, cwd: str | Path = ROOT,
          timeout: float = 60.0) -> dict:
    store = store or RunStore(RUNS_DIR / "drift")
    rows: list[tuple[str, bool]] = []
    for case in cases:
        result = handle(TaskSpec(text=case.task, verify=case.verify),
                        store=store, cwd=cwd, timeout=timeout,
                        agents=max(1, case.agents))
        rows.append((case.name, result.passed))
    failed = [name for name, passed in rows if not passed]
    return {"total": len(rows), "failed": failed, "drift": len(failed)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m mvp.drift")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--runs", default="")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--demo", action="store_true",
                        help="用默认用例集跑一遍（等同上 --cases 默认值）")
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    store = RunStore(Path(args.runs) if args.runs else RUNS_DIR / "drift")
    report = check(cases, store=store, timeout=args.timeout)
    for name in report["failed"]:
        print(f"  drift: {name} 从通过变失败")
    print(f"drift={report['drift']}/{report['total']}")
    return 0 if report["drift"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
