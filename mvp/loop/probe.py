"""哨兵探针（融合 v1.0 §8）：按比例抽任务绕过收敛、直接走探索，比对两路答案。

防的是什么：内环高效地把事做对，也能高效地把事做错。验证器乐观、规则快照
过期时，收敛路径自己发现不了——它只和「当时的快照」对齐。探针是唯一不依赖
收敛自证的在线机制。

判据：两条路径给出的规则集合相同才算一致。不一致的样本落 `probes.jsonl`，
差异率超阈值由维护回路报警，人决定全量重校还是回滚快照。

抽样是确定性的（id + salt 的哈希），不是随机数：同样的任务集每次抽到同一批，
重跑可复现；rate 只是期望比例，不是严格配额。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core.contracts import Trace
from .sampling import selected

PROBE_RATE = 0.01       # 设计值 1%：不到 1% 的预算，挡住最致命的问题
SALT = "probe-v1"


@dataclass(frozen=True)
class ProbeResult:
    """一次探针：同一条任务，收敛答案 vs 探索答案。"""
    task_id: str
    cached: tuple[str, ...]
    model: tuple[str, ...]
    agree: bool
    cost: int

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "cached": list(self.cached),
            "model": list(self.model),
            "agree": self.agree,
            "cost": self.cost,
        }


def should_probe(task_id: str, rate: float = PROBE_RATE, salt: str = SALT) -> bool:
    """确定性抽样：与无偏采样共用同一个原语，只是 salt 不同。"""
    return selected(task_id, rate, salt)


def _answer(trace: Trace) -> tuple[str, ...]:
    """轨迹的最终答案：最后一条带规则输出的步骤。"""
    for step in reversed(trace.steps):
        if isinstance(step.output, list):
            return tuple(sorted(str(x) for x in step.output))
    return ()


def probe_task(task, truth, verifier, store, index, llm=None) -> ProbeResult:
    """对一条任务跑两条路径并比对。不写轨迹（探针是观测，不污染执行轨迹）。"""
    from .runner import run_path, run_path_cached

    cached = run_path_cached("probe:cached", [task], truth, verifier, store, index)
    model = run_path("probe:model", [task], truth, verifier, store,
                     use_shots=False, llm=llm)
    c_trace, m_trace = cached.traces[0], model.traces[0]
    c_ans, m_ans = _answer(c_trace), _answer(m_trace)
    return ProbeResult(
        task_id=task.id,
        cached=c_ans,
        model=m_ans,
        agree=c_ans == m_ans,
        cost=c_trace.total_cost + m_trace.total_cost,
    )


def probe_batch(tasks, truth, verifier, store, index, rate: float = PROBE_RATE,
                llm=None, sink=None) -> tuple[list[ProbeResult], dict]:
    """按比例抽任务跑探针；sink 非空时把结果落 probes.jsonl。"""
    results = [
        probe_task(task, truth, verifier, store, index, llm=llm)
        for task in tasks
        if should_probe(task.id, rate)
    ]
    disagree = [r for r in results if not r.agree]
    summary = {
        "rate": rate,
        "probed": len(results),
        "agree": len(results) - len(disagree),
        "disagree": len(disagree),
        "diff_rate": len(disagree) / len(results) if results else 0.0,
        "cost": sum(r.cost for r in results),
    }
    if sink is not None:
        for result in results:
            sink.append_probe(result)
    return results, summary


def demo() -> None:
    """演示：干净快照 vs 被改坏的快照，探针能不能抓到。"""
    import tempfile
    from pathlib import Path

    from ..core.contracts import Kind, Status, Verdict
    from ..core.llm import SimulatedLLM
    from ..core.persistence import RunStore
    from ..core.store import ExperienceStore
    from ..core.tasks import build_tasks, ground_truth, structural_key
    from .runner import build_index

    class AlwaysPass:
        """乐观验证器：模拟验证器漂移/快照过期——收敛错事也会被放行。"""
        vid = "vf-permissive"

        def check(self, task_id, predicted):
            return Verdict(Status.PASS, Kind.DECISIVE, 1.0, "assume_ok")

    tasks = build_tasks(50)
    truth = {t.id: ground_truth(t) for t in tasks}
    store = ExperienceStore()
    perfect = SimulatedLLM(truth, p_base=1.0, p_shot=1.0)

    def run(index, label: str) -> dict:
        with tempfile.TemporaryDirectory() as d:
            sink = RunStore(Path(d))
            results, summary = probe_batch(tasks, truth, AlwaysPass(), store,
                                           index, rate=1.0, llm=perfect, sink=sink)
            stored = len(sink.load_probes())
        print(f"{label}：探针 {summary['probed']} 条，一致 {summary['agree']}，"
              f"不一致 {summary['disagree']}（差异率 {summary['diff_rate']:.0%}），"
              f"落盘 {stored} 条")
        return summary

    clean = build_index(tasks, truth, k=40)
    run(clean, "干净快照")

    broken = build_index(tasks, truth, k=40)
    for task in tasks[:5]:
        broken[structural_key(task)] = {"R9"}       # 不存在的规则 = 过期/错快照
    run(broken, "改坏 5 条快照")

    # 1% 抽样在 50 条任务上大概抽 0~1 条：这里只验证抽样是确定性的
    sample = [t.id for t in tasks if should_probe(t.id, 0.01)]
    print(f"1% 抽样：50 条里抽中 {len(sample)} 条（确定性，可复现）")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="哨兵探针")
    ap.add_argument("--demo", action="store_true", help="干净/坏快照对照")
    args = ap.parse_args()
    if args.demo:
        demo()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()