"""质量漂移探针（T24）：重跑已知通过任务，失败即漂移。"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from mvp.drift import check, main
from mvp.stress import load_cases


def cases_file(root: Path, rows: list[dict]) -> Path:
    path = root / "cases.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")
    return path


class TestDrift(unittest.TestCase):
    def test_no_drift(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            loaded = load_cases(cases_file(root, [
                {"name": "ok", "domain": "text", "task": "在 /tmp/x 写入 y",
                 "verify": "true"},
            ]))
            report = check(loaded, store=__import__("mvp.core.persistence",
                                                    fromlist=["RunStore"]).RunStore(root / "runs"))
            self.assertEqual(report["drift"], 0)

    def test_drift_detected(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            loaded = load_cases(cases_file(root, [
                {"name": "bad", "domain": "text", "task": "在 /tmp/x 写入 y",
                 "verify": "false"},
            ]))
            report = check(loaded, store=__import__("mvp.core.persistence",
                                                    fromlist=["RunStore"]).RunStore(root / "runs"))
            self.assertEqual(report["drift"], 1)
            self.assertIn("bad", report["failed"])

    def test_main_prints_drift(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = cases_file(root, [
                {"name": "ok", "domain": "text", "task": "在 /tmp/x 写入 y",
                 "verify": "true"},
            ])
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["--cases", str(path), "--runs", str(root / "runs")])
            self.assertEqual(rc, 0)
            self.assertIn("drift=0/1", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
