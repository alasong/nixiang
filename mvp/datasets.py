"""数据集附带文件的定位与下载缓存（只用标准库）。

仓库里提交的是两个 parquet；Banking77 的说明文件（13KB 的数据集卡，含
77 个意图名）不提交——数据集卡的许可是数据集那边的，缓存比入库稳妥。

解析顺序：
    data/b77_readme.md          → 手动放的优先
    <缓存目录>/b77_readme.md     → 上次自动下载的
    镜像下载（hf-mirror → huggingface）→ 写入缓存

缓存目录可用 HARNESS_LAB_CACHE 覆盖（测试用）。
"""
from __future__ import annotations

import os
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

B77_README_URLS = (
    "https://hf-mirror.com/datasets/PolyAI/banking77/raw/main/README.md",
    "https://huggingface.co/datasets/PolyAI/banking77/raw/main/README.md",
)

# 意图名里有 question mark（reverted_card_payment?），旧正则会把 53 号漏掉
NAME_RE = re.compile(r"^\s+'(\d+)':\s*([A-Za-z_0-9?]+)\s*$", re.M)


def cache_dir() -> Path:
    override = os.environ.get("HARNESS_LAB_CACHE")
    return Path(override) if override else Path.home() / ".cache" / "harness-lab"


def b77_readme(auto_fetch: bool = True) -> Path:
    """返回说明文件路径；缺失且允许下载时自动拉取到缓存。"""
    candidates = [DATA / "b77_readme.md", cache_dir() / "b77_readme.md"]
    for path in candidates:
        if path.exists() and path.stat().st_size > 0:
            return path
    if not auto_fetch:
        raise FileNotFoundError(
            "缺少 b77_readme.md：放到 data/ 下，或去掉 auto_fetch=False 让它自动下载")

    target = cache_dir() / "b77_readme.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for url in B77_README_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "harness-lab"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            if len(data) < 1000:
                raise ValueError(f"内容过小（{len(data)} 字节）")
            target.write_bytes(data)
            return target
        except Exception as exc:          # 换下一个镜像
            last = exc
    raise RuntimeError(f"下载 b77_readme.md 失败（镜像都试过）：{last}")


def b77_names(auto_fetch: bool = True) -> dict[int, str]:
    text = b77_readme(auto_fetch).read_text(encoding="utf-8", errors="replace")
    return {int(i): name for i, name in NAME_RE.findall(text)}