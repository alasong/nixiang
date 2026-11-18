"""对抗样本：喂「看起来对但实际错」的答案，量验证器的识别率。

变体不靠猜：拿真值答案做三类近似扰动——漏一条（drop）、多一条（add）、
换一条（swap）。它们都错，验证器必须全部拒绝。识别率下降说明验证器被放松
（比如改成「子集即可」），这正是 §8 说的「评分器被利用」。

这套东西测的是**验证器**，不是模型：答案是造出来的，不依赖模型输出。所以它
可以在没有流量的情况下定期跑，识别率跌破阈值就该换验证器或降级为辅助。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..core.contracts import Status
from ..core.tasks import RULE_ORDER

MIN_DETECTION = 0.99     # 近似变体必须几乎全部被拒；阈值是拍的，按验证器类型标定


@dataclass(frozen=True)
class Mutation:
    """一条近似答案：它和真值的差在哪。"""
    kind: str                 # drop | add | swap
    predicted: frozenset[str]
    removed: str = ""
    added: str = ""

    def describe(self) -> str:
        if self.kind == "drop":
            return f"漏判 {self.removed}：{sorted(self.predicted)}"
        if self.kind == "add":
            return f"误报 {self.added}：{sorted(self.predicted)}"
        return f"{self.removed}→{self.added}：{sorted(self.predicted)}"


@dataclass(frozen=True)
class Audit:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    report: dict = field(default_factory=dict)


def mutations(truth: set[str], universe: set[str] | None = None) -> list[Mutation]:
    """由真值生成近似变体：每条真值规则漏一次、每条非真值规则多一次、换一次。"""
    truth_set = set(truth)
    universe = set(RULE_ORDER if universe is None else universe)
    out: list[Mutation] = []
    for rule in sorted(truth_set):
        out.append(Mutation("drop", frozenset(truth_set - {rule}), removed=rule))
    for rule in sorted(universe - truth_set):
        out.append(Mutation("add", frozenset(truth_set | {rule}), added=rule))
    for rule in sorted(truth_set):
        for other in sorted(universe - truth_set)[:3]:
            out.append(Mutation("swap", frozenset((truth_set - {rule}) | {other}),
                                removed=rule, added=other))
    return out


def detection_rate(verifier, tasks, truth, universe: set[str] | None = None) -> dict:
    """识别率 = 被拒绝的近似答案 / 全部近似答案。"""
    stats = {kind: [0, 0] for kind in ("drop", "add", "swap")}
    examples: list[str] = []
    for task in tasks:
        expected = set(truth[task.id])
        for variant in mutations(expected, universe):
            verdict = verifier.check(task.id, set(variant.predicted))
            rejected = verdict.status != Status.PASS
            stats[variant.kind][0] += 1 if rejected else 0
            stats[variant.kind][1] += 1
            if not rejected and len(examples) < 3:
                examples.append(f"{task.id} {variant.describe()}")
    by_kind = {kind: (hit / total if total else 0.0)
               for kind, (hit, total) in stats.items()}
    total = sum(t for _, t in stats.values())
    return {
        "by_kind": by_kind,
        "overall": sum(h for h, _ in stats.values()) / total if total else 0.0,
        "total": total,
        "leaks": examples,
    }


def audit(verifier, tasks, truth, min_detection: float = MIN_DETECTION) -> tuple[Audit, dict]:
    """对抗审计：识别率低于阈值就判红，给出漏在哪一类。"""
    report = detection_rate(verifier, tasks, truth)
    reasons: list[str] = []
    if report["overall"] < min_detection:
        reasons.append(f"总体识别率 {report['overall']:.0%} < {min_detection:.0%}")
    for kind, rate in report["by_kind"].items():
        if rate < min_detection:
            reasons.append(f"{kind} 识别率 {rate:.0%} < {min_detection:.0%}")
    if report["leaks"]:
        reasons.append("漏例：" + "；".join(report["leaks"]))
    return Audit(not reasons, reasons, report), report


def demo() -> None:
    from ..core.tasks import build_tasks, ground_truth
    from ..core.contracts import Kind, Status, Verdict
    from ..core.verifier import SetMatchVerifier

    class SubsetOk:
        """宽松验证器：预测是真值的子集就放行（模拟验证器被放松）。"""
        vid = "vf-lenient"

        def __init__(self, truth):
            self._truth = truth

        def check(self, task_id, predicted):
            expected = self._truth.get(task_id, set())
            if predicted <= expected:
                return Verdict(Status.PASS, Kind.DECISIVE, 1.0, "subset_ok")
            return Verdict(Status.FAIL, Kind.DECISIVE, 1.0, "extra")

    tasks = build_tasks(50)
    truth = {t.id: ground_truth(t) for t in tasks}

    print("对抗审计（近似答案：漏一条 / 多一条 / 换一条）")
    print("=" * 66)
    for name, verifier in (("精确验证器", SetMatchVerifier(truth)),
                           ("宽松验证器（子集即可）", SubsetOk(truth))):
        decision, report = audit(verifier, tasks, truth)
        kinds = "  ".join(f"{k} {v:.0%}" for k, v in report["by_kind"].items())
        print(f"{name:<18} 样本 {report['total']:>4}  总体识别率 "
              f"{report['overall'] * 100:>5.1f}%  [{kinds}]  "
              f"{'通过' if decision.ok else '判红'}")
        if not decision.ok:
            print(f"  {'；'.join(decision.reasons)}")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="对抗样本审计")
    ap.add_argument("--demo", action="store_true", help="精确/宽松验证器对照")
    ap.add_argument("--audit", action="store_true",
                    help="对当前精确验证器跑审计；识别率不达标退出码 1")
    args = ap.parse_args()
    if args.demo:
        demo()
    elif args.audit:
        raise SystemExit(audit_cli())
    else:
        ap.print_help()


def audit_cli() -> int:
    from ..core.tasks import build_tasks, ground_truth
    from ..core.verifier import SetMatchVerifier

    tasks = build_tasks(200)
    truth = {t.id: ground_truth(t) for t in tasks}
    decision, report = audit(SetMatchVerifier(truth), tasks, truth)
    print(f"对抗审计（{report['total']} 条近似答案）：总体识别率 "
          f"{report['overall']:.1%}  {'通过' if decision.ok else '判红'}")
    for reason in decision.reasons:
        print("  " + reason)
    return 0 if decision.ok else 1


if __name__ == "__main__":
    main()