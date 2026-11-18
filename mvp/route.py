"""在线路由（T25）：按任务与验收形状选执行路径。

规则（确定性，不调模型）：
  backward   验收可符号反推（grep -q / test -f / &&）
  decompose  任务是带 ## 子目标 的计划文件
  formalize  没有可反推形状、任务是自由文本（交给形式化）
  direct     其余（直接执行或报 unsupported）
"""
from __future__ import annotations

import argparse
import sys

from .backward_plan import plan_from_verify
from .core.paths import ROOT
from .handle import load_task_text, parse_task_file


def route_task(task_text: str, verify: str) -> str:
    if plan_from_verify(verify):
        return "backward"
    if parse_task_file(load_task_text(task_text)):
        return "decompose"
    if verify.strip() in ("", "true"):
        return "formalize"
    return "direct"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m mvp.route")
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args(argv)
    examples = [
        ("在 /tmp/x 写入 hi", "grep -q hi /tmp/x"),
        (str(ROOT / "tasks" / "T2.md"), "make docs-check"),
        ("用两三段介绍 nixiang", "true"),
    ]
    routes = []
    for task, verify in examples:
        route = route_task(task, verify)
        routes.append(route)
        print(f"  {route:10s} {task[:44]}")
    print("route=" + ",".join(routes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
