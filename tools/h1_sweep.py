"""H1 阈值扫描：同一批任务在不同相似度阈值下的簇结构。

H1 的判据（README）不是"有没有簇"，而是"簇的形状对不对"：

    5–8 簇、每簇 ≥5 条      → 有分布，可建复用库
    簇数很多、每簇 1–2 条    → 没有分布，换单任务高质量执行思路
    全并成 1–2 个大簇        → 阈值偏低，或任务描述太笼统

所以阈值不是拍的：同一批任务在不同 τ 下可能给出完全不同的结论。这个脚本
把 τ 扫一遍，告诉你"有没有一个阈值能让判据成立"。

用法：
    .venv/bin/python tools/h1_sweep.py --dataset banking77 --n 200
    .venv/bin/python tools/h1_sweep.py --jsonl tasks.jsonl --taus 0.2,0.3,0.4
    python3 tools/h1_sweep.py --self-test
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TAUS = "0.20,0.25,0.30,0.35,0.40"


def _load_cluster_module():
    spec = importlib.util.spec_from_file_location(
        "cluster_check", Path(__file__).resolve().parent / "cluster_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)          # type: ignore[union-attr]
    return module


def load_jsonl(path: Path) -> list[dict]:
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            items.append(json.loads(line))
    return items


def load_banking77(n: int, seed: int = 0) -> list[dict]:
    """抽样真实任务文本。需要 .venv（pandas + pyarrow）。"""
    import pandas as pd

    df = pd.read_parquet(ROOT / "data" / "b77_train.parquet")
    rng = random.Random(seed)
    idx = rng.sample(range(len(df)), min(n, len(df)))
    rows = df.iloc[idx]
    return [{"id": f"B{i}", "text": str(text), "domain": str(label)}
            for i, (text, label) in enumerate(zip(rows["text"], rows["label"]))]


def sweep(items: list[dict], taus: list[float]) -> list[dict]:
    cluster_mod = _load_cluster_module()
    rows = []
    for tau in taus:
        r = cluster_mod.report(items, tau)
        rows.append({
            "tau": tau,
            "n_clusters": r["n_clusters"],
            "min_size": r["min_size"],
            "sizes": sorted(r["sizes"], reverse=True),
            "h1_ok": r["h1_ok"],
            "verdict": r["verdict"],
        })
    return rows


def summarize(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["h1_ok"]]
    if ok:
        best = min(ok, key=lambda r: abs(r["n_clusters"] - 6))
        return {"h1_ok": True, "tau": best["tau"], "detail": best["verdict"]}
    nearest = min(rows, key=lambda r: (abs(r["n_clusters"] - 6), -r["min_size"]))
    return {"h1_ok": False, "tau": nearest["tau"],
            "detail": f"没有任何阈值满足 5–8 簇且最小 ≥5；最接近的 τ={nearest['tau']:.2f} "
                      f"给出 {nearest['n_clusters']} 簇、最小 {nearest['min_size']} 条"}


def _print(rows: list[dict], summary: dict, n_items: int) -> None:
    print(f"H1 阈值扫描   样本 {n_items} 条")
    print("=" * 74)
    print(f"{'τ':>6}{'簇数':>6}{'最小簇':>8}{'最大簇':>8}  判定")
    for r in rows:
        print(f"{r['tau']:>6.2f}{r['n_clusters']:>6}{r['min_size']:>8}"
              f"{r['sizes'][0]:>8}  {'成立' if r['h1_ok'] else '不成立'}")
    print()
    print(f"结论：{'成立，推荐 τ=' + format(summary['tau'], '.2f') if summary['h1_ok'] else '不成立'}")
    print(f"  {summary['detail']}")


def self_test() -> None:
    """用内置 36 条示例验证扫描器本身可用（不依赖第三方库）。"""
    cluster_mod = _load_cluster_module()
    rows = sweep(cluster_mod.DEMO and [{"id": str(i), "text": t, "domain": d}
                                       for i, (d, t) in enumerate(cluster_mod.DEMO)],
                 [0.24])
    if not rows[0]["h1_ok"]:
        print(f"自检失败：内置示例在 τ=0.24 应成立，实际 {rows[0]['verdict']}")
        raise SystemExit(1)
    print(f"自检通过：内置示例 τ=0.24 → {rows[0]['n_clusters']} 簇，"
          f"最小 {rows[0]['min_size']} 条")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", default="", help="任务清单 jsonl（每行 id/text/domain）")
    ap.add_argument("--dataset", choices=["banking77"], default="")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--taus", default=DEFAULT_TAUS)
    ap.add_argument("--seeds", default="",
                    help="多抽样稳定性检查，如 0,1,2,3（只在 --dataset 下生效）")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        self_test()
        return
    taus = [float(x) for x in args.taus.split(",")]
    if args.jsonl:
        items = load_jsonl(Path(args.jsonl))
    elif args.dataset == "banking77":
        items = load_banking77(args.n, args.seed)
    else:
        ap.print_help()
        raise SystemExit(1)

    if args.seeds:
        if not args.dataset:
            print("--seeds 需要配 --dataset", file=sys.stderr)
            raise SystemExit(1)
        seeds = [int(x) for x in args.seeds.split(",")]
        stats: dict[float, list] = {tau: [] for tau in taus}
        for seed in seeds:
            sample = load_banking77(args.n, seed)
            for row in sweep(sample, taus):
                stats[row["tau"]].append(row)
        print(f"H1 稳定性   样本 {args.n} 条 × {len(seeds)} 次抽样")
        print("=" * 74)
        print(f"{'τ':>6}{'成立次数':>10}{'平均簇数':>10}{'平均最小簇':>12}")
        for tau in taus:
            rs = stats[tau]
            ok = sum(1 for r in rs if r["h1_ok"])
            avg_c = sum(r["n_clusters"] for r in rs) / len(rs)
            avg_min = sum(r["min_size"] for r in rs) / len(rs)
            print(f"{tau:>6.2f}{ok:>7}/{len(rs)}{avg_c:>13.1f}{avg_min:>12.1f}")
        best = max(taus, key=lambda t: sum(1 for r in stats[t] if r["h1_ok"]))
        hold = sum(1 for r in stats[best] if r["h1_ok"])
        print()
        print(f"结论：最稳的是 τ={best:.2f}，在 {len(seeds)} 次抽样中成立 {hold} 次。"
              + ("判据对抽样敏感，单次判定不可信。" if 0 < hold < len(seeds) else ""))
        raise SystemExit(0 if hold == len(seeds) else 2)

    rows = sweep(items, taus)
    summary = summarize(rows)
    _print(rows, summary, len(items))
    raise SystemExit(0 if summary["h1_ok"] else 2)


if __name__ == "__main__":
    main()