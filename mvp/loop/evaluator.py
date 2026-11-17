"""留出集评估（Evaluator）：先建度量，再谈优化。

和 `runner.main` 的区别：这里把任务切成「训练段 / 留出段」，经验条目与指纹
索引**只用训练段构造**，留出段不参与任何构建。runner.main 是同一批任务上
同时跑三条路径，没有留出，数字会被"见过的任务"抬高。

判据取自《融合双环架构设计 v1.0》§6.4：

    成本降幅 ≥ 40%   且   成功率下降 ≤ 5pp

两条任一不满足即判红。这是硬门：不达标不许进下一段（§7 要求每段指标稳定）。
§6.4 的第三条「复用误伤 ≤ 人工逐条确认」机器判不了，这里只记录，不判定。

用法：python3 -m mvp.evaluator [--split 0.6] [--index 12] [--seed 42]
"""
from __future__ import annotations

import argparse
import random

from ..core.contracts import Status
from ..core.paths import RUNS_DIR
from ..core.persistence import RunStore
from .runner import build_experience, build_index, run_path, run_path_cached
from ..core.store import ExperienceStore
from ..core.tasks import Expense, build_tasks, ground_truth
from ..core.verifier import SetMatchVerifier

COST_GATE = -40.0       # 成本必须至少降 40%
QUALITY_GATE = -5.0     # 成功率最多掉 5pp
EXPENSIVE_TAG = "Evaluator"


def split_tasks(tasks: list[Expense], split: float = 0.6,
                seed: int = 42) -> tuple[list[Expense], list[Expense]]:
    """确定性切分：训练段在前，留出段在后。两段不重叠。"""
    if not 0.0 < split < 1.0:
        raise ValueError("split 必须在 (0, 1) 内")
    ordered = list(tasks)
    random.Random(seed).shuffle(ordered)
    cut = int(len(ordered) * split)
    return ordered[:cut], ordered[cut:]


def gate(baseline: dict, candidate: dict) -> dict:
    """按 §6.4 判定一条改进路径是否过门。两条判据分别记录，不合并成一个分数。"""
    cost_delta = (candidate["avg_cost"] - baseline["avg_cost"]) / baseline["avg_cost"] * 100
    quality_delta = (candidate["success_rate"] - baseline["success_rate"]) * 100
    # 阈值比较带容差：0.75 与 0.80−0.05 在浮点下会差 1e-15，不能让边界误判
    eps = 1e-9
    cost_ok = cost_delta <= COST_GATE + eps
    quality_ok = quality_delta >= QUALITY_GATE - eps
    reasons = []
    if not cost_ok:
        reasons.append(f"成本降幅 {cost_delta:.1f}% 未达 {COST_GATE:.0f}%")
    if not quality_ok:
        reasons.append(f"成功率 {quality_delta:+.1f}pp 低于 {QUALITY_GATE:+.0f}pp")
    return {
        "cost_change_pct": round(cost_delta, 2),
        "quality_change_pp": round(quality_delta, 2),
        "passed": cost_ok and quality_ok,
        "reasons": reasons or ["两条判据均满足"],
    }


def evaluate(tasks: list[Expense] | None = None, split: float = 0.6,
             index_size: int = 12, seed: int = 42,
             store: ExperienceStore | None = None, sink: RunStore | None = None) -> dict:
    """在留出段上评估三条路径，返回读数与判红结果。"""
    tasks = tasks or build_tasks()
    train, heldout = split_tasks(tasks, split=split, seed=seed)
    truth = {t.id: ground_truth(t) for t in tasks}
    verifier = SetMatchVerifier(truth)
    store = store or build_experience()
    # 索引只用训练段构造：留出段的答案不能被提前放进去
    index = build_index(train, truth, k=index_size)

    base = run_path("A 基线（纯模型）", heldout, truth, verifier, store,
                    use_shots=False, sink=sink, name_prefix="E:A")
    enh = run_path("B 检索增强", heldout, truth, verifier, store,
                   use_shots=True, sink=sink, name_prefix="E:B")
    cached = run_path_cached("C 指纹命中", heldout, truth, verifier, store,
                             index, sink=sink)

    return {
        "train": len(train),
        "heldout": len(heldout),
        "paths": {"A": base, "B": enh, "C": cached},
        "gates": {"B": gate(base.stats, enh.stats), "C": gate(base.stats, cached.stats)},
    }


def _print_report(result: dict) -> None:
    base = result["paths"]["A"]
    print(f"留出集评估   训练 {result['train']} 条 / 留出 {result['heldout']} 条"
          f"   （索引与经验只用训练段）")
    print("=" * 76)
    print(f"{'路径':<22}{'成功率':>8}{'平均成本':>10}{'成本变化':>10}{'质量变化':>10}{'判红':>6}")
    print(f"{'A 基线（纯模型）':<22}{base.stats['success_rate']*100:>7.1f}%"
          f"{base.stats['avg_cost']:>10.0f}{'—':>10}{'—':>10}{'—':>6}")
    for key in ("B", "C"):
        path, g = result["paths"][key], result["gates"][key]
        print(f"{path.name:<22}{path.stats['success_rate']*100:>7.1f}%"
              f"{path.stats['avg_cost']:>10.0f}{g['cost_change_pct']:>9.1f}%"
              f"{g['quality_change_pp']:>+9.1f}pp{'通过' if g['passed'] else '不通过':>6}")
    print()
    for key in ("B", "C"):
        g = result["gates"][key]
        print(f"  {key} 判据：{'；'.join(g['reasons'])}")
    print(f"  注：§6.4 的「人工逐条确认复用误伤」机器判不了，本报告不判定")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", type=float, default=0.6, help="训练段占比，默认 0.6")
    ap.add_argument("--index", type=int, default=12, help="指纹索引条数，默认 12")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    sink = RunStore(RUNS_DIR / EXPENSIVE_TAG.lower())
    result = evaluate(split=args.split, index_size=args.index, seed=args.seed, sink=sink)
    _print_report(result)
    print(f"\n产物 → {sink.root}  （{sink.counts()['traces']} 条轨迹）")


if __name__ == "__main__":
    main()