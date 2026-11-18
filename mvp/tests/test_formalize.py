"""形式化（T12）：自由文本 → 可执行计划，以及运行进度输出。"""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mvp.formalize import FileFormalizer, FormalizeError
from mvp.handle import TaskSpec, handle, render

ROOT = Path(__file__).resolve().parents[2]


class TestFileFormalizer(unittest.TestCase):
    def test_returns_plan_text(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "plan.md"
            path.write_text("## 子目标\n\n1. 在 /tmp/x 写入 y\n   验收：true\n",
                            encoding="utf-8")
            formalizer = FileFormalizer(path)
            text = formalizer.formalize("任意自由文本")
            self.assertIn("## 子目标", text)
            self.assertEqual(formalizer.vid, "formalize-file-v1")

    def test_missing_file_raises(self) -> None:
        with self.assertRaises(FormalizeError):
            FileFormalizer(Path("/nonexistent/plan.md")).formalize("x")


class TestHandleWithPlan(unittest.TestCase):
    def _plan(self, root: Path) -> str:
        return ("## 子目标\n\n"
                f"1. 在 {root}/a.txt 写入 nixiang 第一行\n"
                f"   验收：grep -q nixiang {root}/a.txt\n"
                f"2. 在 {root}/a.txt 追加 第二行\n"
                f"   验收：grep -q 第二行 {root}/a.txt\n")

    def test_plan_executes_and_marks_formalized(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            result = handle(
                TaskSpec(text="自由文本任务", verify=f"grep -q nixiang {root}/a.txt"),
                store=__import__("mvp.core.persistence",
                                 fromlist=["RunStore"]).RunStore(root / "runs"),
                plan_text=self._plan(root),
                formalizer_vid="formalize-file-v1")
            self.assertTrue(result.passed)
            self.assertEqual(result.subgoals_passed, 2)
            self.assertIn("formalized=formalize-file-v1", render(result))

    def test_progress_prints_each_subgoal(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            from mvp.core.persistence import RunStore
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                handle(TaskSpec(text="自由文本任务",
                                verify=f"grep -q nixiang {root}/a.txt"),
                       store=RunStore(root / "runs"),
                       plan_text=self._plan(root), progress=True)
            out = buf.getvalue()
            self.assertEqual(out.count("[progress] 子目标"), 2)
            self.assertIn("[progress] 子目标 1/2", out)


class TestFormalizeCLI(unittest.TestCase):
    def test_cli_offline(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, HARNESS_LAB_RUNS=d)
            proc = subprocess.run(
                [sys.executable, "-m", "mvp.handle",
                 "--task", "写一份包含 nixiang 的两行说明并保存到 /tmp/xm-f.txt",
                 "--verify", "grep -q nixiang /tmp/xm-f.txt",
                 "--formalize-from", str(ROOT / "tasks" / "T12-plan.md"),
                 "--progress"],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("formalized=formalize-file-v1", proc.stdout)
            self.assertIn("[progress] 子目标", proc.stdout)

    def test_cli_missing_plan_rc2(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, HARNESS_LAB_RUNS=d)
            proc = subprocess.run(
                [sys.executable, "-m", "mvp.handle", "--task", "x",
                 "--verify", "true", "--formalize-from", "/nonexistent/plan.md"],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()