"""端到端：一条命令跑完整个回路，中间没有人工步骤。

    1. 三路径跑任务        → 轨迹落盘（含 task_text）
    2. 从轨迹自举任务清单  → H1 聚类判定
    3. 留出集评估          → §6.4 判红
    4. 维护回路            → 哨兵 / 晋升 / 淘汰 / 路由
    5. 汇总 + 总门         → H1 成立 且 C 路径过门

注意：这里跑的是**合成域**（差旅报销判定），只能证明「回路能自己走通」，
不能当业务结论。真实域要换成真实任务与真实规则。

用法：python3 -m mvp.e2e [--threshold 0.24]
"""
from __future__ import annotations

import argparse
import importlib.util
import shutil
from pathlib import Path

from ..loop.evaluator import evaluate as evaluate_heldout
from ..loop.maintenance import run_maintenance
from ..core.paths import RUNS_DIR
from ..core.persistence import RunStore
from ..loop.runner import build_experience, build_index, run_path, run_path_cached
from ..loop.self_bootstrap import collect_tasks
from ..core.snapshot import SnapshotStore
from ..core.tasks import build_tasks, ground_truth
from ..core.verifier import SetMatchVerifier

CLUSTER_TOOL = Path(__file__).resolve().parents[2] / "tools" / "cluster_check.py"


def _load_cluster_module():
    spec = importlib.util.spec_from_file_location("cluster_check", CLUSTER_TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)          # type: ignore[union-attr]
    return module


def run_e2e(run_dir: Path, threshold: float = 0.24, split: float = 0.6,
            index_size: int = 12) -> dict:
    if run_dir.exists():
        shutil.rmtree(run_dir)
    sink = RunStore(run_dir)
    snapshots = SnapshotStore(run_dir / "snapshots")

    tasks = build_tasks()
    truth = {t.id: ground_truth(t) for t in tasks}
    verifier = SetMatchVerifier(truth)
    store = build_experience()

    # 1. 跑任务（A 基线与 C 命中，都会写轨迹）
    base = run_path("A 基线", tasks, truth, verifier, store, use_shots=False,
                    sink=sink, name_prefix="E2E:A")
    cached = run_path_cached("C 命中", tasks, truth, verifier, store,
                             build_index(tasks, truth, k=index_size), sink=sink)

    # 2. 自举任务清单 → H1（集群检测器直接调用，不依赖子进程）
    exported = collect_tasks([run_dir / "traces.jsonl"])
    h1 = _load_cluster_module().report(exported, threshold) if exported else {
        "h1_ok": False, "verdict": "没有任务描述可聚类", "n_items": 0}

    # 3. 留出集评估（自己的训练/留出切分，索引只用训练段）
    heldout = evaluate_heldout(tasks=tasks, split=split, index_size=index_size)
    gates = heldout["gates"]

    # 4. 维护回路（这份目录里还没有 usage/经验，预期无改动，但会出告警）
    maintenance = run_maintenance(sink, snapshots)

    overall = bool(h1.get("h1_ok")) and gates["C"]["passed"]
    return {
        "run_dir": run_dir,
        "tasks": len(tasks),
        "traces": sink.counts()["traces"],
        "exported": len(exported),
        "h1": h1,
        "base": base.stats,
        "cached": cached.stats,
        "gates": gates,
        "maintenance": maintenance,
        "overall": overall,
    }


def _print_summary(result: dict) -> None:
    h1, gates, maint = result["h1"], result["gates"], result["maintenance"]
    print("端到端回路（合成域：差旅报销判定）")
    print("=" * 72)
    print(f"任务 {result['tasks']} 条 → 轨迹 {result['traces']} 条"
          f" → 自举任务清单 {result['exported']} 条")
    print(f"H1 聚类：{h1.get('n_items', 0)} 条 / {h1.get('n_clusters', 0)} 簇"
          f" → {'成立' if h1.get('h1_ok') else '不成立'}")
    print(f"  {h1.get('verdict', '')}")
    print(f"留出集：A 成功率 {result['base']['success_rate']*100:.1f}% / "
          f"平均成本 {result['base']['avg_cost']:.0f}")
    for key in ("B", "C"):
        g = gates[key]
        print(f"  {key} 路径：成本 {g['cost_change_pct']:+.1f}%  "
              f"质量 {g['quality_change_pp']:+.1f}pp  → "
              f"{'过门' if g['passed'] else '不过门'}")
    print(f"维护回路：告警 {len(maint.alarms)} 条，晋升 {len(maint.promoted)}，"
          f"淘汰 {len(maint.retired)}")
    if result["overall"]:
        print("\n总门：通过（H1 成立 且 C 过门）")
    else:
        why = "H1 不成立" if not result["h1"].get("h1_ok") else "C 路径未过门"
        print(f"\n总门：未通过（{why}）")
        if not result["h1"].get("h1_ok"):
            print("  合成域只有报销一个任务族，聚类必然并成 1 簇——"
                  "判据在单域任务流上就该拒绝放行。")
    print("注：合成域只证明回路能走通，不是业务结论。")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.24)
    ap.add_argument("--dir", default=str(RUNS_DIR / "e2e"))
    ap.add_argument("--strict", action="store_true",
                    help="门未通过时以退出码 1 结束（默认只看回路是否跑完）")
    args = ap.parse_args()

    result = run_e2e(Path(args.dir), threshold=args.threshold)
    _print_summary(result)
    if args.strict and not result["overall"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()