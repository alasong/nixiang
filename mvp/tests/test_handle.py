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
from mvp.handle import (FileWriteExecutor, TaskSpec, handle, handle_twice,
                        parse_task_file, render, split_conjunction)

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


class TestDecomposition(unittest.TestCase):
    def test_parse_task_file_returns_subgoals(self) -> None:
        content = """# 任务
## 子目标

1. 在 /tmp/a.txt 写入 a
   验收：grep -q a /tmp/a.txt
2. 在 /tmp/b.txt 写入 b
   验收：grep -q b /tmp/b.txt
"""
        subs = parse_task_file(content)
        self.assertEqual(len(subs), 2)
        self.assertEqual(subs[0].verify, "grep -q a /tmp/a.txt")
        self.assertIn("写入 a", subs[0].action)

    def test_parse_task_file_without_section_is_empty(self) -> None:
        self.assertEqual(parse_task_file("在 /tmp/a 写入 a"), [])

    def test_split_conjunction_keeps_quoted_amp(self) -> None:
        self.assertEqual(split_conjunction("a && b && c"), ["a", "b", "c"])
        self.assertEqual(split_conjunction('grep -q "x && y" f && true'),
                         ['grep -q "x && y" f', "true"])

    def test_conjunction_is_decomposed_and_counted(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "x.txt"
            spec = TaskSpec(text=f"在 {target} 写入 x", verify="true && true")
            result = handle(spec, store=RunStore(Path(d) / "runs"))
            self.assertTrue(result.passed)
            self.assertEqual(result.subgoals_passed, 2)
            self.assertIn("子目标 2/2", render(result))

    def test_composite_file_runs_each_subgoal(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            md = root / "task.md"
            md.write_text(
                "## 子目标\n\n"
                f"1. 在 {root}/a.txt 写入 aa\n   验收：grep -q aa {root}/a.txt\n"
                f"2. 在 {root}/b.txt 写入 bb\n   验收：grep -q bb {root}/b.txt\n"
                f"3. 在 {root}/c.txt 写入 cc\n   验收：grep -q cc {root}/c.txt\n",
                encoding="utf-8")
            spec = TaskSpec(text=str(md), verify="true")
            result = handle(spec, store=RunStore(root / "runs"))
            self.assertTrue(result.passed)
            self.assertEqual(result.subgoals_passed, 3)
            for name in ("a.txt", "b.txt", "c.txt"):
                self.assertTrue((root / name).exists())
            self.assertIn("子目标 3/3", render(result))

    def test_composite_reports_partial_failure(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            md = root / "task.md"
            md.write_text(
                "## 子目标\n\n"
                f"1. 在 {root}/a.txt 写入 aa\n   验收：true\n"
                f"2. 在 {root}/b.txt 写入 bb\n   验收：true\n"
                f"3. 在 {root}/c.txt 写入 cc\n   验收：false\n",
                encoding="utf-8")
            spec = TaskSpec(text=str(md), verify="true")
            result = handle(spec, store=RunStore(root / "runs"))
            self.assertFalse(result.passed)
            self.assertEqual(result.subgoals_passed, 2)
            self.assertIn("子目标 2/3", render(result))

    def test_explain_lists_subgoals(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            md = root / "task.md"
            md.write_text(
                "## 子目标\n\n"
                f"1. 在 {root}/a.txt 写入 aa\n   验收：true\n",
                encoding="utf-8")
            result = handle(TaskSpec(text=str(md), verify="true"),
                            store=RunStore(root / "runs"))
            text = render(result, explain=True)
            self.assertIn("子目标 1/1", text)
            self.assertIn("验收：true", text)


class TestAppendLine(unittest.TestCase):
    def test_append_line_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "notes.md"
            target.write_text("x\n", encoding="utf-8")
            spec = TaskSpec(text=f"在 {target} 末尾加一行 y",
                            verify=f"grep -q '^y$' {target}")
            result = handle(spec, store=RunStore(Path(d) / "runs"))
            self.assertTrue(result.passed)
            self.assertEqual(target.read_text(encoding="utf-8"), "x\ny\n")


class TestCompositeCLI(unittest.TestCase):
    def test_cli_runs_repo_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, HARNESS_LAB_RUNS=d)
            proc = subprocess.run(
                [sys.executable, "-m", "mvp.handle",
                 "--task", str(ROOT / "tasks" / "T2.md"),
                 "--verify", "make docs-check", "--explain"],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("子目标 3/3", proc.stdout)


class TestPathResolution(unittest.TestCase):
    def test_extensionless_path_resolves_to_existing_md(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            target = root / "notes.md"
            target.write_text("x\n", encoding="utf-8")
            spec = TaskSpec(text=f"在 {root}/notes 末尾加一行 y",
                            verify=f"grep -q '^y$' {target}")
            result = handle(spec, store=RunStore(root / "runs"))
            self.assertTrue(result.passed)
            self.assertEqual(target.read_text(encoding="utf-8"), "x\ny\n")
            self.assertFalse((root / "notes").exists())

    def test_md_sibling_wins_when_both_exist(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            bare = root / "README"
            bare.write_text("bare\n", encoding="utf-8")
            md = root / "README.md"
            md.write_text("md\n", encoding="utf-8")
            spec = TaskSpec(text=f"在 {root}/README 末尾加一行 y",
                            verify=f"grep -q '^y$' {md}")
            result = handle(spec, store=RunStore(root / "runs"))
            self.assertTrue(result.passed)
            self.assertEqual(md.read_text(encoding="utf-8"), "md\ny\n")
            self.assertEqual(bare.read_text(encoding="utf-8"), "bare\n")


class TestTwice(unittest.TestCase):
    def test_second_run_reuses_plan(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            target = root / "hello.txt"
            store = RunStore(root / "runs")
            spec = TaskSpec(text=f"在 {target} 写入 hello",
                            verify=f"grep -q hello {target}")
            first, second = handle_twice(spec, store=store)
            self.assertTrue(first.passed)
            self.assertTrue(second.passed)
            self.assertTrue(second.plan_reused)
            self.assertEqual(second.model_calls, 0)
            plans = store.root / "plans.jsonl"
            self.assertTrue(plans.exists())
            self.assertGreaterEqual(
                len(plans.read_text(encoding="utf-8").splitlines()), 1)

    def test_cli_twice_reports_model_calls(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, HARNESS_LAB_RUNS=d)
            target = Path(d) / "cli.txt"
            proc = subprocess.run(
                [sys.executable, "-m", "mvp.handle", "--task",
                 f"在 {target} 写入 cli-ok", "--verify",
                 f"grep -q cli-ok {target}", "--twice"],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("model_calls=0", proc.stdout)
            self.assertIn("计划缓存", proc.stdout)


if __name__ == "__main__":
    unittest.main()