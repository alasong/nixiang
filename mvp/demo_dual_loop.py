"""双环重跑：RSI 调研。

第一轮  内环查经验库（空）→ 交外环探索 → 判定 scored → 人工晋升 → 提交快照 v1
第二轮  内环命中晋升后的经验 → 时效核验（数据源截止日是否变化）→ 复用结论

为了能真的闭合环，补了两个最小部件（核心包不动）：
  快照清单   snapshots/v<id>.json —— 不再是裸字符串 vid
  时效核验   抓数据源确认 DATA CUTOFF 未变；变了就写负证据，不许复用
仍缺：自动晋升（本轮是人工）、RuleMiner、淘汰。

跑法：python3 mvp/demo_dual_loop.py
产物：runs/dual-loop-rsi/{iter1,iter2}/、snapshots/
"""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

from .contracts import Judgment, Kind, Status, Step, Trace, Verdict, fingerprint
from .llm import Shot
from .paths import RUNS_DIR
from .persistence import RunStore
from .store import ExperienceItem

ROOT = RUNS_DIR / "dual-loop-rsi"
SNAPS = ROOT / "snapshots"
SOURCE = "https://forecastingresearch.org/research/recursive-self-improvement-v1"
EXPECTED_CUTOFF = "2026-11-03"
EXPECTED_CUTOFF_DATE = date(2026, 11, 3)
TTL_DAYS = 30


def parse_cutoff(text: str) -> str | None:
    """从页面里抠出数据截止日。页面里既有 `DATA CUTOFF: NOV 3, 2026`，
    也有正文的 `our data cutoff of Nov 3, 2026`，两种都要认。"""
    match = re.search(
        r"data\s*cutoff\s*:?\s*(?:of\s+)?([A-Za-z]{3,9}\.?\s+\d{1,2},\s*\d{4})",
        text, re.I)
    return match.group(1) if match else None


def cutoff_matches(seen: str, expected: date = EXPECTED_CUTOFF_DATE) -> bool:
    try:
        return datetime.strptime(seen, "%b %d, %Y").date() == expected
    except ValueError:
        return False


