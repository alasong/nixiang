"""真实数据上的端到端：Banking77 查询 → 轨迹 → H1 → 巡检 → 混合成本。

和 `tools/real_c_check.py` 的区别：那边只量覆盖率与命中准确率；这里把每条
查询落成轨迹（带任务原文与粗粒度族标签），于是同一条数据流能同时回答：

    H1（标签口径）  按族标签数分布
    H1（聚类口径）  对同一批文本聚类，和标签口径对照
    维护巡检        负证据比例等告警
    混合成本        c×命中成本 + (1-c)×模型成本

全程不调模型：命中用历史标签，未命中只记「转模型」并沿用实测成本参考值。
"""
from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mvp.contracts import Judgment, Status, Step, Trace  # noqa: E402
from mvp.maintenance import run_maintenance  # noqa: E402
from mvp.paths import RUNS_DIR  # noqa: E402
from mvp.persistence import RunStore  # noqa: E402
from mvp.snapshot import SnapshotStore  # noqa: E402

HIT_COST = 10
MODEL_COST = 583        # 前次真实 API 实测（deepseek-chat，77 类目录）参考值
MODEL_ACC = 0.738       # 前次同批样本上的模型准确率
COST_GATE = -40.0       # §6.4
QUALITY_GATE = -5.0


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)          # type: ignore[union-attr]
    return module


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=400, help="写入轨迹的查询样本数")
    ap.add_argument("--tau", type=float, default=0.6, help="写轨迹用的阈值")
    ap.add_argument("--taus", default="", help="另给一组阈值做工作点扫描，如 0.5,0.6,0.7")
    ap.add_argument("--threshold", type=float, default=0.24, help="聚类 H1 的相似度阈值")
    ap.add_argument("--names", default="", help="banking77 README，用于粗粒度族标签")
    ap.add_argument("--dir", default=str(RUNS_DIR / "real-e2e"))
    args = ap.parse_args()

    real_check = _load("real_check", ROOT / "tools" / "real_check.py")
    real_c = _load("real_c_check", ROOT / "tools" / "real_c_check.py")
    family_tool = _load("family_stats", ROOT / "tools" / "family_stats.py")

    texts, labels = real_check.load_banking77()
    rng = np.random.default_rng(0)
    perm = rng.permutation(len(texts))
    cut = int(len(perm) * 0.6)
    idx_set, qry_set = perm[:cut], perm[cut:]
    train_texts = [texts[i] for i in idx_set]
    query_texts = [texts[i] for i in qry_set]
    y_idx, y_q = labels[idx_set], labels[qry_set]

    X, Q = real_check.vectorize(train_texts, query_texts)
    nn, sim = real_check.topk(X, Q, k=1)
    sim1, nn_label = sim[:, 0], y_idx[nn[:, 0]]

    # 粗粒度族标签（没有 names 时用细类编号）
    if args.names:
        names = {i: n for i, n in enumerate(real_c.load_names(Path(args.names)))}
        cmap = real_check.coarse_map(names)
        family_of = lambda label: cmap.get(int(label), "other")   # noqa: E731
        label_note = "粗粒度（9 族）"
    else:
        family_of = lambda label: str(int(label))                 # noqa: E731
        label_note = "细粒度（77 类）"

    # 落轨迹：抽样 n 条查询
    run_dir = Path(args.dir)
    if run_dir.exists():
        shutil.rmtree(run_dir)
    sink = RunStore(run_dir)
    snaps = SnapshotStore(run_dir / "snapshots")
    snaps.commit("vf-b77", [], ttl_days=30, source="real_e2e")

    sample = rng.choice(len(qry_set), size=min(args.n, len(qry_set)), replace=False)
    hits = correct = 0
    family_items = []
    for i in sample:
        text = query_texts[i]
        family = family_of(y_q[i])
        family_items.append({"id": f"Q{i}", "family": family})
        hit = bool(sim1[i] >= args.tau)
        ok = hit and nn_label[i] == y_q[i]
        hits += hit
        correct += ok
        trace = Trace(task_id=f"Q{i}", verifier_vid="vf-b77", task_text=text[:200],
                      outcome=Status.PASS if ok else (Status.UNKNOWN if hit else Status.UNKNOWN))
        trace.steps = [Step(
            f"Q{i}#s1", 0, "lookup" if hit else "miss_to_model",
            f"h{i}", {"hit": hit, "label": str(int(y_q[i]))},
            Judgment.OK if ok else Judgment.RETRY,
            HIT_COST if hit else MODEL_COST, 0)]
        sink.append_trace(trace)
        if hit and not ok:
            sink.append_negative(trace, reason="reuse_wrong")

    coverage = hits / len(sample)
    hit_acc = correct / max(hits, 1)
    mixed_cost = coverage * HIT_COST + (1 - coverage) * MODEL_COST
    cost_change = (mixed_cost - MODEL_COST) / MODEL_COST * 100

    # 工作点扫描：每个 τ 的成本/质量与 §6.4 判红
    operating = []
    if args.taus:
        for tau in [float(x) for x in args.taus.split(",")]:
            hit_mask = sim1[sample] >= tau
            c = float(hit_mask.mean())
            a = float((nn_label[sample][hit_mask] == y_q[sample][hit_mask]).mean()) \
                if hit_mask.any() else 0.0
            mixed = c * HIT_COST + (1 - c) * MODEL_COST
            blended = c * a + (1 - c) * MODEL_ACC
            cost_pct = (mixed - MODEL_COST) / MODEL_COST * 100
            quality_pp = (blended - MODEL_ACC) * 100
            operating.append({
                "tau": tau, "coverage": c, "hit_acc": a, "cost": mixed,
                "cost_pct": cost_pct, "quality_pp": quality_pp,
                "passed": cost_pct <= COST_GATE and quality_pp >= QUALITY_GATE,
            })

    # H1：标签口径 + 聚类口径
    label_h1 = family_tool.summarize(family_items)
    from mvp.self_bootstrap import collect_tasks
    exported = collect_tasks([run_dir / "traces.jsonl"])
    cluster_h1 = _load("cluster_check", ROOT / "tools" / "cluster_check.py").report(
        exported, args.threshold) if exported else {"h1_ok": False, "verdict": "无任务"}

    maintenance = run_maintenance(sink, snaps)

    print("真实数据端到端（Banking77：索引 6001 / 查询 4002）")
    print("=" * 76)
    print(f"查询样本 {len(sample)} 条   命中阈值 τ={args.tau}")
    print(f"覆盖率 {coverage*100:.1f}%   命中准确率 {hit_acc*100:.1f}%")
    print(f"混合成本 {mixed_cost:.0f} tokens/条（命中 {HIT_COST}，未命中参考 {MODEL_COST}）"
          f" → {cost_change:+.1f}%")
    if operating:
        print("\n工作点扫描（§6.4：成本降 ≥40% 且质量掉 ≤5pp）")
        print(f"{'τ':>6}{'覆盖率':>8}{'命中准确':>9}{'混合成本':>9}"
              f"{'成本变化':>10}{'混合准确':>9}{'质量变化':>9}{'判红':>6}")
        for row in operating:
            print(f"{row['tau']:>6.2f}{row['coverage']*100:>7.1f}%"
                  f"{row['hit_acc']*100:>8.1f}%{row['cost']:>9.0f}"
                  f"{row['cost_pct']:>9.1f}%{ (row['coverage']*row['hit_acc'] + (1-row['coverage'])*MODEL_ACC)*100:>8.1f}%"
                  f"{row['quality_pp']:>+8.1f}pp{'通过' if row['passed'] else '不通过':>6}")
        ok = [r for r in operating if r["passed"]]
        if ok:
            best = min(ok, key=lambda r: r["cost"])
            print(f"  结论：推荐工作点 τ={best['tau']:.2f}"
                  f"（成本 {best['cost_pct']:.1f}%，质量 {best['quality_pp']:+.1f}pp）")
        else:
            print("  结论：给出的阈值里没有同时过两道门的")

    print(f"\nH1 标签口径（{label_note}）：{label_h1['verdict']}")
    print(f"  族数 {label_h1['n_families']}  最小 {label_h1['min_size']}  "
          f"前 5 族占比 {label_h1['top5_share']*100:.0f}%")
    print(f"H1 聚类口径（τ={args.threshold}）：{cluster_h1['verdict']}")
    print(f"\n维护巡检：告警 {len(maintenance.alarms)} 条，"
          f"晋升 {len(maintenance.promoted)}，淘汰 {len(maintenance.retired)}")
    for alarm in maintenance.alarms:
        print("  " + str(alarm))
    print(f"\n轨迹 → {run_dir}（{sink.counts()['traces']} 条 / "
          f"负证据 {sink.counts()['negative']} 条）")
    print("注：未命中成本是前次实测参考值，不是本轮新测；Banking77 不是你的业务域。")


if __name__ == "__main__":
    main()