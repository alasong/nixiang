"""下一条任务：结构化输入 → 逆向链判定 → 报告（不调模型）。

这是唯一能把「一条具体任务」交给系统的入口。自由文本任务不支持——没有对应
的验证器与领域规则，硬接会把「判不了」伪装成「判完了」。
"""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from mvp.task import judge, load_task, main, render

SAMPLE = {
    "id": "T-1",
    "amount": 8000,
    "has_director_approval": False,
    "city_tier": 1,
    "hotel_per_night": 500,
    "nights": 2,
    "transport_class": "商务舱",
    "meal_days": 3,
    "meal_total": 200,
    "invoice_dates": ["2026-01-01", "2026-01-03"],
    "trip_start": "2026-01-01",
    "trip_end": "2026-01-02",
    "submitted_month": "2026-01",
    "has_note": False,
    "same_day_dup": False,
}


class TestLoad(unittest.TestCase):
    def test_rejects_non_object(self) -> None:
        with self.assertRaises(ValueError):
            load_task([1, 2, 3])

    def test_rejects_no_known_fields(self) -> None:
        with self.assertRaises(ValueError):
            load_task({"foo": 1})

    def test_keeps_known_fields(self) -> None:
        facts = load_task(SAMPLE)
        self.assertEqual(facts["amount"], 8000)
        self.assertEqual(facts["transport_class"], "商务舱")


class TestJudge(unittest.TestCase):
    def test_finds_violations(self) -> None:
        result = judge(SAMPLE)
        self.assertEqual(result["violated"], ["R1", "R3", "R5"])

    def test_reports_unknown_and_missing_fields(self) -> None:
        partial = {k: v for k, v in SAMPLE.items() if k != "has_director_approval"}
        result = judge(partial)
        self.assertIn("R1", result["unknown"])
        self.assertIn("has_director_approval", result["missing"])

    def test_clean_task_has_no_violations(self) -> None:
        clean = dict(SAMPLE, amount=1000, has_director_approval=True,
                     transport_class="经济舱", hotel_per_night=300,
                     meal_total=300, invoice_dates=["2026-01-01"],
                     trip_end="2026-01-03")
        self.assertEqual(judge(clean)["violated"], [])


class TestRender(unittest.TestCase):
    def test_contains_values(self) -> None:
        text = render(load_task(SAMPLE))
        self.assertIn("8000", text)
        self.assertIn("商务舱", text)


class TestMain(unittest.TestCase):
    def _run(self, argv: list[str]) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(argv)
        return rc, buf.getvalue()

    def test_single_task(self) -> None:
        rc, out = self._run(["--json", json.dumps(SAMPLE)])
        self.assertEqual(rc, 0)
        self.assertIn("R1", out)
        self.assertIn("R3", out)

    def test_invalid_json_exit_2(self) -> None:
        rc, _ = self._run(["--json", "{oops"])
        self.assertEqual(rc, 2)

    def test_batch_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "tasks.jsonl"
            path.write_text(
                json.dumps(SAMPLE) + "\n"
                + json.dumps(dict(SAMPLE, id="T-2", amount=1000,
                                  has_director_approval=True,
                                  transport_class="经济舱",
                                  hotel_per_night=300)) + "\n",
                encoding="utf-8")
            rc, out = self._run(["--file", str(path)])
        self.assertEqual(rc, 0)
        self.assertIn("T-1", out)
        self.assertIn("T-2", out)


if __name__ == "__main__":
    unittest.main()