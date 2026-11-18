"""闭环演示：冷启动只会调模型 → 从台账学规则 → 切换成符号优先。

这是整条链的验收演示：没有手写规则，系统自己跑、自己记、自己学、自己切换。
读数要能同时回答三个问题：学到没有（规则数）、质量掉没掉（成功率）、
成本降没降（每条 token）。

合成域声明：标签来自真值引擎（真实域要换人工复核/下游回调），成本按
「查表 10 token」计，模型成本是仿真值。结论只对合成域成立。
"""
from __future__ import annotations

from ..core.contracts import Status
from ..core.llm import SimulatedLLM
from ..core.rules import RuleStore
from ..core.store import ExperienceStore
from ..core.tasks import build_tasks, ground_truth
from ..core.verifier import SetMatchVerifier
from ..loop.backward import BackwardEngine
from ..loop.learner import learn_from_ledger
from ..loop.runner import run_path
from ..loop.validator import replay_and_activate

SYMBOLIC_COST = 10      # 一次指纹/规则查表


def run_demo(n_train: int = 200, n_fresh: int = 200) -> dict:
    """跑一遍闭环，返回读数（不打印，方便测试）。"""
    import tempfile
    from pathlib import Path

    from ..core.persistence import RunStore

    from dataclasses import replace

    train = build_tasks(n_train, seed=42)
    # fresh 换前缀：两个语料的 id 都是 EXP-xxx，不换会被真值表互相覆盖
    fresh = [replace(t, id=f"F-{t.id}") for t in build_tasks(n_fresh, seed=7)]
    truth = {t.id: ground_truth(t) for t in train + fresh}
    verifier = SetMatchVerifier(truth)
    store = ExperienceStore()

    with tempfile.TemporaryDirectory() as d:
        sink = RunStore(Path(d))
        # 阶段 1：冷启动，只会调模型
        cold = run_path("cold", train, truth, verifier, store,
                        use_shots=False, sink=sink)
        for task in train:
            sink.append_label(task.id, sorted(truth[task.id]), source="truth-engine")
        # 阶段 2：从轨迹 + 台账学规则，回放过门才激活
        learned, report = learn_from_ledger(sink)

    rules = RuleStore(())
    activated = [item.rule.id for item in learned
                 if replay_and_activate(rules, item.rule, _samples(train, truth)).ok]
    engine = BackwardEngine(rules)

    # 基线：同一批 fresh 任务，纯模型（逐条成本/结论留作回退时复用）
    baseline = run_path("baseline", fresh, truth, verifier, store, use_shots=False)
    model_cost = {t.task_id: t.total_cost for t in baseline.traces}
    model_pass = {t.task_id: t.outcome == Status.PASS for t in baseline.traces}

    # 阶段 3：切换后，符号优先；判不了才回模型
    sample_cost, sample_pass, fallback = 0, 0, 0
    for task in fresh:
        data = task.__dict__
        if engine.unknown(data):
            fallback += 1
            sample_cost += model_cost[task.id]
            sample_pass += 1 if model_pass[task.id] else 0
            continue
        sample_cost += SYMBOLIC_COST
        sample_pass += 1 if engine.violated(data) == truth[task.id] else 0

    return {
        "learned": len(learned),
        "activated": activated,
        "label_report": report,
        "cold_success": cold.stats["success_rate"],
        "cold_cost": cold.stats["avg_cost"],
        "symbolic_success": sample_pass / n_fresh,
        "symbolic_cost": sample_cost / n_fresh,
        "fallback": fallback,
        "baseline_success": baseline.stats["success_rate"],
        "baseline_cost": baseline.stats["avg_cost"],
    }


def _samples(tasks, truth):
    from ..loop.validator import Sample
    return [Sample(t.id, t.__dict__, frozenset(truth[t.id])) for t in tasks]


def demo() -> None:
    result = run_demo()
    print("闭环演示：冷启动 → 学规则 → 符号优先（合成域）")
    print("=" * 70)
    print(f"阶段 1  冷启动 200 条：成功率 {result['cold_success']:.1%}，"
          f"平均 {result['cold_cost']:.0f} token/条")
    print(f"阶段 2  从轨迹 + 台账学出 {result['learned']} 条规则，"
          f"过激活门 {len(result['activated'])} 条：{result['activated']}")
    print(f"阶段 3  切换后 200 条：成功率 {result['symbolic_success']:.1%}，"
          f"平均 {result['symbolic_cost']:.0f} token/条，回退模型 {result['fallback']} 条")
    print(f"        同批纯模型基线：成功率 {result['baseline_success']:.1%}，"
          f"平均 {result['baseline_cost']:.0f} token/条")
    drop = (result["symbolic_cost"] - result["baseline_cost"]) / result["baseline_cost"]
    print(f"  结论：成本 {drop:+.1%}，质量 "
          f"{(result['symbolic_success'] - result['baseline_success']) * 100:+.1f}pp"
          f"（合成域；成本按查表 {SYMBOLIC_COST} token 计，标签来自真值引擎）")


def main() -> None:
    demo()


if __name__ == "__main__":
    main()