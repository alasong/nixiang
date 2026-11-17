"""数据集说明文件的定位测试：不联网，只测路径解析与解析正则。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mvp.core import datasets


class TestNamesResolution(unittest.TestCase):
    def test_reads_from_cache_dir(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / "cache"
            cache.mkdir()
            (cache / "b77_readme.md").write_text(
                "          '0': activate_my_card\n"
                "          '53': reverted_card_payment?\n",
                encoding="utf-8")
            with mock.patch.dict(os.environ, {"HARNESS_LAB_CACHE": str(cache)}):
                path = datasets.b77_readme(auto_fetch=False)
                self.assertEqual(path, cache / "b77_readme.md")
                names = datasets.b77_names(auto_fetch=False)
            self.assertEqual(names[0], "activate_my_card")
            self.assertEqual(names[53], "reverted_card_payment?",
                             "带问号的意图名不能被旧正则漏掉")

    def test_missing_without_fetch_raises_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"HARNESS_LAB_CACHE": d}):
                with self.assertRaises(FileNotFoundError) as ctx:
                    datasets.b77_readme(auto_fetch=False)
        self.assertIn("b77_readme.md", str(ctx.exception))

    def test_cache_dir_env_override(self) -> None:
        with mock.patch.dict(os.environ, {"HARNESS_LAB_CACHE": "/tmp/x-cache"}):
            self.assertEqual(datasets.cache_dir(), Path("/tmp/x-cache"))


if __name__ == "__main__":
    unittest.main()