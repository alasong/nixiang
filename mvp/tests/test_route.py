"""在线路由（T25）：按任务/验收形状选路径。"""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from mvp.route import main, route_task

ROOT = Path(__file__).resolve().parents[2]


class TestRoute(unittest.TestCase):
    def test_backward_for_reversible_verify(self) -> None:
        self.assertEqual(route_task("在 /tmp/x 写入 hi", "grep -q hi /tmp/x"),
                         "backward")

    def test_decompose_for_task_file(self) -> None:
        self.assertEqual(route_task(str(ROOT / "tasks" / "T2.md"),
                                    "make docs-check"), "decompose")

    def test_formalize_for_soft_goal(self) -> None:
        self.assertEqual(route_task("用两三段介绍 nixiang", "true"), "formalize")


class TestDemo(unittest.TestCase):
    def test_demo_prints_route(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["--demo"])
        self.assertEqual(rc, 0)
        self.assertIn("route=backward,decompose,formalize", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
