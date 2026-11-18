"""M0：任意任务 + 验收命令 → 执行 → 验收 → 轨迹落盘。

TDD：本文件先于 `mvp/handle.py` 存在。第一版必须红（模块不存在）；
实现后转绿。红了不改测试迁就实现，只改实现。

范围（M0）：确定性模式命中（内环），不调模型。自由文本任务如果没有匹配
的执行器，必须返回 unsupported，不能把"判不了"伪装成"做完了"。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mvp.core.contracts import Status
from mvp.core.persistence import RunStore
from mvp.handle import FileWriteExecutor, TaskSpec, handle, render

ROOT = Path(__file__).resolve().parents[2]


class TestWritePattern(unittest.TestCase):
    def test_writes_file_and_passes_verify(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "hello.txt"
            spec = TaskSpec(text=f"在 {target} 写入 hello",
                            verify=f"grep -q hello {target}")
            result = handle(spec, store=RunStore(Path(d) / "runs"))
            self.assertTrue(result.passed)
            self.assertEqual(target.read_text(encoding="utf-8").strip(), "hello")
            self.assertIn("verify: pass", render(result))

    def test_append_keeps_existing_content(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "log.txt"
            target.write_text("a\n", encoding="utf-8")
            spec = TaskSpec(text=f"在 {target} 追加 b",
                            verify=f"grep -q '^b$' {target}")
            result = handle(spec, store=RunStore(Path(d) / "runs"))
            self.assertTrue(result.passed)
            self.assertEqual(target.read_text(encoding="utf-8"), "a\nb\n")


class TestFailurePaths(unittest.TestCase):
    def test_verify_failure_is_not_pass(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "hello.txt"
            spec = TaskSpec(text=f"在 {target} 写入 hello",
                            verify=f"grep -q nope {target}")
            result = handle(spec, store=RunStore(Path(d) / "runs"))
            self.assertFalse(result.passed)
            self.assertEqual(result.trace.outcome, Status.FAIL)
            self.assertIn("verify: fail", render(result))

    def test_unsupported_task_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            spec = TaskSpec(text="帮我写一份竞品分析报告", verify="true")
            store = RunStore(Path(d) / "runs")
            result = handle(spec, store=store)
            self.assertEqual(result.status, "unsupported")
            self.assertEqual(result.trace.outcome, Status.UNKNOWN)
            self.assertIn("unsupported", render(result))
            # 不支持也要留痕，否则长尾任务永远不进统计
            self.assertEqual(len(store.traces_file.read_text(encoding="utf-8")
                                 .splitlines()), 1)


class TestTrace(unittest.TestCase):
    def test_trace_has_task_text_verify_and_two_steps(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "hello.txt"
            spec = TaskSpec(text=f"在 {target} 写入 hello",
                            verify=f"grep -q hello {target}")
            store = RunStore(Path(d) / "runs")
            result = handle(spec, store=store)
            rec = json.loads(store.traces_file.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(rec["task_text"], spec.text)
            self.assertEqual(rec["facts"]["verify"], spec.verify)
            self.assertEqual(rec["outcome"], "pass")
            self.assertEqual([s["action"] for s in rec["steps"]], ["write_file", "verify"])
            self.assertTrue(rec["task_id"])

    def test_traces_are_append_only(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "hello.txt"
            store = RunStore(Path(d) / "runs")
            spec = TaskSpec(text=f"在 {target} 写入 hello",
                            verify=f"grep -q hello {target}")
            handle(spec, store=store)
            handle(spec, store=store)
            self.assertEqual(len(store.traces_file.read_text(encoding="utf-8")
                                 .splitlines()), 2)


class TestCLI(unittest.TestCase):
    def run_cli(self, task: str, verify: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, HARNESS_LAB_RUNS=d)
            return subprocess.run(
                [sys.executable, "-m", "mvp.handle", "--task", task, "--verify", verify],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)

    def test_cli_passes_and_prints_verify_pass(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "cli.txt"
            proc = self.run_cli(f"在 {target} 写入 cli-ok", f"grep -q cli-ok {target}")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("verify: pass", proc.stdout)

    def test_cli_fails_with_exit_1(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "cli.txt"
            proc = self.run_cli(f"在 {target} 写入 cli-ok", f"grep -q nope {target}")
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("verify: fail", proc.stdout)

    def test_cli_unsupported_exit_2(self) -> None:
        proc = self.run_cli("帮我写一份竞品分析报告", "true")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("unsupported", proc.stdout)


class TestExecutorContract(unittest.TestCase):
    def test_executor_is_pluggable(self) -> None:
        class NoopExecutor(FileWriteExecutor):
            name = "noop"

            def match(self, text: str):
                return {} if text.startswith("noop") else None

            def run(self, parsed: dict) -> dict:
                return {"output": {"noop": True}}

        with tempfile.TemporaryDirectory() as d:
            spec = TaskSpec(text="noop", verify="true")
            result = handle(spec, executor=NoopExecutor(),
                            store=RunStore(Path(d) / "runs"))
            self.assertTrue(result.passed)
            self.assertEqual(result.executor, "noop")


if __name__ == "__main__":
    unittest.main()