def write_snapshot(vid: str, items: list[ExperienceItem], note: str) -> dict:
    SNAPS.mkdir(parents=True, exist_ok=True)
    payload = {
        "vid": vid,
        "verifier_vid": "vf-scored-selfcheck",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": SOURCE,
        "data_cutoff": EXPECTED_CUTOFF,
        "ttl_days": TTL_DAYS,
        "experience": [it.shot.id for it in items],
        "note": note,
    }
    (SNAPS / f"{vid}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


def check_source() -> tuple[str, str]:
    """时效核验，三态对齐契约 §3.2。

    fresh   源可读且截止日没变 → 快照继续有效
    stale   源可读但截止日变了 → 快照失效，必须写负证据
    unknown 源不可达/页面改版   → 判不了 ≠ 失效，不写负证据
    """
    req = urllib.request.Request(
        SOURCE, headers={"User-Agent": "Mozilla/5.0 (harness-lab TTL check)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8", "replace")
    except Exception as e:
        return "unknown", f"源不可达：{e}"
    match = parse_cutoff(text)
    if match is None:
        return "unknown", "页面里找不到数据截止日"
    if cutoff_matches(match):
        return "fresh", f"页面截止日 {match}"
    return "stale", f"页面截止日 {match}，与快照的 {EXPECTED_CUTOFF} 不一致"


def exploration_trace() -> Trace:
    """第一轮的外环探索：就是上一轮实际做过的四步。"""
    trace = Trace(task_id="TASK-RSI-SURVEY", snapshot_vid="v0",
                  verifier_vid="vf-scored-selfcheck")
    trace.steps = [
        Step("rsi#s1", 0, "search", fingerprint({"queries": 4}),
             {"queries": 4, "sources": 10}, Judgment.OK, 0, 0),
        Step("rsi#s2", 0, "read", fingerprint({"urls": 5}),
             {"full_text": 2, "abstract": 2, "blocked": 1}, Judgment.OK, 0, 0),
        Step("rsi#s3", 0, "synthesize", fingerprint({"sections": 6}),
             {"numbers_cited": 9, "contradictions": 1}, Judgment.OK, 0, 0),
        Step("rsi#s4", 0, "selfcheck", fingerprint({"claims": 6}),
             {"cross_checked": 4, "flagged_unverified": 2}, Judgment.OK, 0, 0),
    ]
    trace.attempts = 1
    trace.outcome = Status.PASS
    return trace


def main() -> None:
    # ---------- 第一轮：内环未命中，交外环 ----------
    sink1 = RunStore(ROOT / "iter1")
    inner = sink1.load_experience()
    miss = inner.retrieve("research-survey", k=5)
    write_snapshot("v0", [], "初始快照：经验库为空")

    trace1 = exploration_trace()
    trace1.steps.insert(0, Step(
        "rsi#s0", 2, "recall", fingerprint({"tag": "research-survey"}),
        {"hit": False, "items": []}, Judgment.OK, 0, 0))
    sink1.append_trace(trace1)

    verdict = Verdict(Status.PASS, Kind.SCORED, 0.75,
                      detail="无客观真值；自检通过，数据截止 2026-11-03")
    method = Shot("RSI-1", "research-survey", {"R-survey"},
                  "调研先并行取证再下结论；数字只用一手来源，自报与第三方分开；结论带数据截止日。")
    findings = Shot("RSI-2", "research-survey", {"R-rsi"},
                    f"RSI 现状（截止 {EXPECTED_CUTOFF}）：AI 已承担实验室大部分编码/实验，"
                    "研究判断仍由人；公开 ECI 未加速（约 14 点/年），内部 METR 估约 2×。")
    scored = ExperienceItem(method, "TASK-RSI-SURVEY", "v0", verdict)
    sink1.append_experience(scored)
    print(f"第一轮  内环检索命中 {len(miss)} 条 → 外环探索 {len(trace1.steps)} 步")

    # 晋升：本 demo 没有自动晋升，模拟人工复核
    promoted = [
        ExperienceItem(method, "TASK-RSI-SURVEY", "v1",
                       Verdict(Status.PASS, Kind.DECISIVE, 0.95, "人工复核晋升")),
        ExperienceItem(findings, "TASK-RSI-SURVEY", "v1",
                       Verdict(Status.PASS, Kind.DECISIVE, 0.90, "人工复核晋升，带时效")),
    ]
    for item in promoted:
        sink1.append_experience(item)
    write_snapshot("v1", promoted, "外环产出晋升为 decisive，附 30 天时效")

    # ---------- 第二轮：内环命中，时效核验后复用 ----------
    sink2 = RunStore(ROOT / "iter2")
    store = sink1.load_experience()          # 内环读的是快照 v1 对应的经验库
    hits = store.retrieve("research-survey", k=5)
    status, detail = check_source()
    fresh = status == "fresh"

    trace2 = Trace(task_id="TASK-RSI-SURVEY", snapshot_vid="v1",
                   verifier_vid="vf-scored-selfcheck")
    trace2.steps = [
        Step("rsi#c1", 2, "recall", fingerprint({"tag": "research-survey"}),
             {"hit": bool(hits), "items": [s.id for s in hits]}, Judgment.OK, 0, 0),
        Step("rsi#c2", 2, "freshness", fingerprint({"source": SOURCE}),
             {"status": status, "detail": detail},
             Judgment.OK if fresh else Judgment.RETRY, 1, 0),
        Step("rsi#c3", 2, "reuse", fingerprint({"vid": "v1"}),
             {"reused": fresh, "new_sources_read": 0 if fresh else None},
             Judgment.OK if fresh else Judgment.RETRY, 0, 0),
    ]
    trace2.attempts = 1
    trace2.outcome = (Status.PASS if fresh
                      else Status.FAIL if status == "stale" else Status.UNKNOWN)
    sink2.append_trace(trace2)
    if status == "stale":
        sink2.append_negative(trace2, reason=f"snapshot_stale: {detail}")

    action = {"fresh": "复用", "stale": "失效，写负证据", "unknown": "判不了，按未验证处理"}[status]
    print(f"第二轮  内环检索命中 {len(hits)} 条 → 时效核验 {detail} → "
          f"{action}（{len(trace2.steps)} 步）")

    # ---------- 对比 ----------
    print("\n两轮对比")
    print(f"  第一轮  步骤 {len(trace1.steps):>2}   模型调用 0   读到来源 2 篇正文 + 2 篇摘要")
    print(f"  第二轮  步骤 {len(trace2.steps):>2}   模型调用 0   读到来源 {'0 篇' if fresh else '需重做'}")
    print(f"  省下     {len(trace1.steps) - len(trace2.steps):>2} 步（检索与通读全部跳过），"
          f"代价是 1 次时效请求")
    print(f"\n产物 {ROOT}/")
    print(f"  iter1/  traces.jsonl {sink1.counts()['traces']} 行  experience.jsonl {sink1.counts()['experience']} 行")
    print(f"  iter2/  traces.jsonl {sink2.counts()['traces']} 行  negative.jsonl {sink2.counts()['negative']} 行")
    print(f"  snapshots/ v0.json v1.json  快照 v1 带 {TTL_DAYS} 天时效")
    print("仍缺：自动晋升（本轮人工）、RuleMiner（从轨迹挖新经验）、淘汰（过期经验的回收）")


if __name__ == "__main__":
    main()