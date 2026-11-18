"""真实模型小样本测量（缺口：真实模型成功率 / 多 Agent 收益）。

只用可注入的 provider + judge；测试用 fake，真实读数用 DeepSeek 跑
`tools/real_measure.py`。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mvp.measure import Item, measure, summarize


class FakeProvider:
    def __init__(self, contents: dict[str, list[str]]) -> None:
        self.contents = contents

    def generate(self, task: str, n: int) -> list[str]:
        return self.contents[task][:n]


class FakeJudge:
    vid = "judge-v1"

    def score(self, task: str, content: str):
        from mvp.judge import JudgeResult
        return JudgeResult(90 if "good" in content else 10)


class TestMeasure(unittest.TestCase):
    def test_best_beats_single(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            items = [
                Item(name="a", task="写 a", out=str(root / "a.txt"),
                     verify=f"grep -q good {root}/a.txt"),
                Item(name="b", task="写 b", out=str(root / "b.txt"),
                     verify=f"grep -q good {root}/b.txt"),
            ]
            provider = FakeProvider({
                "写 a": ["bad", "good", "bad"],
                "写 b": ["good", "bad", "bad"],
            })
            rows = measure(items, provider, FakeJudge(), n=3,
                           store=__import__("mvp.core.persistence",
                                            fromlist=["RunStore"]).RunStore(root / "runs"))
            self.assertEqual(len(rows), 2)
            self.assertFalse(rows[0].single_pass)
            self.assertTrue(rows[0].best_pass)
            self.assertEqual(rows[0].best_score, 90)
            self.assertEqual(rows[0].best_index, 2)
            self.assertTrue(rows[1].single_pass)

    def test_summarize_counts(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            items = [Item(name="a", task="写 a", out=str(root / "a.txt"),
                          verify=f"grep -q good {root}/a.txt")]
            rows = measure(items, FakeProvider({"写 a": ["bad", "good"]}),
                           FakeJudge(), n=2,
                           store=__import__("mvp.core.persistence",
                                            fromlist=["RunStore"]).RunStore(root / "runs"))
            summary = summarize(rows)
            self.assertEqual(summary["tasks"], 1)
            self.assertEqual(summary["best_pass"], 1)
            self.assertEqual(summary["single_pass"], 0)
            self.assertGreater(summary["mean_delta"], 0)
            self.assertEqual(summary["single_fail"], 1)
            self.assertEqual(summary["rescued"], 1)
            self.assertEqual(summary["rescue_rate"], 1.0)

    def test_rows_json_serializable(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            items = [Item(name="a", task="写 a", out=str(root / "a.txt"),
                          verify=f"grep -q good {root}/a.txt")]
            rows = measure(items, FakeProvider({"写 a": ["good"]}),
                           FakeJudge(), n=1,
                           store=__import__("mvp.core.persistence",
                                            fromlist=["RunStore"]).RunStore(root / "runs"))
            self.assertIn("single_pass", json.dumps([row.__dict__ for row in rows]))


if __name__ == "__main__":
    unittest.main()