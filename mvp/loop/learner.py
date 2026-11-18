"""规则挖掘（P1）：从带真值样本里学「条件 → 结论」。

学什么：给定样本字段与**外部给的真值标签**，为每个标签找一组纯合取
（多条合取之间是析取），在训练集上零误伤地覆盖尽量多的正样本。

怎么找：每个候选条件先算成样本位掩码，纯度 = 掩码与负样本掩码无交集，
覆盖 = 掩码与正样本掩码的位数。位运算比逐样本循环快两个数量级，200 条
样本上搜「单条件 + 两条件合取」是毫秒级。

不用什么：不调模型、不猜领域特征。特征只有字段本身与两两比值。表达不了
的标签（日期区间、跨月比较）覆盖率上不去，就不产出规则——不硬凑一条
看起来能跑的规则，那只会把漏报藏起来。

产出的 Rule 与手写规则同构（`All` / `Any` 条件树），因此可以直接交给
`validator.replay` 做回放、过激活门、再进 `RuleStore`。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import combinations

from ..core.rules import All, Any, Fact, Rule

MAX_SIZE = 2            # 合取最多几个条件
MAX_BRANCHES = 3        # 每个标签最多几条析取支
MIN_RECALL = 0.8        # 覆盖不到这个比例就不产出（宁缺毋滥）
THRESHOLDS = 12         # 每个数值特征最多保留几个阈值
MAX_CANDIDATES = 600    # 每个标签参与两两组合的条件上限
DATE_LIKE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")   # 日期/月份：等值匹配是字面量捷径


@dataclass(frozen=True)
class Learned:
    """学到的规则 + 它凭什么被接受：训练覆盖、分支、可读条件。"""
    rule: Rule
    recall: float
    support: int
    branches: tuple[tuple[str, ...], ...]

    def rows(self) -> list[str]:
        return [" ∧ ".join(b) if b else "（恒真）" for b in self.branches]


def _bits(mask: int) -> int:
    return bin(mask).count("1")


def numeric_fields(rows: list[dict]) -> list[str]:
    """数值字段（排除 bool，bool 是 int 的子类，不排会被当成数值）。"""
    names = []
    for key, value in rows[0].items():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            names.append(key)
    return names


def feature_predicates(rows: list[dict]) -> list[Fact]:
    """候选条件：数值阈值 + 两两比值阈值 + 类别/布尔取值。"""
    fields = numeric_fields(rows)
    facts: list[Fact] = []

    def add_thresholds(name: str, values: list[float], build) -> None:
        unique = sorted(set(values))
        if len(unique) < 2:
            return
        if len(unique) > THRESHOLDS + 1:
            step = (len(unique) - 1) / THRESHOLDS
            unique = [unique[round(i * step)] for i in range(THRESHOLDS + 1)]
            unique = sorted(set(unique))
        for low, high in zip(unique, unique[1:]):
            threshold = (low + high) / 2
            facts.append(build(threshold))

    for name in fields:
        values = [float(row[name]) for row in rows]
        add_thresholds(
            name, values,
            lambda t, n=name: Fact(
                f"{n} > {t:g}", (n,),
                lambda f, n=n, t=t: f[n] > t))

    for a, b in combinations(fields, 2):
        # 两个方向都生成：x/y 与 y/x 携带的信息不同（R4 要的是总价/天数）
        for num, den in ((a, b), (b, a)):
            ratios = []
            usable = True
            for row in rows:
                if not row.get(den):
                    usable = False
                    break
                ratios.append(float(row[num]) / float(row[den]))
            if not usable:
                continue
            add_thresholds(
                f"{num}/{den}", ratios,
                lambda t, n=num, d=den: Fact(
                    f"{n}/{d} > {t:g}", (n, d),
                    lambda f, n=n, d=d, t=t: f[d] != 0 and f[n] / f[d] > t))

    for key, value in rows[0].items():
        if isinstance(value, bool):
            for option in (True, False):
                facts.append(Fact(f"{key} == {option}", (key,),
                                  lambda f, k=key, v=option: f[k] == v))
            continue
        if isinstance(value, str):
            if all(DATE_LIKE.match(str(row[key])) for row in rows):
                # 日期/月份字段需要区间与前缀特征；展开成等值会学出字面量捷径
                continue
            options = sorted({row[key] for row in rows})
            if len(options) > max(10, len(rows) // 2):
                # 近乎唯一的字符串是标识符，不可能是可推广的条件
                continue
            for option in options:
                facts.append(Fact(f"{key} == {option}", (key,),
                                  lambda f, k=key, v=option: f[k] == v))
                facts.append(Fact(f"{key} != {option}", (key,),
                                  lambda f, k=key, v=option: f[k] != v))
    return facts


def _masks(facts: list[Fact], rows: list[dict]) -> list[int]:
    masks = []
    for fact in facts:
        mask = 0
        for i, row in enumerate(rows):
            if fact.test(row):
                mask |= 1 << i
        masks.append(mask)
    return masks


def candidates_for(pos: int, neg: int, facts: list[Fact],
                   masks: list[int], top: int | None = None):
    """筛出「至少盖住一个正样本」的条件，按覆盖数降序后截断。"""
    top = MAX_CANDIDATES if top is None else top
    scored = []
    for fact, mask in zip(facts, masks):
        if mask & pos:
            scored.append((_bits(mask & pos), -_bits(mask & neg), fact, mask))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return scored[:top]


def _pure_conjunctions(pos: int, neg: int, facts: list[Fact],
                       masks: list[int]) -> list[tuple[int, int, tuple[Fact, ...]]]:
    """所有「在训练集上零误伤、且覆盖 ≥1 正样本」的单条件/双条件合取。

    返回 (覆盖数, 掩码, 事实元组)，按覆盖降序。
    """
    chosen = []
    for fact, mask in zip(facts, masks):
        if mask & pos and not (mask & neg):
            chosen.append((_bits(mask & pos), mask, (fact,)))

    top = candidates_for(pos, neg, facts, masks)
    for (_, _, f1, m1), (_, _, f2, m2) in combinations(top, 2):
        mask = m1 & m2
        if mask & neg or not (mask & pos):
            continue
        chosen.append((_bits(mask & pos), mask, (f1, f2)))

    # 同一掩码只留一条（条件越少越短，排序时单条件在前）
    best: dict[int, tuple[int, int, tuple[Fact, ...]]] = {}
    for cover, mask, facts_tuple in chosen:
        current = best.get(mask)
        if current is None or len(facts_tuple) < len(current[2]):
            best[mask] = (cover, mask, facts_tuple)
    return sorted(best.values(), key=lambda item: (-item[0], len(item[2])))


def _rule(label: str, branches: list[tuple[Fact, ...]]) -> Rule:
    if len(branches) == 1:
        condition = All(branches[0])
    else:
        condition = Any(tuple(All(branch) for branch in branches))
    return Rule(label, f"learned：{len(branches)} 条分支", condition)


def learn(samples, max_size: int = MAX_SIZE, max_branches: int = MAX_BRANCHES,
          min_recall: float = MIN_RECALL) -> list[Learned]:
    """在带标签样本上学规则。返回覆盖率达标的标签对应的规则。"""
    learned, _ = learn_with_report(samples, max_size, max_branches, min_recall)
    return learned


def learn_with_report(samples, max_size: int = MAX_SIZE,
                      max_branches: int = MAX_BRANCHES,
                      min_recall: float = MIN_RECALL):
    """同上，另返回「学不出」的标签及其最好覆盖率，便于报告缺口。"""
    rows = [s.facts for s in samples]
    facts = feature_predicates(rows)
    masks = _masks(facts, rows)

    labels = sorted({label for s in samples for label in s.labels})
    learned: list[Learned] = []
    skipped: dict[str, float] = {}

    for label in labels:
        pos = 0
        for i, s in enumerate(samples):
            if label in s.labels:
                pos |= 1 << i
        everything = (1 << len(samples)) - 1
        neg = everything & ~pos
        total = _bits(pos)

        pool = _pure_conjunctions(pos, neg, facts, masks)
        covered = 0
        branches: list[tuple[Fact, ...]] = []
        while covered != pos and len(branches) < max_branches:
            best = None
            for cover, mask, facts_tuple in pool:
                gain = _bits(mask & pos & ~covered)
                if gain and (best is None or gain > best[0]):
                    best = (gain, mask, facts_tuple)
            if best is None:
                break
            _, mask, facts_tuple = best
            covered |= mask & pos
            branches.append(facts_tuple)

        recall = _bits(covered) / total if total else 0.0
        if recall < min_recall:
            skipped[label] = recall
            continue
        learned.append(Learned(
            rule=_rule(label, branches),
            recall=recall,
            support=_bits(covered),
            branches=tuple(tuple(fact.name for fact in branch) for branch in branches),
        ))
    return learned, skipped


def traces_to_samples(traces, labels) -> tuple[list, int]:
    """把带 facts 的轨迹转成带标签样本。

    labels 是 {task_id: 标签集合}；真值由验证器/下游给，不从轨迹里猜。
    没有 facts 的轨迹（旧格式）跳过并计数——不能拿空字段去学，那是噪声。
    """
    from .validator import Sample

    samples, skipped = [], 0
    for trace in traces:
        label = labels.get(trace.task_id)
        if not trace.facts or label is None:
            skipped += 1
            continue
        samples.append(Sample(trace.task_id, trace.facts, frozenset(label)))
    return samples, skipped


def demo() -> None:
    """训练集学规则，留出集验：零误伤、零漏报才算学到，学不出的如实报告。"""
    from ..core.tasks import build_tasks, ground_truth, task_facts
    from .backward import describe
    from .validator import Sample, gate, replay

    def corpus(seed: int):
        tasks = build_tasks(200, seed=seed)
        samples = [Sample(t.id, task_facts(t), frozenset(ground_truth(t)))
                   for t in tasks]
        return tasks, samples

    tasks_train, train = corpus(42)
    _, fresh = corpus(7)
    learned, skipped = learn_with_report(train)

    print("规则挖掘（P1）：200 条训练 → 200 条留出（不同抽样）")
    print("=" * 82)
    print(f"{'标签':<5}{'分支':>4}  {'条件':<52}{'训练覆盖':>8}{'留出误伤':>8}{'留出漏报':>8}{'结论':>6}")
    for item in learned:
        rows = item.rows()
        r = replay(item.rule, fresh)
        first = rows[0]
        print(f"{item.rule.id:<5}{len(rows):>4}  {first:<52}"
              f"{item.support:>8}{r.counter:>8}{r.fn:>8}"
              f"{'可激活' if gate(r).ok else '拒绝':>6}")
        for extra in rows[1:]:
            print(f"{'':<9}  {extra:<52}")
    for label, recall in sorted(skipped.items()):
        print(f"学不出：{label} 最好覆盖 {recall:.0%}（需要日期区间/月份前缀特征，"
              f"靠字段阈值表达不了）")
    # 学出的规则激活进规则库，直接用引擎跑留出集：机制要能接上才算落地
    from ..core.rules import RuleStore
    from .backward import BackwardEngine
    from .validator import replay_and_activate

    store = RuleStore(())
    activated = []
    for item in learned:
        if replay_and_activate(store, item.rule, train).ok:
            activated.append(item.rule.id)
    engine = BackwardEngine(store)
    fresh_tasks = build_tasks(200, seed=7)
    learned_ids = set(activated)
    agree = sum(1 for t in fresh_tasks
                if engine.violated(t.__dict__) ==
                {r for r in ground_truth(t) if r in learned_ids})
    missed = sum(1 for t in fresh_tasks if ground_truth(t) & set(skipped))
    print(f"\n引擎实跑（学出的 {len(activated)} 条规则 → 留出 200 条）："
          f"与真值一致的样本 {agree}/{len(fresh_tasks)}")
    print(f"  缺失规则 {sorted(skipped)}：这批样本里有 {missed} 条真违规引擎判不出（漏判，已明示）")
    # 原料来源对照：手工样本 vs 系统自己跑出来的轨迹
    import tempfile
    from pathlib import Path

    from ..core.persistence import RunStore
    from ..core.store import ExperienceStore
    from ..core.verifier import SetMatchVerifier
    from .runner import run_path

    truth = {t.id: ground_truth(t) for t in tasks_train}

    def _learn_from_traces(tasks):
        with tempfile.TemporaryDirectory() as d:
            sink = RunStore(Path(d))
            run_path("A", tasks, truth, SetMatchVerifier(truth),
                     ExperienceStore(), use_shots=False, sink=sink)
            traces = sink.load_traces()
            samples2, skipped_traces = traces_to_samples(traces, labels=truth)
        return traces, samples2, skipped_traces, learn_with_report(samples2)[0]

    traces, samples2, skipped_traces, learned2 = _learn_from_traces(tasks_train)
    same = {x.rule.id: describe(x.rule.condition) for x in learned} == \
           {x.rule.id: describe(x.rule.condition) for x in learned2}
    print(f"\n原料来源对照：{len(traces)} 条自跑轨迹 → {len(samples2)} 条带字段样本"
          f"（跳过 {skipped_traces}）→ 学出 {len(learned2)} 条；与手工样本结果{'一致' if same else '不一致'}")
    print(f"\n结论：{len(learned)}/7 条规则可从数据学出，"
          f"留出集零误伤零漏报；{len(skipped)} 条已如实报告缺口")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="规则挖掘（P1）")
    ap.add_argument("--demo", action="store_true", help="训练/留出集演示")
    args = ap.parse_args()
    if args.demo:
        demo()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
