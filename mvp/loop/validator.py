"""回放验证器（RuleValidator）：候选规则能否从 candidate 进 active，由回放读数判。

对齐《双引擎逆向推理设计文档》§7 的硬条件与 §8 P2 的验收：

    candidate → active   support ≥ 5，counter = 0，回放不误伤其他样本
    P2                  回放通过率 > 80%，误伤率 < 5%

两件事分开记，不合并成一个分数：
  - **激活门**是硬条件（counter 必须为 0），由 `gate()` 判；
  - **通过率 / 误伤率**是验收读数，给报告用，边界由调用方传参决定。

为什么要有它：晋升（`promotion.py`）只看「用过几次、全过没有」，没有校验
内容本身；候选池里的东西也可能只是运气。回放是唯一不依赖作者的判据。

输入是**样本**：事实 + 外部给的真值标签。真值不能由候选规则自己产生，
否则就是自己出题自己判卷。

仍然缺（本轮不做）：从轨迹里挖「条件→结论」形态的候选；这里只提供判据，
候选来源仍是人写的规则或指纹短路。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..core.contracts import Judgment, Status, Step, Trace
from ..core.rules import MIN_SUPPORT, All, Fact, Rule, RuleStore
from .backward import UNKNOWN, VIOLATED, check

MAX_FALSE_ALARM = 0.05  # P2：误伤率 < 5%
MIN_ACCURACY = 0.80     # P2：回放通过率 > 80%


@dataclass(frozen=True)
class Sample:
    """一个带真值标签的样本。

    labels 是「这个样本应当被判为违反的规则 id」，由外部给（人工、下游系统、
    验证器）。回放只负责比对，不负责产生真值。
    """
    sample_id: str
    facts: dict
    labels: frozenset[str] = frozenset()

    def positive(self, target: str) -> bool:
        return target in self.labels


@dataclass(frozen=True)
class Report:
    """一次回放的混淆矩阵。support/counter 是 §7 的术语，tp/fp 是统计术语。"""
    name: str
    tp: int = 0             # 命中且真值有
    fp: int = 0             # 命中但真值为空（误伤）
    fn: int = 0             # 未命中但真值有（漏报）
    tn: int = 0             # 未命中且真值为空
    unknown: int = 0        # 三态求值判不了，不参与统计

    @property
    def support(self) -> int:
        return self.tp

    @property
    def counter(self) -> int:
        return self.fp

    @property
    def decided(self) -> int:
        return self.tp + self.fp + self.fn + self.tn

    @property
    def accuracy(self) -> float:
        return (self.tp + self.tn) / self.decided if self.decided else 0.0

    @property
    def false_alarm_rate(self) -> float:
        """误伤率 = 误伤 / 真值为空的样本。没有干净样本时记 0。"""
        negative = self.fp + self.tn
        return self.fp / negative if negative else 0.0

    @property
    def miss_rate(self) -> float:
        positive = self.tp + self.fn
        return self.fn / positive if positive else 0.0

    def row(self) -> str:
        return (f"{self.name:<8} 支持 {self.support:>3}  误伤 {self.counter:>2}  "
                f"漏报 {self.fn:>2}  未知 {self.unknown:>2}  "
                f"通过率 {self.accuracy * 100:>5.1f}%  "
                f"误伤率 {self.false_alarm_rate * 100:>4.1f}%")


@dataclass(frozen=True)
class Decision:
    """激活结论。reasons 为空才算通过；每条 reason 都能单独判对错。"""
    ok: bool
    reasons: list[str] = field(default_factory=list)
    report: Report | None = None


def replay(rule: Rule, samples: list[Sample]) -> Report:
    """把一条规则放到带真值的样本上重放。

    逆向链的三态求值决定「命中」：
      violated → 判为违反（正）
      clean    → 判为不违反（负）
      unknown  → 弃权，不计入混淆矩阵，也不当误伤
    """
    tp = fp = fn = tn = unknown = 0
    for s in samples:
        verdict = check(rule, s.facts)
        if verdict.status == UNKNOWN:
            unknown += 1
            continue
        predicted = verdict.status == VIOLATED
        truth = s.positive(rule.id)
        if predicted and truth:
            tp += 1
        elif predicted:
            fp += 1
        elif truth:
            fn += 1
        else:
            tn += 1
    return Report(rule.id, tp, fp, fn, tn, unknown)


def replay_all(rules, samples: list[Sample]) -> list[Report]:
    return [replay(rule, samples) for rule in rules]


def gate(report: Report,
         min_support: int = MIN_SUPPORT,
         max_false_alarm: float = MAX_FALSE_ALARM,
         min_accuracy: float = MIN_ACCURACY) -> Decision:
    """激活门。四条判据分别记，任一不过即拒绝。

    counter = 0 是 §7 的硬条件，比「误伤率 < 5%」更严：P2 允许有少量误伤，
    但一条有误伤的规则不允许被激活。
    """
    reasons: list[str] = []
    if report.support < min_support:
        reasons.append(f"支持样本 {report.support} < {min_support}")
    if report.counter > 0:
        reasons.append(f"误伤 {report.counter} 条（counter 必须为 0）")
    if report.accuracy < min_accuracy:
        reasons.append(f"回放通过率 {report.accuracy:.0%} < {min_accuracy:.0%}")
    if report.false_alarm_rate > max_false_alarm:
        reasons.append(f"误伤率 {report.false_alarm_rate:.1%} > {max_false_alarm:.1%}")
    return Decision(not reasons, reasons, report)


def replay_shortcut(digest: str, traces: list[Trace]) -> Report:
    """回放指纹短路候选：命中该指纹的轨迹是否必然失败。

    样本域只含命中该指纹的轨迹——短路只对它们作断言，没命中的轨迹属于
    「不在候选的适用域」，记未知，不算漏报也不算正确。

    support = 命中且失败的轨迹数；counter = 命中但成功的轨迹数。出现过一次
    成功，这条短路就会误伤，按 §7 不能激活。
    """
    tp = fp = unknown = 0
    for t in traces:
        hit = any(step.input_hash == digest for step in t.steps)
        if not hit:
            unknown += 1
            continue
        if t.outcome in (Status.FAIL, Status.UNKNOWN):
            tp += 1
        else:
            fp += 1
    return Report(digest, tp, fp, 0, 0, unknown)


def replay_and_activate(store: RuleStore, rule: Rule,
                        samples: list[Sample]) -> Decision:
    """候选规则走完整链路：登记 → 回放 → 过门 → 激活。

    过不了门就停在 candidate，不删也不改：下一轮数据补齐还能再验。
    """
    store.register(rule, source="replay")
    report = replay(rule, samples)
    decision = gate(report)
    if decision.ok:
        store.activate(rule.id, report.support, report.counter)
    return decision


def validate_candidates(sink) -> list[Decision]:
    """把挖掘出的候选过一遍激活门。候选由 miner 产出，判据在这里，两件事分开。"""
    from .miner import mine

    traces = sink.load_traces()
    return [gate(replay_shortcut(c.input_hash, traces)) for c in mine(sink)]


def demo() -> None:
    """回放读数：7 条手写规则、1 条坏规则、1 条指纹短路。"""
    from ..core.tasks import build_tasks, ground_truth

    tasks = build_tasks(200)
    samples = [Sample(t.id, t.__dict__, frozenset(ground_truth(t))) for t in tasks]

    print("回放验证（合成报销域，200 条带真值样本）")
    print("=" * 78)
    print(f"{'规则':<8}{'支持':>6}{'误伤':>6}{'漏报':>6}{'未知':>6}"
          f"{'通过率':>9}{'误伤率':>9}{'结论':>8}")
    for r in replay_all(RuleStore().all(), samples):
        d = gate(r)
        print(f"{r.name:<8}{r.support:>6}{r.counter:>6}{r.fn:>6}{r.unknown:>6}"
              f"{r.accuracy * 100:>8.1f}%{r.false_alarm_rate * 100:>8.1f}%"
              f"{'可激活' if d.ok else '拒绝':>8}")

    # 坏规则：漏掉「无总监审批」这一半，金额超 5000 就判违规
    broken = Rule("R1", "坏规则：漏掉审批条件",
                  Fact("金额>5000", ("amount",), lambda f: f["amount"] > 5000))
    rb = replay(broken, samples)
    db = gate(rb)
    print()
    print(f"坏规则（缺审批条件）：{rb.row()}")
    print(f"  结论 {'可激活' if db.ok else '拒绝'}：{'; '.join(db.reasons) or '无'}")

    traces = [
        Trace(task_id=f"t{i}", outcome=outcome,
              steps=[Step(node_id=f"t{i}#0", level=0, action="extract",
                          input_hash="H" if i < 2 else "X", output=[],
                          judgment=Judgment.OK, cost=1, latency_ms=1)])
        for i, outcome in enumerate((Status.FAIL, Status.FAIL, Status.PASS))
    ]
    rs = replay_shortcut("H", traces)
    ds = gate(rs)
    print()
    print(f"指纹短路 H：{rs.row()}")
    print(f"  结论 {'可激活' if ds.ok else '拒绝'}：{'; '.join(ds.reasons) or '无'}")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="候选规则回放验证")
    ap.add_argument("--demo", action="store_true", help="跑合成域演示")
    args = ap.parse_args()
    if args.demo:
        demo()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()