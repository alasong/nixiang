"""从验收谓词反推动作（T17）：符号逆向规划。"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mvp.backward_plan import plan_from_verify, plan_text
from mvp.handle import parse_task_file

ROOT = Path(__file__).resolve().parents[2]


class TestPlanFromVerify(unittest.TestCase):
    def test_grep_simple(self) -> None:
        subs = plan_from_verify("grep -q hello /tmp/a.txt")
        self.assertEqual(len(subs), 1)
        self.assertIn("/tmp/a.txt", subs[0].action)
        self.assertIn("hello", subs[0].action)
        self.assertEqual(subs[0].verify, "grep -q hello /tmp/a.txt")

    def test_grep_quoted_content(self) -> None:
        subs = plan_from_verify("grep -q 'hello world' /tmp/a.txt")
        self.assertIn("hello world", subs[0].action)

    def test_conjunction(self) -> None:
        subs = plan_from_verify("grep -q a /tmp/a && grep -q b /tmp/b")
        self.assertEqual(len(subs), 2)

    def test_test_f(self) -> None:
        subs = plan_from_verify("test -f /tmp/c.txt")
        self.assertEqual(len(subs), 1)
        self.assertIn("/tmp/c.txt", subs[0].action)

    def test_unknown_returns_empty(self) -> None:
        self.assertEqual(plan_from_verify("make docs-check"), [])


class TestPlanText(unittest.TestCase):
    def test_round_trips_through_parser(self) -> None:
        subs = plan_from_verify("grep -q a /tmp/a && grep -q b /tmp/b")
        parsed = parse_task_file(plan_text(subs))
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0].verify, "grep -q a /tmp/a")


class TestAutoPlanCLI(unittest.TestCase):
    def test_cli_derives_action_from_verify(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, HARNESS_LAB_RUNS=d)
            target = Path(d) / "auto.txt"
            proc = subprocess.run(
                [sys.executable, "-m", "mvp.handle",
                 "--task", "给我一个包含 hello 的文件",
                 "--verify", f"grep -q hello {target}", "--auto-plan"],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("formalized=backward-plan-v1", proc.stdout)
            self.assertIn("hello", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()