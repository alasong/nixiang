"""命令行入口：检查 README 与代码的一致性。

    python3 tools/check_docs.py     # 0 = 一致，1 = 有漂移
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mvp.doccheck import check  # noqa: E402


def main() -> None:
    problems = check(ROOT)
    if not problems:
        print("文档一致性：通过（测试数 / 入口 / make 目标）")
        return
    print(f"文档一致性：发现 {len(problems)} 处漂移")
    for problem in problems:
        print("  - " + problem)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
