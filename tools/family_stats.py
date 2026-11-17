"""按族标签统计分布：H1 的标签口径。

聚类口径（`cluster_check.py`）要靠文本密度猜族，结果对阈值和抽样都敏感
（见 `h1_sweep.py` 的稳定性诊断）。如果你已经有族标签——业务类型、意图类别、
工单分类——H1 可以直接数，不需要猜：

    族数 5–8 且每族 ≥5 条   → 有分布，可建复用库
    族数 > 8                → 过细：按业务口径合并后再看
    族数 < 5                → 族太少，复用面窄
    任一族 < 5 条           → 样本不足，先攒数据

用法：
    .venv/bin/python tools/family_stats.py --dataset banking77
    .venv/bin/python tools/family_stats.py --dataset banking77 --names /tmp/b77_readme.md
    python3 tools/family_stats.py --jsonl tasks.jsonl      # 每行 {id, text, family}
    python3 tools/family_stats.py --self-test
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_real_check():
    spec = importlib.util.spec_from_file_location(
        "real_check", Path(__file__).resolve().parent / "real_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)          # type: ignore[union-attr]
    return module


def load_jsonl(path: Path) -> list[dict]:
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        family = item.get("family") or item.get("domain")
        if not family:
            raise SystemExit("jsonl 每行需要有 family 或 domain 字段")
        items.append({"id": item.get("id", ""), "family": str(family)})
    return items


def load_banking77(names_path: Path | None = None) -> list[dict]:
    """细粒度标签 = 77 个意图；给 names 时按粗粒度业务动作合并成 9 族。"""
    import pandas as pd

    labels = pd.read_parquet(ROOT / "data" / "b77_train.parquet")["label"].astype(int)
    mapping: dict[int, str] = {}
    if names_path is not None:
        import re

        real_check = _load_real_check()
        text = names_path.read_text(encoding="utf-8")
        names = {int(i): name for i, name in
                 re.findall(r"^\s+'(\d+)':\s*([A-Za-z_0-9?]+)\s*$", text, re.M)}
        if len(names) < 77:
            print(f"提示：只解析到 {len(names)} 个类别名，缺的会归入 other")
        mapping = real_check.coarse_map(names)
    return [{"id": str(i), "family": mapping.get(int(label), str(label))}
            for i, label in enumerate(labels)]


def verdict(n_families: int, min_size: int) -> tuple[bool, str]:
    """三档判定。

    5–8 族这个区间来自示例数据，不是统计判据。真正的要求是「每族样本够」+
    「族数不至于散碎」。所以 9–12 族但每族 ≥5 条时判「成立（边界）」，把
    超出建议区间这件事写明，而不是假装没超。
    """
    if min_size < 5:
        return False, f"不成立：最小族仅 {min_size} 条 < 5，样本不足"
    if n_families < 5:
        return False, f"存疑：仅 {n_families} 族，复用面窄"
    if n_families <= 8:
        return True, f"成立：{n_families} 族，最小 {min_size} 条"
    if n_families <= 12:
        return True, (f"成立（边界）：{n_families} 族超出 5–8 建议区间，"
                      f"但每族 ≥{min_size} 条")
    return False, f"存疑：{n_families} 族，过于细分（按业务口径合并后再看）"


def summarize(items: list[dict]) -> dict:
    counts = Counter(item["family"] for item in items)
    sizes = sorted(counts.values(), reverse=True)
    n = len(counts)
    ok, text = verdict(n, min(sizes) if sizes else 0)
    total = sum(sizes) or 1
    return {
        "n_items": len(items),
        "n_families": n,
        "min_size": min(sizes) if sizes else 0,
        "median_size": statistics.median(sizes) if sizes else 0,
        "max_size": max(sizes) if sizes else 0,
        "top5_share": round(sum(sizes[:5]) / total, 3),
        "counts": counts.most_common(),
        "h1_ok": ok,
        "verdict": text,
    }


def _print(summary: dict, label: str) -> None:
    print(f"族分布统计   口径 {label}   样本 {summary['n_items']}")
    print("=" * 72)
    for family, size in summary["counts"][:12]:
        print(f"  {family:<28}{size:>6}")
    if len(summary["counts"]) > 12:
        print(f"  … 其余 {len(summary['counts']) - 12} 族")
    print(f"\n族数 {summary['n_families']}   最小 {summary['min_size']}"
          f"   中位 {summary['median_size']:.0f}   最大 {summary['max_size']}"
          f"   前 5 族占比 {summary['top5_share'] * 100:.0f}%")
    print(f"H1 判定（标签口径）：{summary['verdict']}")


def self_test() -> None:
    items = [{"id": f"t{d}{i}", "family": d}
             for d in ["报销", "合同", "代码", "日志", "报表", "回复"] for i in range(6)]
    summary = summarize(items)
    assert summary["h1_ok"], summary["verdict"]
    over = summarize(items + [{"id": f"x{i}", "family": f"族{i}"} for i in range(5)])
    assert not over["h1_ok"]
    print(f"自检通过：6 族 × 6 条 → {summary['verdict']}；"
          f"11 族 → {over['verdict']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", default="")
    ap.add_argument("--dataset", choices=["banking77"], default="")
    ap.add_argument("--names", default="", help="banking77 README（给则做粗粒度合并）")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        self_test()
        return
    if args.jsonl:
        items, label = load_jsonl(Path(args.jsonl)), Path(args.jsonl).name
    elif args.dataset == "banking77":
        names = Path(args.names) if args.names else None
        items = load_banking77(names)
        label = "banking77 粗粒度（9 族）" if names else "banking77 细粒度（77 类）"
    else:
        ap.print_help()
        raise SystemExit(1)

    summary = summarize(items)
    _print(summary, label)
    raise SystemExit(0 if summary["h1_ok"] else 2)


if __name__ == "__main__":
    main()