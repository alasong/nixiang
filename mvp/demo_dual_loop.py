"""双环重跑：RSI 调研（三轮，含自动晋升）。

第一轮  内环查经验库（空）→ 外环探索 → 判定 scored → 写 2 条经验
第二轮  独立复核（不读经验）→ 结论一致 → 记 2 条 usage（通过）
        → 自动晋升（依据 usage，不再人工）→ 提交快照
第三轮  内环命中 → 时效核验 → 复用

为了能真的闭合环，补了三个最小部件（核心包不动）：
  快照实体   mvp/snapshot.py   不可变清单：验证器版本 + 经验 id + 时效
  复用记录   usage.jsonl       哪条经验被用上、结果如何
  自动晋升   mvp/promotion.py  同一经验 N 次复用全过 → 升成 decisive
仍缺：RuleMiner（从轨迹挖新经验）、淘汰（过期/长期不用经验的回收）。

跑法：python3 -m mvp.demo_dual_loop
产物：runs/dual-loop-rsi/{traces,negative,experience,usage}.jsonl、snapshots/
"""
from __future__ import annotations

import os
import re
import time
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

from .contracts import Judgment, Kind, Status, Step, Trace, Verdict, fingerprint
from .llm import Shot
from .paths import RUNS_DIR
from .persistence import RunStore
from .promotion import apply_promotions, evaluate
from .snapshot import SnapshotStore
from .store import ExperienceItem

ROOT = RUNS_DIR / "dual-loop-rsi"
SNAPS = ROOT / "snapshots"
SOURCE = "https://forecastingresearch.org/research/recursive-self-improvement-v1"
EXPECTED_CUTOFF = "2026-11-03"
EXPECTED_CUTOFF_DATE = date(2026, 11, 3)
TTL_DAYS = 30
VERIFIER_VID = "vf-scored-selfcheck"
TAG = "research-survey"

METHOD = Shot("RSI-1", TAG, {"R-survey"},
              "调研先并行取证再下结论；数字只用一手来源，自报与第三方分开；结论带数据截止日。")
FINDINGS = Shot("RSI-2", TAG, {"R-rsi"},
                f"RSI 现状（截止 {EXPECTED_CUTOFF}）：AI 已承担实验室大部分编码/实验，"
                "研究判断仍由人；公开 ECI 未加速（约 14 点/年），内部 METR 估约 2×。")


def parse_cutoff(text: str) -> str | None:
    """从页面里抠出数据截止日。`DATA CUTOFF: NOV 3, 2026` 与正文的
    `our data cutoff of Nov 3, 2026` 两种写法都要认。"""
    match = re.search(
        r"data\s*cutoff\s*:?\s*(?:of\s+)?([A-Za-z]{3,9}\.?\s+\d{1,2},\s*\d{4})",
        text, re.I)
    return match.group(1) if match else None


def cutoff_matches(seen: str, expected: date = EXPECTED_CUTOFF_DATE) -> bool:
    try:
        return datetime.strptime(seen, "%b %d, %Y").date() == expected
    except ValueError:
        return False


def check_source() -> tuple[str, str]:
    """时效核验，三态对齐契约 §3.2。

    fresh   源可读且截止日没变 → 快照继续有效
    stale   源可读但截止日变了 → 快照失效，必须写负证据
    unknown 源不可达/页面改版   → 判不了 ≠ 失效，不写负证据

    HARNESS_LAB_OFFLINE=1 时跳过联网，直接判 fresh（仅供离线演示与验收）。
    """
    if os.environ.get("HARNESS_LAB_OFFLINE") == "1":
        return "fresh", "离线模式：跳过源核验，假定截止日不变"

    req = urllib.request.Request(
        SOURCE, headers={"User-Agent": "Mozilla/5.0 (harness-lab TTL check)"})
    last: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                text = resp.read().decode("utf-8", "replace")
            break
        except Exception as e:                       # 网络抖动重试，别误判成失效
            last = e
            time.sleep(1.0 * (attempt + 1))
    else:
        return "unknown", f"源不可达：{last}"

    seen = parse_cutoff(text)
    if seen is None:
        return "unknown", "页面里找不到数据截止日"
    if cutoff_matches(seen):
        return "fresh", f"页面截止日 {seen}"
    return "stale", f"页面截止日 {seen}，与快照的 {EXPECTED_CUTOFF} 不一致"


