"""落地状态表的机检测试：表不撒谎，检查器也不装样子。"""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_checker():
    spec = importlib.util.spec_from_file_location(
        "status_check", ROOT / "tools" / "status_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)          # type: ignore[union-attr]
    return module


class TestStatusCheck(unittest.TestCase):
    def setUp(self) -> None:
        self.checker = load_checker()

    def test_repo_status_table_is_consistent(self) -> None:
        problems = self.checker.check(ROOT / "docs" / "落地状态_2026-11.md", ROOT)
        self.assertEqual(problems, [])

    def test_detects_dangling_references(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "Makefile").write_text("test:\n\t@true\n", encoding="utf-8")
            (root / "docs").mkdir()
            doc = root / "docs" / "状态.md"
            doc.write_text(
                "| 条目 | 现状 | 证据 |\n|---|---|---|\n"
                "| A | 达成 | `make nope` |\n"
                "| B | 达成 | `python3 -m mvp.nope` |\n"
                "| C | 达成 | `python3 tools/nope.py` |\n",
                encoding="utf-8")
            problems = self.checker.check(doc, root)
            self.assertEqual(len(problems), 3, problems)
            self.assertTrue(any("make 目标不存在" in p for p in problems))
            self.assertTrue(any("模块不存在" in p for p in problems))
            self.assertTrue(any("工具不存在" in p for p in problems))

    def test_requires_evidence_for_evidence_tables(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "Makefile").write_text("test:\n\t@true\n", encoding="utf-8")
            doc = root / "doc.md"
            doc.write_text("| 条目 | 现状 | 证据 |\n|---|---|---|\n"
                           "| A | 达成 | 无 |\n", encoding="utf-8")
            problems = self.checker.check(doc, root)
            self.assertTrue(any("没有可机检的证据命令" in p for p in problems))


if __name__ == "__main__":
    unittest.main()