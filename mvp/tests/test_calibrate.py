"""rubric 校准（缺口：评审与人类一致）：离线样本可复现，真实样本待输入。"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mvp.calibrate import Sample, evaluate, load_samples, main
from mvp.judge import FileJudge, JudgeResult

ROOT = Path(__file__).resolve().parents[2]


class TestLoad(unittest.TestCase):
    def test_loads_samples(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "s.jsonl"
            path.write_text('{"task": "t", "content": "c", "human": 80}\n',
                            encoding="utf-8")
            samples = load_samples(path)
            self.assertEqual(samples[0].human, 80)

    def test_rejects_missing_human(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "s.jsonl"
            path.write_text('{"task": "t", "content": "c"}\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_samples(path)


class TestEvaluate(unittest.TestCase):
    def test_agreement_counts(self) -> None:
        samples = [Sample("t1", "c1", 80), Sample("t2", "c2", 20),
                   Sample("t3", "c3", 90)]
        judge = FileJudge([JudgeResult(85), JudgeResult(25), JudgeResult(80)])
        report = evaluate(samples, judge)
        self.assertEqual(report["n"], 3)
        self.assertEqual(report["agree"], 3)
        self.assertEqual(report["mae"], (5 + 5 + 10) / 3)
        self.assertEqual(report["bias"], (5 + 5 - 10) / 3)

    def test_disagreement(self) -> None:
        samples = [Sample("t1", "c1", 80), Sample("t2", "c2", 80)]
        judge = FileJudge([JudgeResult(10), JudgeResult(10)])
        report = evaluate(samples, judge)
        self.assertEqual(report["agree"], 0)


class TestMain(unittest.TestCase):
    def _files(self, root: Path, human: list[int], judged: list[int]):
        samples = root / "s.jsonl"
        samples.write_text("\n".join(
            f'{{"task": "t{i}", "content": "c{i}", "human": {h}}}'
            for i, h in enumerate(human)) + "\n", encoding="utf-8")
        judge = root / "j.jsonl"
        judge.write_text("\n".join(f'{{"score": {s}, "reason": "r"}}'
                                   for s in judged) + "\n", encoding="utf-8")
        return samples, judge

    def test_below_threshold_returns_1(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            samples, judge = self._files(Path(d), [80, 80], [10, 10])
            self.assertEqual(main(["--samples", str(samples),
                                   "--judge-from", str(judge)]), 1)

    def test_cli_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, HARNESS_LAB_RUNS=d)
            proc = subprocess.run(
                [sys.executable, "-m", "mvp.calibrate",
                 "--samples", str(ROOT / "tasks" / "T13-samples.jsonl"),
                 "--judge-from", str(ROOT / "tasks" / "T13-judge.jsonl")],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("agree=3/3", proc.stdout)


if __name__ == "__main__":
    unittest.main()