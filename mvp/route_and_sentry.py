"""路由与哨兵的演示：造一份带问题的历史，看它们给出什么。

演示目录里故意埋了四类问题：
  family-a  3 次复用全过        → 路由应给 hit
  family-b  3 次里 2 次失败     → 路由应给 disable
  family-c  有经验但从未被使用  → 哨兵 dormant_experience
  快照 TTL=0                    → 哨兵 snapshot_expired

用法：python3 -m mvp.route_and_sentry [--demo]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .contracts import Judgment, Kind, Status, Step, Trace, Verdict
from .llm import Shot
from .paths import RUNS_DIR
from .persistence import RunStore
from .router import route_all
from .sentry import inspect
from .snapshot import SnapshotStore
from .store import ExperienceItem


def build_fixture(root: Path) -> tuple[RunStore, SnapshotStore]:
    """造一份确定性的历史（可重复运行；调用方负责先清目录）。"""
    sink = RunStore(root)
    snaps = SnapshotStore(root / "snapshots")

    decisive = Verdict(Status.PASS, Kind.DECISIVE, 1.0, "demo")
    for shot_id, tag in (("A1", "family-a"), ("B1", "family-b"), ("C1", "family-c")):
        sink.append_experience(ExperienceItem(
            Shot(shot_id, tag, set(), f"{tag} 的经验"), f"T-{shot_id}", "v1", decisive))

    snaps.commit("vf0", ["A1", "B1", "C1"], ttl_days=0, source="demo: 故意过期")

    for i in range(3):
        sink.append_usage(["A1"], f"T-A{i}", "pass", "family-a 复用成功")
    sink.append_usage(["B1"], "T-B0", "pass", "family-b 首次成功")
    sink.append_usage(["B1"], "T-B1", "fail", "family-b 复用答错")
    sink.append_usage(["B1"], "T-B2", "fail", "family-b 复用答错")

    ok = Trace(task_id="T-A0", verifier_vid="vf0")
    ok.steps = [Step("t#1", 2, "recall", "h1", {"hit": True}, Judgment.OK, 0, 0)]
    ok.outcome = Status.PASS
    sink.append_trace(ok)

    bad = Trace(task_id="T-B1", verifier_vid="vf0")
    bad.steps = [Step("t#2", 2, "recall", "h2", {"hit": True}, Judgment.RETRY, 5, 0)]
    bad.outcome = Status.FAIL
    sink.append_trace(bad)
    sink.append_negative(bad, reason="reuse_wrong")

    return sink, snaps


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="在 runs/sentry-eval 造一份演示数据")
    ap.add_argument("--dir", default="", help="巡检已有运行目录")
    args = ap.parse_args()

    if args.dir:
        root = Path(args.dir)
        sink, snaps = RunStore(root), SnapshotStore(root / "snapshots")
    elif args.demo:
        root = RUNS_DIR / "sentry-eval"
        sink, snaps = build_fixture(root)
    else:
        ap.print_help()
        raise SystemExit(1)

    print(f"巡检目录 {sink.root}")
    print("=" * 74)
    print(f"{'族':<12}{'使用':>5}{'通过率':>8}{'路由':>9}  依据")
    for d in route_all(sink.load_usage(), sink.load_experience()):
        print(f"{d.tag:<12}{d.stats.uses:>5}{d.stats.pass_rate * 100:>7.0f}%"
              f"{d.path:>9}  {d.reason}")

    alarms = inspect(sink, snaps)
    print(f"\n哨兵告警 {len(alarms)} 条")
    for a in alarms:
        print("  " + str(a))
    if not alarms:
        print("  （无）")


if __name__ == "__main__":
    main()