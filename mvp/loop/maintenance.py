"""维护回路：一条命令跑完巡检、晋升、淘汰、路由决策。

之前这些是四个孤立模块，运维要手工串。这里按固定顺序跑一遍：

    哨兵巡检 → 自动晋升 → 经验淘汰 → 路由决策

顺序有讲究：先巡检留下"体检前"的告警（晋升会提交新快照，过期告警可能随之
消失），再晋升（把经得起复用的经验升成 decisive），再淘汰（不可靠的清出去），
最后按聚合统计给路由结论。

只写三样东西：快照、经验追加行、淘汰墓碑。都可重复执行——第二次跑不会再
晋升同一条、也不会重复写墓碑。
"""
from __future__ import annotations

import argparse
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..core.contracts import Kind, Status, Verdict
from ..core.llm import Shot
from ..core.paths import RUNS_DIR
from ..core.persistence import RunStore
from .promotion import apply_promotions, evaluate as evaluate_promotions
from .retire import apply as apply_retirements, evaluate as evaluate_retirements
from .router import route_all
from .sentry import inspect
from ..core.snapshot import SnapshotStore
from ..core.store import ExperienceItem

VERIFIER_VID = "vf0"


@dataclass
class Report:
    alarms: list = field(default_factory=list)
    promoted: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    routes: list = field(default_factory=list)
    snapshot: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.promoted or self.retired)


def run_maintenance(sink: RunStore, snapshots: SnapshotStore) -> Report:
    report = Report()

    # 1. 巡检：先留一份体检前快照
    report.alarms = inspect(sink, snapshots)

    # 2. 晋升：复用记录够格的经验升成 decisive
    store = sink.load_experience()
    promoted, snap = apply_promotions(
        evaluate_promotions(store, sink.load_usage()), store, sink, snapshots,
        VERIFIER_VID)
    report.promoted = [it.shot.id for it in promoted]
    if snap is not None:
        report.snapshot = snap.vid

    # 3. 淘汰：复用表现不可靠的写墓碑
    store = sink.load_experience()
    report.retired = apply_retirements(
        evaluate_retirements(store, sink.load_usage()), sink)

    # 4. 路由：只读聚合统计
    store = sink.load_experience()
    report.routes = route_all(sink.load_usage(), store)
    return report


def _print_report(report: Report, root: Path) -> None:
    print(f"维护回路  {root}")
    print("=" * 70)
    print(f"哨兵告警 {len(report.alarms)} 条")
    for alarm in report.alarms:
        print("  " + str(alarm))
    print(f"晋升 {len(report.promoted)} 条：{report.promoted or '（无）'}"
          + (f" → 新快照 {report.snapshot}" if report.snapshot else ""))
    print(f"淘汰 {len(report.retired)} 条：{report.retired or '（无）'}")
    print("\n路由决策")
    for decision in report.routes:
        print(f"  {decision.tag:<12}{decision.stats.uses:>3} 次"
              f"  {decision.stats.pass_rate * 100:>4.0f}%  → {decision.path}"
              f"  （{decision.reason}）")
    print(f"\n本轮改动：{'有' if report.changed else '无（幂等，无需重复执行）'}")


def build_fixture(root: Path) -> tuple[RunStore, SnapshotStore]:
    """一份带问题的历史：可晋升的、该淘汰的、告警的。重建目录保证幂等。"""
    if root.exists():
        shutil.rmtree(root)
    sink = RunStore(root)
    snaps = SnapshotStore(root / "snapshots")

    decisive = Verdict(Status.PASS, Kind.DECISIVE, 1.0, "demo")
    scored = Verdict(Status.PASS, Kind.SCORED, 0.7, "demo")
    for shot_id, tag, verdict in (("A", "fam-a", decisive), ("B", "fam-b", decisive),
                                  ("C", "fam-c", scored), ("D", "fam-d", scored)):
        sink.append_experience(ExperienceItem(
            Shot(shot_id, tag, set(), f"{tag} 经验"), f"T-{shot_id}", "v1", verdict))

    # A 稳定：3 过；B 不可靠：1 过 2 败；C 可晋升：2 过；D 样本不足：1 过
    for _ in range(3):
        sink.append_usage(["A"], "T-A", "pass", "稳定复用")
    sink.append_usage(["B"], "T-B", "pass", "首次")
    for _ in range(2):
        sink.append_usage(["B"], "T-B", "fail", "答错")
    for _ in range(2):
        sink.append_usage(["C"], "T-C", "pass", "复现一致")
    sink.append_usage(["D"], "T-D", "pass", "仅一次")

    snaps.commit(VERIFIER_VID, ["A", "B"], ttl_days=0, source="demo：故意过期")
    return sink, snaps


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="在 runs/maintenance-eval 造一份数据")
    ap.add_argument("--dir", default="", help="对已有目录做维护")
    args = ap.parse_args()

    if args.dir:
        root = Path(args.dir)
        sink, snaps = RunStore(root), SnapshotStore(root / "snapshots")
    elif args.demo:
        root = RUNS_DIR / "maintenance-eval"
        sink, snaps = build_fixture(root)
    else:
        ap.print_help()
        raise SystemExit(1)

    _print_report(run_maintenance(sink, snaps), sink.root)


if __name__ == "__main__":
    main()