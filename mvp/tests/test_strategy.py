"""策略挖掘（T20）与 priority 历史估计（T23）。"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mvp.strategy import StrategyStore, shape_of

ROOT = Path(__file__).resolve().parents[2]


class TestShape(unittest.TestCase):
    def test_same_shape_across_paths_and_contents(self) -> None:
        left = shape_of("grep -q a /tmp/x.txt && grep -q b /tmp/x.txt")
        right = shape_of("grep -q c /tmp/y.txt && grep -q d /tmp/y.txt")
        self.assertEqual(left, right)

    def test_distinct_paths_differ(self) -> None:
        same = shape_of("grep -q a /tmp/x && grep -q b /tmp/x")
        diff = shape_of("grep -q a /tmp/x && grep -q b /tmp/y")
        self.assertNotEqual(same, diff)


class TestStore(unittest.TestCase):
    def test_records_and_picks_best(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            store = StrategyStore(Path(d) / "strategies.jsonl")
            shape = shape_of("grep -q a /tmp/x && grep -q b /tmp/x")
            store.record(shape, "naive", False)
            store.record(shape, "dedup", True)
            store.record(shape, "dedup", True)
            self.assertEqual(store.best(shape), "dedup")
            self.assertGreater(store.p(shape, "dedup"), 0.9)


class TestStrategyCLI(unittest.TestCase):
    def test_second_run_hits(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, HARNESS_LAB_RUNS=d)
            def run(target: Path) -> subprocess.CompletedProcess:
                return subprocess.run(
                    [sys.executable, "-m", "mvp.handle",
                     "--task", "文件里要既有 a 又有 b",
                     "--verify", f"grep -q a {target} && grep -q b {target}",
                     "--auto-plan"],
                    cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
            first = run(Path(d) / "a.txt")
            self.assertNotIn("strategy=hit", first.stdout)
            second = run(Path(d) / "b.txt")
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertIn("strategy=hit", second.stdout)
            self.assertIn("search=1/2", second.stdout)


class TestPriorityOutput(unittest.TestCase):
    def test_explain_prints_p(self) -> None:
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
            self.assertIn("p=", proc.stdout)


if __name__ == "__main__":
    unittest.main()