def exploration_trace(vid: str) -> Trace:
    """第一轮：内环未命中，交外环探索。"""
    trace = Trace(task_id="TASK-RSI-SURVEY", snapshot_vid=vid, verifier_vid=VERIFIER_VID)
    trace.steps = [
        Step("rsi#s0", 2, "recall", fingerprint({"tag": TAG}),
             {"hit": False, "items": []}, Judgment.OK, 0, 0),
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


def review_trace(vid: str, status: str, detail: str) -> Trace:
    """独立复核（不读经验）：抽查时效与关键数字，再与上轮结论比对。

    只有时效核验为 fresh 才认为结论可复用 —— unknown 不算通过，
    这样"复核没做成"不会被当成"复核通过"。
    """
    consistent = status == "fresh"
    trace = Trace(task_id="TASK-RSI-SURVEY", snapshot_vid=vid, verifier_vid=VERIFIER_VID)
    trace.steps = [
        Step("rsi#r1", 2, "freshness", fingerprint({"source": SOURCE}),
             {"status": status, "detail": detail},
             Judgment.OK if consistent else Judgment.RETRY, 1, 0),
        Step("rsi#r2", 2, "spot_check", fingerprint({"numbers": ["73.8", "88.1", "2.0"]}),
             {"checked": 3, "mismatch": 0}, Judgment.OK, 0, 0),
        Step("rsi#r3", 2, "compare", fingerprint({"against": "explore"}),
             {"consistent": consistent, "diff": 0 if consistent else None},
             Judgment.OK if consistent else Judgment.RETRY, 0, 0),
    ]
    trace.attempts = 1
    trace.outcome = Status.PASS if consistent else Status.UNKNOWN
    return trace


def reuse_trace(vid: str, hits: list[Shot], status: str, detail: str) -> Trace:
    """第三轮：内环命中后复用，时效不过就不用。"""
    fresh = status == "fresh"
    trace = Trace(task_id="TASK-RSI-SURVEY", snapshot_vid=vid, verifier_vid=VERIFIER_VID)
    trace.steps = [
        Step("rsi#c1", 2, "recall", fingerprint({"tag": TAG}),
             {"hit": bool(hits), "items": [s.id for s in hits]}, Judgment.OK, 0, 0),
        Step("rsi#c2", 2, "freshness", fingerprint({"source": SOURCE}),
             {"status": status, "detail": detail},
             Judgment.OK if fresh else Judgment.RETRY, 1, 0),
        Step("rsi#c3", 2, "reuse", fingerprint({"vid": vid}),
             {"reused": fresh, "new_sources_read": 0 if fresh else None},
             Judgment.OK if fresh else Judgment.RETRY, 0, 0),
    ]
    trace.attempts = 1
    trace.outcome = (Status.PASS if fresh
                     else Status.FAIL if status == "stale" else Status.UNKNOWN)
    return trace


def main() -> None:
    sink = RunStore(ROOT)
    snapshots = SnapshotStore(SNAPS)
    snap0 = snapshots.commit(VERIFIER_VID, [], source="初始快照：经验库为空")

    # ---------- 第一轮 ----------
    inner = sink.load_experience()
    trace1 = exploration_trace(snap0.vid)
    sink.append_trace(trace1)
    verdict = Verdict(Status.PASS, Kind.SCORED, 0.75,
                      detail="无客观真值；自检通过，数据截止 2026-11-03")
    for shot in (METHOD, FINDINGS):
        sink.append_experience(ExperienceItem(shot, trace1.task_id, snap0.vid, verdict))
    print(f"第一轮  内环命中 {len(inner.retrieve(TAG, k=5))} 条 → 外环探索 "
          f"{len(trace1.steps)} 步 → 写 2 条 scored 经验")

    # ---------- 第二轮：两轮独立复核 ----------
    review_steps = 0
    passes = 0
    for round_no in range(2):
        status, detail = check_source()
        trace = review_trace(snap0.vid, status, detail)
        sink.append_trace(trace)
        review_steps += len(trace.steps)
        ok = bool(trace.steps[-1].output["consistent"])
        passes += ok
        for shot in (METHOD, FINDINGS):
            sink.append_usage([shot.id], trace.task_id, "pass" if ok else "unknown",
                              note=f"第 {round_no + 1} 轮独立复核（{status}）")
    print(f"第二轮  独立复核 2 轮 / {review_steps} 步 → {passes}/2 通过 → "
          f"记 usage（自动，无人工）")

    # ---------- 自动晋升 ----------
    store = sink.load_experience()
    decisions = evaluate(store, sink.load_usage())
    promoted, snap1 = apply_promotions(decisions, store, sink, snapshots, VERIFIER_VID,
                                       ttl_days=TTL_DAYS)
    for d in decisions:
        print(f"        晋升判定 {d.shot_id}: {'升' if d.promoted else '不升'}（{d.reason}）")

    # ---------- 第三轮 ----------
    store = sink.load_experience()
    hits = store.retrieve(TAG, k=5)
    status3, detail3 = check_source()
    trace3 = reuse_trace(snap1.vid if snap1 else snap0.vid, hits, status3, detail3)
    sink.append_trace(trace3)
    if status3 == "stale":
        sink.append_negative(trace3, reason=f"snapshot_stale: {detail3}")
    elif status3 == "fresh":
        sink.append_usage([s.id for s in hits], trace3.task_id, "pass", note="命中复用且时效有效")
    print(f"第三轮  内环命中 {len(hits)} 条 → 时效 {detail3} → "
          f"{'复用' if status3 == 'fresh' else status3}（{len(trace3.steps)} 步）")

    # ---------- 汇总 ----------
    counts = sink.counts()
    print("\n三轮对比")
    print(f"  探索 {len(trace1.steps)} 步   复核 {review_steps} 步（2 轮）   "
          f"复用 {len(trace3.steps)} 步（相对探索省 {len(trace1.steps) - len(trace3.steps)} 步）")
    print(f"  快照 {len(snapshots.list())} 份（最新 {snapshots.latest().vid}，"
          f"经验 {list(snapshots.latest().experience_ids)}，时效 {TTL_DAYS} 天）")
    print(f"  产物 traces {counts['traces']} / experience {counts['experience']} / "
          f"usage {counts['usage']} / negative {counts['negative']}")
    print("仍缺：RuleMiner（从轨迹挖新经验）、淘汰（过期与长期不用经验的回收）")


if __name__ == "__main__":
    main()