"""落地状态表的机检：表里引用的命令必须真的存在。

检查三类引用，全部能判 0/1：
  make <target>            Makefile 里有这个目标
  python3 -m <module>      模块文件存在（含 unittest 形式）
  python3 tools/<x>.py     工具脚本存在

不跑命令本身——跑全部证据太慢，存在性检查负责「表不撒谎」，实际读数由各命令
自己负责。这张表如果引用了一个不存在的入口，就是文档在骗人，直接判红。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "落地状态_2026-11.md"

MAKE_RE = re.compile(r"`make ([a-z][a-z-]*)[^`]*`")
MODULE_RE = re.compile(r"`python3 -m (?:unittest )?([a-z][a-z0-9_.]*)[^`]*`")
TOOL_RE = re.compile(r"(?:python3|\.venv/bin/python) (tools/[a-z0-9_]+\.py)")
MAKE_TARGET_RE = re.compile(r"^([a-z][a-z-]*):", re.M)


def tables(doc: Path) -> list[tuple[list[str], list[list[str]]]]:
    """按表切开：(表头, 数据行)。分隔线与两列表格跳过。"""
    out: list[tuple[list[str], list[list[str]]]] = []
    header: list[str] | None = None
    rows: list[list[str]] = []
    for line in doc.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            if header is not None:
                out.append((header, rows))
                header, rows = None, []
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3 or set(cells[0]) <= set("-: "):
            continue
        if header is None:
            header = cells
            continue
        rows.append(cells)
    if header is not None:
        out.append((header, rows))
    return out


def table_rows(doc: Path) -> list[list[str]]:
    return [row for _, rows in tables(doc) for row in rows]


def referenced_commands(cell: str) -> list[tuple[str, str]]:
    """(kind, name)：make / module / tool。"""
    found: list[tuple[str, str]] = []
    for match in MAKE_RE.finditer(cell):
        found.append(("make", match.group(1)))
    for match in MODULE_RE.finditer(cell):
        found.append(("module", match.group(1)))
    for match in TOOL_RE.finditer(cell):
        found.append(("tool", match.group(1)))
    return found


def module_exists(name: str, root: Path) -> bool:
    parts = name.split(".")
    if parts[0] != "mvp":
        return False
    file = root.joinpath(*parts).with_suffix(".py")
    package = root.joinpath(*parts, "__init__.py")
    return file.exists() or package.exists()


def check(doc: Path = DOC, root: Path = ROOT) -> list[str]:
    if not doc.exists():
        return [f"缺少状态表：{doc}"]
    makefile = root / "Makefile"
    targets = set(MAKE_TARGET_RE.findall(makefile.read_text(encoding="utf-8")))
    problems: list[str] = []
    for header, rows in tables(doc):
        needs_evidence = header[-1] == "证据"
        for cells in rows:
            entry, evidence = cells[0], cells[-1]
            commands = referenced_commands(evidence)
            if needs_evidence and not commands:
                problems.append(f"条目没有可机检的证据命令：{entry}")
                continue
            for kind, name in commands:
                if kind == "make" and name not in targets:
                    problems.append(f"{entry}：make 目标不存在：{name}")
                elif kind == "module" and not module_exists(name, root):
                    problems.append(f"{entry}：模块不存在：{name}")
                elif kind == "tool" and not (root / name).exists():
                    problems.append(f"{entry}：工具不存在：{name}")
    return problems


def main() -> int:
    problems = check()
    if problems:
        print("落地状态表与代码不一致：")
        for p in problems:
            print("  " + p)
        return 1
    print(f"落地状态表：{len(table_rows(DOC))} 行，引用的命令全部存在")
    return 0


if __name__ == "__main__":
    sys.exit(main())