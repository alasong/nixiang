"""AND-OR 图 + 证明树 + priority(n)（T19）。"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mvp.planner import build_plan, render_plan

ROOT = Path(__file__).resolve().parents[2]


class TestBuildPlan(unittest.TestCase):
    def test_same_path_two_branches_ordered_by_priority(self) -> None:
        plan = build_plan("grep -q a /tmp/s.txt && grep -q b /tmp/s.txt && "
                          "grep -q c /tmp/s.txt", depth=1)
        self.assertIsNotNone(plan)
        self.assertEqual(len(plan.branches), 2)
        self.assertEqual(plan.branches[0].name, "naive")
        self.assertGreater(plan.branches[0].priority, plan.branches[1].priority)
        self.assertEqual(plan.atoms, 3)

    def test_depth_zero_single_branch(self) -> None:
        plan = build_plan("grep -q a /tmp/s.txt && grep -q b /tmp/s.txt", depth=0)
        self.assertEqual(len(plan.branches), 1)

    def test_unknown_returns_none(self) -> None:
        self.assertIsNone(build_plan("make docs-check", depth=1))


class TestRender(unittest.TestCase):
    def test_proof_tree_marks_chosen(self) -> None:
        plan = build_plan("grep -q a /tmp/s.txt && grep -q b /tmp/s.txt", depth=1)
        text = render_plan(plan, [False, True])
        self.assertIn("OR[1] naive", text)
        self.assertIn("fail", text)
        self.assertIn("pass (chosen)", text)
        self.assertIn("proof:", text)


class TestExplainCLI(unittest.TestCase):
    def test_cli_prints_or_and_proof(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, HARNESS_LAB_RUNS=d)
            target = Path(d) / "p.txt"
            proc = subprocess.run(
                [sys.executable, "-m", "mvp.handle",
                 "--task", "三个词都要在同一个文件里",
                 "--verify", f"grep -q a {target} && grep -q b {target} && "
                             f"grep -q c {target}",
                 "--auto-plan", "--explain-plan"],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("or=2 tried=2", proc.stdout)
            self.assertIn("proof:", proc.stdout)


if __name__ == "__main__":
    unittest.main()
