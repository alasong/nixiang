"""文档一致性检查的测试：能抓到三类漂移。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mvp.loop.doccheck import (check, count_tests, make_targets, module_exists,
                          readme_make_refs, readme_modules, readme_test_count,
                          test_modules)

FAKE_MAKEFILE = """PY ?= python3

test:
\t$(PY) -m unittest mvp.tests.test_mvp -v

help:
\t@echo hi
"""

FAKE_README = """# demo

32 个测试，零依赖。

| 命令 | 作用 |
|---|---|
| `python3 -m mvp` | 跑 |
| `python3 -m mvp.demo_real_task` | 演示 |

`make test` 和 `make help`。
"""


class TestDocCheckParsing(unittest.TestCase):
    def test_parses_test_modules_targets_and_claims(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "Makefile").write_text(FAKE_MAKEFILE, encoding="utf-8")
            (root / "README.md").write_text(FAKE_README, encoding="utf-8")
            self.assertEqual(test_modules(root / "Makefile"), ["mvp.tests.test_mvp"])
            self.assertEqual(make_targets(root / "Makefile"), {"test", "help"})
            self.assertEqual(readme_test_count(root / "README.md"), 32)
            self.assertIn("mvp.demo_real_task", readme_modules(root / "README.md"))
            self.assertEqual(readme_make_refs(root / "README.md"), ["help", "test"])

    def test_module_exists(self) -> None:
        root = Path(__file__).resolve().parents[2]
        self.assertTrue(module_exists("mvp", root))
        self.assertTrue(module_exists("mvp.demos.e2e", root))
        self.assertFalse(module_exists("mvp.core.nope", root))


class TestDocCheckDrift(unittest.TestCase):
    def test_repo_is_consistent(self) -> None:
        root = Path(__file__).resolve().parents[2]
        self.assertEqual(check(root), [], "README 与代码漂移了：跑 tools/check_docs.py 看细节")

    def test_wrong_test_count_detected(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "Makefile").write_text(FAKE_MAKEFILE, encoding="utf-8")
            (root / "README.md").write_text(
                FAKE_README.replace("32 个测试", "99 个测试"), encoding="utf-8")
            # 真实模块里 mvp.tests.test_mvp 有 15 条用例，声明 99 必然对不上
            problems = check(root)
            self.assertTrue(any("测试数不一致" in p for p in problems), problems)

    def test_missing_entry_detected(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "Makefile").write_text(FAKE_MAKEFILE, encoding="utf-8")
            (root / "README.md").write_text(
                FAKE_README.replace("python3 -m mvp`", "python3 -m mvp.nope`"),
                encoding="utf-8")
            self.assertTrue(any("不存在的入口" in p for p in check(root)))

    def test_missing_make_target_detected(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "Makefile").write_text(FAKE_MAKEFILE, encoding="utf-8")
            (root / "README.md").write_text(
                FAKE_README.replace("make help", "make nope"), encoding="utf-8")
            self.assertTrue(any("不存在的 make 目标" in p for p in check(root)))


if __name__ == "__main__":
    unittest.main()