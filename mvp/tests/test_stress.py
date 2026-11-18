"""跨域压测（M4）：多域任务 × 重复，输出可复现的通过数与墙钟。

范围：确定性任务（文件/文本/代码/复合），不测语义质量——这里的"域"只换
任务形状与验收命令，不换领域知识。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mvp.stress import load_cases, main, run, summarize

ROOT = Path(__file__).resolve().parents[2]


def write_cases(path: Path, cases: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n",
                    encoding="utf-8")
    return path


class TestLoadCases(unittest.TestCase):
    def test_loads_fields(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = write_cases(Path(d) / "c.jsonl", [
                {"name": "a", "domain": "docs", "task": "在 /tmp/a 写入 x",
                 "verify": "true", "agents": 2},
            ])
            cases = load_cases(path)
            self.assertEqual(cases[0].name, "a")
            self.assertEqual(cases[0].agents, 2)

    def test_rejects_missing_verify(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = write_cases(Path(d) / "c.jsonl",
                               [{"name": "a", "domain": "docs", "task": "x"}])
            with self.assertRaises(ValueError):
                load_cases(path)


class TestRun(unittest.TestCase):
    def test_all_pass_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cases = [
                {"name": "one", "domain": "text",
                 "task": f"在 {root}/a.txt 写入 aa",
                 "verify": f"grep -q aa {root}/a.txt"},
                {"name": "two", "domain": "code",
                 "task": f"在 {root}/b.py 写入 print(7)",
                 "verify": f"python3 {root}/b.py | grep -q 7"},
            ]
            loaded = load_cases(write_cases(root / "c.jsonl", cases))
            results = run(loaded, repeat=2, store=__import__(
                "mvp.core.persistence", fromlist=["RunStore"]).RunStore(root / "runs"))
            summary = summarize(results)
            self.assertEqual(summary["runs"], 4)
            self.assertEqual(summary["passed"], 4)
            self.assertEqual(summary["failed"], 0)

    def test_failure_counted(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            loaded = load_cases(write_cases(root / "c.jsonl", [
                {"name": "bad", "domain": "text", "task": "在 /tmp/x 写入 y",
                 "verify": "false"},
            ]))
            from mvp.core.persistence import RunStore
            summary = summarize(run(loaded, repeat=1, store=RunStore(root / "runs")))
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(summary["passed"], 0)


class TestMain(unittest.TestCase):
    def test_main_returns_1_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = write_cases(root / "c.jsonl", [
                {"name": "bad", "domain": "text", "task": "在 /tmp/x 写入 y",
                 "verify": "false"},
            ])
            self.assertEqual(main(["--cases", str(path), "--repeat", "1",
                                   "--runs", str(root / "runs")]), 1)

    def test_cli_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, HARNESS_LAB_RUNS=d)
            proc = subprocess.run(
                [sys.executable, "-m", "mvp.stress", "--cases",
                 str(ROOT / "tasks" / "M4" / "cases.jsonl"),
                 "--repeat", "3", "--runs", str(Path(d) / "runs")],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("passed=24", proc.stdout)
            self.assertIn("failed=0", proc.stdout)


if __name__ == "__main__":
    unittest.main()