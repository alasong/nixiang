"""验收反馈修复（T14）：失败原因喂回模型再生成。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mvp.handle import TaskSpec, handle_candidates, render
from mvp.repair import FileRepairer, RepairError

ROOT = Path(__file__).resolve().parents[2]
GOOD = "nixiang 是一个双环实验的工具集合"   # 恰好 20 字符
BAD = "nixiang 太短"


class TestFileRepairer(unittest.TestCase):
    def test_returns_queued_responses(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "r.jsonl"
            path.write_text(json.dumps(GOOD, ensure_ascii=False) + "\n",
                            encoding="utf-8")
            repairer = FileRepairer.from_jsonl(path)
            self.assertEqual(repairer.repair("t", BAD, "len=9"), GOOD)

    def test_exhausted_raises(self) -> None:
        with self.assertRaises(RepairError):
            FileRepairer([]).repair("t", BAD, "len=9")


class TestHandleRepair(unittest.TestCase):
    def test_repair_fixes_failure(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            result = handle_candidates(
                TaskSpec(text="写一行", verify=f"grep -qxF '{GOOD}' {root}/out.txt"),
                root / "out.txt", [BAD],
                repairer=FileRepairer([GOOD]), repair_rounds=1,
                store=__import__("mvp.core.persistence",
                                 fromlist=["RunStore"]).RunStore(root / "runs"))
            self.assertTrue(result.passed)
            self.assertEqual(result.repaired, 1)
            self.assertIn("repaired=1", render(result))

    def test_no_repair_when_candidate_passes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            result = handle_candidates(
                TaskSpec(text="写一行", verify=f"grep -qxF '{GOOD}' {root}/out.txt"),
                root / "out.txt", [GOOD],
                repairer=FileRepairer([GOOD]), repair_rounds=1,
                store=__import__("mvp.core.persistence",
                                 fromlist=["RunStore"]).RunStore(root / "runs"))
            self.assertTrue(result.passed)
            self.assertEqual(result.repaired, 0)


class TestRepairCLI(unittest.TestCase):
    def test_cli_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, HARNESS_LAB_RUNS=d)
            out = Path(d) / "r.txt"
            proc = subprocess.run(
                [sys.executable, "-m", "mvp.handle",
                 "--task", "写一行中文，恰好 20 个汉字，且包含 nixiang",
                 "--out", str(out),
                 "--verify", f"python3 {ROOT}/tasks/T14-verify.py {out}",
                 "--candidates-from", str(ROOT / "tasks" / "T14-candidates.jsonl"),
                 "--repair-from", str(ROOT / "tasks" / "T14-repair.jsonl"),
                 "--repair", "1"],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("repaired=1", proc.stdout)


if __name__ == "__main__":
    unittest.main()