"""MVP 实验：基线与增强路径对照。

对应架构文档 §6：
  S3 基线批跑 -> S5 对照实验
验证 H3：复用（检索经验）能否降本且不掉质量。
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .budget import Budget, Overdraft
from ..core.contracts import Judgment, Kind, Status, Step, Trace, Verdict, fingerprint
from ..core.llm import LLMClient, Shot, SimulatedLLM
from ..core.paths import RUNS_DIR
from ..core.persistence import RunStore
from ..core.store import ExperienceStore, ExperienceItem, Recorder
from ..core.tasks import Expense, build_tasks, ground_truth, structural_key
from ..core.verifier import SetMatchVerifier

MAX_ATTEMPTS = 3
SEED = 7


def now_tag() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")

# 手工经验：5 条，覆盖 5 个场景标签，刻意留空 tier2-mid 用于验证零样本降级
SHOTS = [
    Shot("E1", "tier1-high", {"R1"},
         "金额超 5000 时必须核对总监审批栏，未签即判 R1。注意审批与金额是两个独立条件。"),
    Shot("E2", "tier1-mid", {"R2"},
         "一线城市住宿上限 600 元/晚，超过即判 R2，不看总价只看单晚单价。"),
    Shot("E3", "tier2-high", {"R2", "R3"},
         "非一线城市住宿上限 400 元/晚；交通工具仅经济舱与二等座合规，商务舱与一等座均判 R3。"),
    Shot("E4", "tier2-low", {"R4", "R5"},
         "餐补按出差天数 × 100 封顶，超出判 R4；发票日期必须落在行程区间内，越界判 R5。"),
    Shot("E5", "tier1-low", {"R6", "R7"},
         "系统提示同日重复即判 R6，与金额无关；报销月份与行程月份不同且无说明即判 R7。"),
]


@dataclass
class PathResult:
    name: str
    traces: list[Trace]
    stats: dict


def run_path(
    name: str,
    tasks: list[Expense],
    truth: dict[str, set[str]],
    verifier: SetMatchVerifier,
    store: ExperienceStore,
    use_shots: bool,
    p_base: float = 0.88,
    p_shot: float = 0.965,
    base_prompt: int = 550,
    shot_cost: int = 180,
    retry_overhead: int = 0,
    sink: "RunStore | None" = None,
    name_prefix: str = "",
    llm: LLMClient | None = None,
    budget: Budget | None = None,
) -> PathResult:
    """跑一条路径。不传 llm 时用仿真模型；传 budget 时按契约 §4 记账。

    预算按「任务的最坏情况上界」拦截：一轮任务最多 MAX_ATTEMPTS 次调用，
    取不够就直接判 unknown（判不了 ≠ 失败），不先花再算。这样禁止透支
    不靠事后对账。
    """
    llm = llm or SimulatedLLM(
        truth, seed=SEED, p_base=p_base, p_shot=p_shot,
        base_prompt=base_prompt, shot_cost=shot_cost,
    )
    recorder = Recorder()

    def persist(trace: Trace) -> None:
        if sink is None:
            return
        t = trace
        if name_prefix:
            t = Trace(task_id=f"{name_prefix}:{trace.task_id}",
                      steps=trace.steps, snapshot_vid=trace.snapshot_vid,
                      verifier_vid=trace.verifier_vid,
                      outcome=trace.outcome, attempts=trace.attempts,
                      task_text=trace.task_text)
        sink.append_trace(t)
        if t.outcome != Status.PASS:
            sink.append_negative(t, reason=_why(t))

    for task in tasks:
        trace = Trace(task_id=task.id, verifier_vid=verifier.vid,
                      task_text=task.render()[:200])
        shots = store.retrieve(task.scene_tag(), k=2) if use_shots else []
        text = task.render()

        # 契约 §4：先用最坏情况上界拦一道，不够就不开跑
        if budget is not None:
            bound = MAX_ATTEMPTS * (base_prompt + shot_cost * len(shots) + 166) \
                + 2 * retry_overhead
            if budget.remaining < bound:
                trace.steps.append(Step(
                    node_id=f"{task.id}#budget", level=2, action="budget_gate",
                    input_hash=fingerprint({"remaining": budget.remaining, "need": bound}),
                    output={"remaining": budget.remaining, "need": bound},
                    judgment=Judgment.GIVEUP, cost=0, latency_ms=0))
                trace.outcome = Status.UNKNOWN
                recorder.add(trace)
                persist(trace)
                continue

        for attempt in range(1, MAX_ATTEMPTS + 1):
            res = llm.extract(text, shots)
            verdict = verifier.check(task.id, res.rules)
            # 重试的固定开销：调度、失败处理、用户等待的当量成本
            extra = retry_overhead if attempt > 1 else 0

            trace.steps.append(Step(
                node_id=f"{task.id}#a{attempt}",
                level=0,
                action="extract",
                input_hash=fingerprint({"text": text, "shots": [s.id for s in shots]}),
                output=sorted(res.rules),
                judgment=Judgment.OK if verdict.status == Status.PASS else Judgment.RETRY,
                cost=res.total_tokens + extra,
                latency_ms=180 + 40 * len(shots),
                nondeterministic=attempt > 1,   # 契约 §5：重采样即脱确定域
            ))
            if budget is not None:
                budget.spend(res.total_tokens + extra)
            trace.attempts = attempt

            if verdict.status == Status.PASS:
                trace.outcome = Status.PASS
                break
            if verdict.status == Status.UNKNOWN:
                break
        else:
            trace.outcome = Status.FAIL

        recorder.add(trace)
        persist(trace)

    return PathResult(name, recorder.traces, recorder.stats())


def _why(t: Trace) -> str:
    """失败归因。契约要求负证据写明「为什么不通」，否则下一轮会重走死路。"""
    if t.outcome == Status.UNKNOWN:
        return "verifier_unknown"
    if t.attempts >= MAX_ATTEMPTS:
        return "attempts_exhausted"
    return "failed"


def build_experience() -> ExperienceStore:
    store = ExperienceStore()
    for s in SHOTS:
        store.add(ExperienceItem(
            shot=s,
            trace_id=f"manual-{s.id}",
            snapshot_vid="v0",
            verdict=Verdict(Status.PASS, Kind.DECISIVE, 1.0, "manual"),
        ))
    return store


def compare(base: PathResult, enh: PathResult) -> dict:
    cost_delta = (enh.stats["avg_cost"] - base.stats["avg_cost"]) / base.stats["avg_cost"]
    succ_delta = enh.stats["success_rate"] - base.stats["success_rate"]
    return {
        "cost_change_pct": round(cost_delta * 100, 2),
        "success_change_pp": round(succ_delta * 100, 2),
        "cost_ok": cost_delta <= -0.40,
        "quality_ok": succ_delta >= -0.05,
    }


def context_grid(tasks, truth, verifier, store, base_prompts, shot_costs,
                 retry_overhead: int = 0) -> list[dict]:
    """二维分析：基础 prompt 长度 × 单条示例成本。

    假设：检索增强的经济性由「示例占 prompt 的比例」决定，
    而不是由「准确率提升幅度」决定。这张表用来验证该假设。
    """
    rows = []
    for bp in base_prompts:
        for sc in shot_costs:
            b = run_path("b", tasks, truth, verifier, store, False,
                         base_prompt=bp, shot_cost=sc, retry_overhead=retry_overhead)
            e = run_path("e", tasks, truth, verifier, store, True,
                         base_prompt=bp, shot_cost=sc, retry_overhead=retry_overhead)
            c = compare(b, e)
            rows.append({
                "base_prompt": bp, "shot_cost": sc,
                "prompt_ratio": round(sc * 2 / bp, 3),
                "cost_change_pct": c["cost_change_pct"],
                "success_change_pp": c["success_change_pp"],
                "viable": c["cost_ok"] and c["quality_ok"],
            })
    return rows


def run_path_cached(
    name: str,
    tasks: list[Expense],
    truth: dict[str, set[str]],
    verifier: SetMatchVerifier,
    store: ExperienceStore,
    index: dict[str, set[str]],
    sink: "RunStore | None" = None,
) -> PathResult:
    """路径 C：规则命中。

    先查结构指纹，命中则直接复用结论，不调模型（成本按一次哈希比对计）。
    未命中才走模型。

    这是唯一能实现数量级降本的机制——降本来自「不调用」，不来自「调得更好」。
    """
    llm = SimulatedLLM(truth, seed=SEED)
    recorder = Recorder()
    hits = 0

    for task in tasks:
        trace = Trace(task_id=task.id, verifier_vid=verifier.vid)
        key = structural_key(task)

        if key in index:
            hits += 1
            predicted = set(index[key])
            verdict = verifier.check(task.id, predicted)
            trace.steps.append(Step(
                node_id=f"{task.id}#hit",
                level=2,
                action="rule_hit",
                input_hash=fingerprint({"key": key}),
                output=sorted(predicted),
                judgment=Judgment.RULE_HIT if verdict.status == Status.PASS else Judgment.RULE_UNCERTAIN,
                cost=10,          # 哈希 + 查表
                latency_ms=2,
                nondeterministic=False,
            ))
            if verdict.status == Status.PASS:
                trace.attempts = 1
                trace.outcome = Status.PASS
                recorder.add(trace)
                if sink is not None:
                    sink.append_trace(trace)
                continue
            # 命中但验证不通过 -> 不信任该命中，降级走模型（契约：失败只降级不重来）
            hits -= 1

        text = task.render()
        for attempt in range(1, MAX_ATTEMPTS + 1):
            res = llm.extract(text, [])
            verdict = verifier.check(task.id, res.rules)
            trace.steps.append(Step(
                node_id=f"{task.id}#a{attempt}",
                level=0,
                action="extract",
                input_hash=fingerprint({"text": text}),
                output=sorted(res.rules),
                judgment=Judgment.OK if verdict.status == Status.PASS else Judgment.RETRY,
                cost=res.total_tokens,
                latency_ms=180,
                nondeterministic=attempt > 1,
            ))
            trace.attempts = attempt
            if verdict.status == Status.PASS:
                trace.outcome = Status.PASS
                break
        else:
            trace.outcome = Status.FAIL
        recorder.add(trace)
        if sink is not None:
            sink.append_trace(trace)
            if trace.outcome != Status.PASS:
                sink.append_negative(trace, reason=_why(trace))

    stats = recorder.stats()
    stats["hit_rate"] = round(hits / len(tasks), 4)
    return PathResult(name, recorder.traces, stats)


def build_index(tasks: list[Expense], truth: dict[str, set[str]], k: int) -> dict[str, set[str]]:
    """按出现顺序取前 k 个去重后的结构指纹建索引。

    k 决定覆盖率：k 越大命中率越高，但冷启动成本也越高（这些指纹都是被处理过的）。
    """
    ordered: dict[str, set[str]] = {}
    for t in tasks:
        key = structural_key(t)
        if key not in ordered:
            ordered[key] = truth[t.id]
    return {key: val for i, (key, val) in enumerate(ordered.items()) if i < k}


def main() -> None:
    tasks = build_tasks()
    truth = {t.id: ground_truth(t) for t in tasks}
    verifier = SetMatchVerifier(truth)
    store = build_experience()

    run_dir = RUNS_DIR / now_tag()
    sink = RunStore(run_dir)
    for it in store.all_items():
        sink.append_experience(it)

    base = run_path("A 基线（纯模型）", tasks, truth, verifier, store,
                    use_shots=False, sink=sink, name_prefix="A")
    enh = run_path("B 检索增强（模型+few-shot）", tasks, truth, verifier, store,
                   use_shots=True, sink=sink, name_prefix="B")
    cached = run_path_cached("C 规则命中（指纹复用）", tasks, truth, verifier, store,
                             build_index(tasks, truth, k=12), sink=sink)

    print("=" * 68)
    print("MVP 三条路径对照（仿真模型，参数见 llm.py，非真实测量）")
    print("=" * 68)
    for r in (base, enh, cached):
        s = r.stats
        extra = f"  命中率 {s['hit_rate']*100:.0f}%" if "hit_rate" in s else ""
        print(f"\n{r.name}{extra}")
        print(f"  成功率 {s['success_rate']*100:.1f}%   平均成本 {s['avg_cost']:.0f} tokens   平均尝试 {s['avg_attempts']:.2f} 次")

    print("\n" + "-" * 68)
    for name, r in (("B vs A", enh), ("C vs A", cached)):
        c = compare(base, r)
        verdict = "成立" if c["cost_ok"] and c["quality_ok"] else "不成立"
        print(f"{name}   成本 {c['cost_change_pct']:+7.2f}%   成功率 {c['success_change_pp']:+6.2f}pp   H3 {verdict}")

    print("\n" + "-" * 68)
    print("敏感性一：示例占 prompt 的比例，决定 B 路径是否划算")
    print(f"{'基础prompt':>10} {'示例成本':>8} {'占比':>7} {'成本变化':>10} {'成功率':>9} {'结论':>6}")
    for r in context_grid(tasks, truth, verifier, store,
                          [400, 800, 1600, 3200], [60, 120, 180, 300]):
        print(f"{r['base_prompt']:>10} {r['shot_cost']:>8} "
              f"{r['prompt_ratio']*100:>6.1f}% {r['cost_change_pct']:>9.2f}% "
              f"{r['success_change_pp']:>8.2f}pp {'成立' if r['viable'] else '—':>6}")

    print("\n" + "-" * 68)
    print("敏感性二：索引覆盖率（对应命中率）决定 C 路径的降本幅度")
    print(f"{'索引条数':>8} {'命中率':>8} {'平均成本':>10} {'成本变化':>10} {'成功率':>9}")
    for k in (4, 8, 12, 16, 18):
        r = run_path_cached("x", tasks, truth, verifier, store, build_index(tasks, truth, k))
        c = compare(base, r)
        print(f"{k:>8} {r.stats['hit_rate']*100:>7.0f}% {r.stats['avg_cost']:>10.0f} "
              f"{c['cost_change_pct']:>9.2f}% {c['success_change_pp']:>8.2f}pp")

    d = sink.counts()
    print("\n" + "=" * 68)
    print(f"沉淀产物 → {run_dir}/")
    print(f"  traces.jsonl      {d['traces']:>3} 行   执行轨迹：双版本号 + 离散判断点 + 步级成本")
    print(f"  negative.jsonl    {d['negative']:>3} 行   负证据：失败路径 + 输入指纹 + 归因")
    print(f"  experience.jsonl  {d['experience']:>3} 行   经验条目：带 (trace_id, vid, verdict) 三元组")
    print("  以上三者是 append-only，进程重启后可从文件恢复（见 test_integration.py）")


if __name__ == "__main__":
    main()
