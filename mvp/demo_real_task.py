"""真实任务走一遍沉淀管道：调研 agent harness 最新进展。

这个任务和报销仿真不同：**没有客观真值**，验收只能靠评分型
（自己检查覆盖面、来源交叉、结论是否有证据）。所以：
- verdict.kind = SCORED，confidence 0.7
- 沉淀出的经验是低置信的，进不了高置信层、参与不了检索

这正好演示了置信分层为什么必要：调研类任务的"经验"（怎么搜、怎么交叉
验证）是程序性知识，可以沉淀；但它的结论不能不经验证就复用。

运行：
    python3 -m mvp.demo_real_task            # 追加（默认，符合 append-only 语义）
    python3 -m mvp.demo_real_task --fresh    # 清空后重跑，便于干净复现
"""
from __future__ import annotations

import shutil
import sys

from mvp.contracts import Kind, Judgment, Status, Step, Trace, Verdict, fingerprint
from mvp.paths import RUNS_DIR
from mvp.persistence import RunStore
from mvp.store import ExperienceItem
from mvp.llm import Shot

DEMO_DIR = RUNS_DIR / "real-task-demo"


def main() -> None:
    if "--fresh" in sys.argv and DEMO_DIR.exists():
        shutil.rmtree(DEMO_DIR)
    sink = RunStore(DEMO_DIR)
    trace = Trace(task_id="TASK-HARNESS-SURVEY",
                  snapshot_vid="v0", verifier_vid="vf-scored-selfcheck")

    steps = [
        Step("survey#s1", 0, "search",
             fingerprint({"q": "agent harness 2026 SWE-agent OpenHands runtime"}),
             {"queries": 3, "sources": 9},
             Judgment.OK, 800, 3000),
        Step("survey#s2", 0, "synthesize",
             fingerprint({"inputs": 9, "cross_checked": True}),
             {"key_facts": 20, "contradictions": 0},
             Judgment.OK, 3200, 6000),
        Step("survey#s3", 0, "verify",
             fingerprint({"claims": 20}),
             {"vendor_self_reported_flagged": 3, "single_source_flagged": 1},
             Judgment.OK, 400, 500),
    ]
    trace.steps = steps
    trace.attempts = 1
    trace.outcome = Status.PASS

    verdict = Verdict(
        Status.PASS, Kind.SCORED, 0.7,
        detail="无客观真值；自检通过（来源交叉、厂商自报数字已标注、双来源事实占多数）",
    )

    sink.append_trace(trace)
    if verdict.status != Status.PASS:
        sink.append_negative(trace, reason=_why(trace))

    # 沉淀为经验 —— 但它是 scored，进不了高置信层
    shot = Shot("H1", "research-survey", {"R-survey"},
                "调研类任务先并行搜索再交叉验证；厂商自报数字必须显式标注，不得与第三方测量混用。")
    sink.append_experience(ExperienceItem(
        shot=shot, trace_id="TASK-HARNESS-SURVEY",
        snapshot_vid="v0", verdict=verdict,
    ))

    # 验证分层语义
    store = sink.load_experience()
    high = [it.shot.id for it in store.high_confidence()]
    got = store.retrieve("research-survey", k=5)

    print("=" * 60)
    print("真实任务沉淀演示：调研 agent harness 最新进展")
    print("=" * 60)
    print(f"\n轨迹      3 步 / {sum(s.cost for s in steps)} tokens / outcome=pass")
    print(f"判定      kind={verdict.kind.value}  confidence={verdict.confidence}")
    print(f"          {verdict.detail}")
    print(f"\n沉淀产物 → {sink.root}/")
    for k, v in sink.counts().items():
        print(f"  {k + '.jsonl':<20}{v} 行")
    print(f"\n置信分层检验")
    print(f"  高置信经验条目: {high or '（空）'}   ← scored 经验被正确挡在门外")
    print(f"  检索 'research-survey': {([s.id for s in got] or '（空）')}")
    print("\n含义：这条经验（搜索→交叉验证→标注自报数字）是程序性知识，")
    print("但它未经客观验证，不能像报销域那种 decisive 经验一样直接复用。")
    print("要么人工复核后升级为 decisive，要么永远留在审计层。")


def _why(t: Trace) -> str:
    if t.outcome == Status.UNKNOWN:
        return "verifier_unknown"
    return "failed"


if __name__ == "__main__":
    main()
