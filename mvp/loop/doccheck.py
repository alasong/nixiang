"""文档一致性机检：README 的声明必须能被代码验证。

检查三类，全部能判 0/1：

  1. 测试数    README 里的「N 个测试」== Makefile 列出的测试用例总数
  2. 入口      README 里每个 `python3 -m mvp.x` 都有对应模块文件
  3. make 目标 README 提到的 `make xxx` 都存在于 Makefile

正文里的业务数字（读数、百分比）需要语义判断，不在本检查范围内——那些靠
「数字必须能在本轮输出里找到」的纪律，而不是靠这个脚本。
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

TEST_MODULE_RE = re.compile(r"mvp\.tests\.test_[a-z0-9_]+")   # 分层后的路径   # 带数字：test_e2e
TEST_COUNT_RE = re.compile(r"(\d+)\s*个测试")
MODULE_CMD_RE = re.compile(r"python3 -m (mvp[\w.]*)")
MAKE_REF_RE = re.compile(r"make ([a-z][a-z-]*)")
MAKE_TARGET_RE = re.compile(r"^([a-z][a-z-]*):", re.M)


def test_modules(makefile: Path) -> list[str]:
    return sorted(set(TEST_MODULE_RE.findall(makefile.read_text(encoding="utf-8"))))


def _failed_tests(suite) -> list[str]:
    """unittest 对导入失败的模块会造一个 _FailedTest 并计 1 条——必须显式拒绝，
    否则拼错的模块名会被算成「一个通过的测试」。"""
    bad: list[str] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            bad.extend(_failed_tests(item))
        elif type(item).__name__ == "_FailedTest":
            bad.append(str(getattr(item, "_testMethodName", "unknown")))
    return bad


def count_tests(modules: list[str], root: Path) -> int:
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    loader = unittest.TestLoader()
    total = 0
    for name in modules:
        suite = loader.loadTestsFromName(name)
        bad = _failed_tests(suite)
        if bad:
            raise RuntimeError(f"测试模块加载失败：{name}（{bad}）")
        total += suite.countTestCases()
    return total


def readme_test_count(readme: Path) -> int | None:
    match = TEST_COUNT_RE.search(readme.read_text(encoding="utf-8"))
    return int(match.group(1)) if match else None


def readme_modules(readme: Path) -> list[str]:
    return sorted(set(MODULE_CMD_RE.findall(readme.read_text(encoding="utf-8"))))


def readme_make_refs(readme: Path) -> list[str]:
    return sorted(set(MAKE_REF_RE.findall(readme.read_text(encoding="utf-8"))))


def make_targets(makefile: Path) -> set[str]:
    return set(MAKE_TARGET_RE.findall(makefile.read_text(encoding="utf-8")))


def module_exists(name: str, root: Path) -> bool:
    parts = name.split(".")
    if parts[0] != "mvp":
        return False
    if len(parts) == 1:
        return (root / "mvp" / "__main__.py").exists()
    path = root.joinpath(*parts) .with_suffix(".py")
    pkg = root.joinpath(*parts, "__init__.py")
    return path.exists() or pkg.exists()


def check(root: Path) -> list[str]:
    """返回问题列表；空列表表示文档与代码一致。"""
    readme = root / "README.md"
    makefile = root / "Makefile"
    problems: list[str] = []
    if not readme.exists() or not makefile.exists():
        return ["缺少 README.md 或 Makefile"]

    modules = test_modules(makefile)
    actual = count_tests(modules, root)
    claimed = readme_test_count(readme)
    if claimed is None:
        problems.append("README 里找不到「N 个测试」的声明")
    elif claimed != actual:
        problems.append(f"测试数不一致：README 写 {claimed}，实际 {actual}"
                        f"（Makefile 列了 {len(modules)} 个测试模块）")

    for name in readme_modules(readme):
        if not module_exists(name, root):
            problems.append(f"README 引用了不存在的入口：{name}")

    targets = make_targets(makefile)
    for ref in readme_make_refs(readme):
        if ref not in targets:
            problems.append(f"README 引用了不存在的 make 目标：{ref}")

    return problems