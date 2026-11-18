"""从负证据挖候选短路规则（RuleMiner 的最小可用版）。

输入：negative.jsonl 的 input_hashes + traces.jsonl 的成功记录
产出：候选规则（某输入指纹 → 直接判死路），带 support / counter

判据：同一 input_hash 至少失败 min_support 次，且在成功轨迹里出现 0 次。
counter 必须为 0 —— 出现过成功的指纹不能短路，否则就是误伤。

只挖候选，不激活（v0.1 §7：candidate → 回放通过 → active）。激活要人工或
RuleValidator，本轮不做。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

MIN_SUPPORT = 2


@dataclass(frozen=True)
class Candidate:
    input_hash: str
    support: int
    counter: int
    verdict: str = "shortcut_negative"
    reason: str = ""

    def __str__(self) -> str:
        return (f"{self.input_hash}  support={self.support}  counter={self.counter}"
                f"  → {self.verdict}")


def mine(sink, min_support: int = MIN_SUPPORT) -> list[Candidate]:
    """扫负证据与成功轨迹，产出可以短路的指纹。"""
    failures: Counter[str] = Counter()
    for rec in sink.load_negative():
        for digest in rec.get("input_hashes", []):
            failures[digest] += 1

    successes: Counter[str] = Counter()
    for trace in sink.load_traces():
        if trace.outcome.value != "pass":
            continue
        for step in trace.steps:
            successes[step.input_hash] += 1

    candidates = [
        Candidate(digest, support, successes.get(digest, 0))
        for digest, support in failures.items()
        if support >= min_support and successes.get(digest, 0) == 0
    ]
    return sorted(candidates, key=lambda c: (-c.support, c.input_hash))


def rejected(sink, min_support: int = MIN_SUPPORT) -> list[Candidate]:
    """没能成为候选的指纹，附上被拒的理由。给人看"为什么没挖出来"。"""
    failures: Counter[str] = Counter()
    for rec in sink.load_negative():
        for digest in rec.get("input_hashes", []):
            failures[digest] += 1
    successes: Counter[str] = Counter()
    for trace in sink.load_traces():
        if trace.outcome.value != "pass":
            continue
        for step in trace.steps:
            successes[step.input_hash] += 1

    out = []
    for digest, support in failures.items():
        counter = successes.get(digest, 0)
        if support >= min_support and counter == 0:
            continue
        if counter:
            reason = f"有 {counter} 次成功记录，短路会误伤"
        else:
            reason = f"support {support} < {min_support}"
        out.append(Candidate(digest, support, counter, reason=reason))
    return sorted(out, key=lambda c: (-c.support, c.input_hash))

def _fixture(root) -> "RunStore":
    """造一份演示历史：重复失败、偶发失败、只失败一次。"""
    import shutil

    from ..core.contracts import Judgment, Status, Step, Trace
    from ..core.persistence import RunStore

    if root.exists():
        shutil.rmtree(root)
    sink = RunStore(root)

    def write(task_id: str, digest: str, ok: bool) -> None:
        trace = Trace(task_id=task_id, verifier_vid="vf0")
        trace.steps = [Step("t#1", 2, "recall", digest, {"hit": True},
                            Judgment.OK if ok else Judgment.RETRY, 0 if ok else 5, 0)]
        trace.outcome = Status.PASS if ok else Status.FAIL
        sink.append_trace(trace)
        if not ok:
            sink.append_negative(trace, reason="reuse_wrong")

    write("T1", "BAD1", ok=False)      # 同一指纹连挂两次 → 候选
    write("T2", "BAD1", ok=False)
    write("T3", "MIXED", ok=False)     # 有成功记录 → 不能短路
    write("T4", "MIXED", ok=True)
    write("T5", "ONCE", ok=False)      # 只挂一次 → support 不足
    return sink


def demo() -> None:
    from ..core.paths import RUNS_DIR

    sink = _fixture(RUNS_DIR / "miner-eval")
    print(f"候选短路规则挖掘（{sink.root}）")
    print("=" * 66)
    candidates = mine(sink)
    print(f"候选 {len(candidates)} 条（support ≥ {MIN_SUPPORT} 且无成功记录）")
    for c in candidates:
        print("  " + str(c))
    for c in rejected(sink):
        print(f"  排除 {c.input_hash}：{c.reason}")

    # 挖掘只是提名，能不能激活由回放验证器判（§7：support ≥ 5、counter = 0）
    from .validator import validate_candidates
    decisions = validate_candidates(sink)
    print()
    print(f"激活验证（{len(decisions)} 条候选）")
    for d in decisions:
        print(f"  {d.report.name} 支持 {d.report.support} → "
              f"{'激活' if d.ok else '拒绝'}：{'; '.join(d.reasons) or '无'}")


if __name__ == "__main__":
    demo()
