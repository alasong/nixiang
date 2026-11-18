"""下一条任务：把结构化任务交给逆向链判定，并给出可读报告。

用法：
    python3 -m mvp.task --json '{"amount": 8000, "has_director_approval": false, ...}'
    python3 -m mvp.task --file tasks.jsonl
    python3 -m mvp.task --json '...' --model        # 与真实 DeepSeek 对照（要 key）

范围：本仓库的领域是差旅报销，任务必须是**结构化字段**。自由文本任务没有对应
验证器与领域规则，硬接会把「判不了」伪装成「判完了」——这类任务暂不支持，会
在 `docs/落地状态_2026-11.md` 的「未做」里标着。

输入字段以规则库为准（`rule_fields` 并集），多余字段忽略，缺字段不算错——
逆向链会返回 unknown 并告诉你缺哪个，这正是三态语义的用处。
"""
from __future__ import annotations

import argparse
import json
import sys

from .core.rules import RuleStore, rule_fields
from .loop.backward import BackwardEngine

SYMBOLIC_COST = 10      # 一次规则查表的 token 当量
STATUS_TEXT = {"violated": "violated", "clean": "clean", "unknown": "unknown"}


def known_fields(store: RuleStore | None = None) -> tuple[str, ...]:
    """规则库用到的字段并集。输入字段以它为准，不额外发明字段。"""
    store = store or RuleStore()
    fields: list[str] = []
    for rule in store.all():
        for field in rule_fields(rule):
            if field not in fields:
                fields.append(field)
    return tuple(fields)


def load_task(obj) -> dict:
    """校验并裁出已知字段。未知字段忽略，一个已知字段都没有才报错。"""
    if not isinstance(obj, dict):
        raise ValueError("任务必须是 JSON 对象")
    known = set(known_fields()) | {"id"}
    facts = {key: value for key, value in obj.items() if key in known}
    if not set(facts) & set(known_fields()):
        raise ValueError(
            "没有任何规则用到的字段；可用字段："
            + ", ".join(known_fields()))
    return facts


def judge(facts: dict) -> dict:
    """逆向链判定。返回违规集合、判不了的规则与缺失字段、字段读取次数。"""
    engine = BackwardEngine()
    verdicts = engine.check_all(facts)
    violated = sorted(rid for rid, v in verdicts.items() if v.violated)
    unknown = {rid: list(v.missing) for rid, v in verdicts.items()
               if v.status == "unknown"}
    missing = sorted({field for v in verdicts.values() for field in v.missing})
    return {
        "violated": violated,
        "unknown": unknown,
        "missing": missing,
        "reads": sum(v.reads for v in verdicts.values()),
        "verdicts": {rid: {"status": v.status, "reads": v.reads,
                           "missing": list(v.missing)}
                     for rid, v in verdicts.items()},
    }


def render(facts: dict) -> str:
    """把字段渲染成一段申请文本，供模型路径阅读。缺的字段如实写「未提供」。"""
    def value(key: str, default: str = "未提供") -> str:
        raw = facts.get(key)
        return default if raw is None else str(raw)

    tier = facts.get("city_tier")
    if tier == 1:
        location = "一线城市"
    elif tier is not None:
        location = "非一线城市"
    else:
        location = "城市等级未提供"

    approval = facts.get("has_director_approval")
    approval_text = ("已附总监审批" if approval else "未经总监审批"
                     if approval is not None else "审批状态未提供")
    note = facts.get("has_note")
    note_text = ("附有跨月说明" if note else "无附加说明"
                 if note is not None else "跨月说明未提供")
    dup = facts.get("same_day_dup")
    dup_text = ("系统提示同日存在另一笔报销" if dup else "系统未检测到同日重复"
                if dup is not None else "同日重复状态未提供")
    dates = "、".join(facts.get("invoice_dates") or []) or "未提供"

    return (
        f"报销申请单 {value('id', 'TASK')}。{location}，"
        f"行程 {value('trip_start')} 至 {value('trip_end')}，共 {value('nights')} 晚。"
        f"交通为{value('transport_class')}。住宿 {value('hotel_per_night')} 元/晚。"
        f"餐补按 {value('meal_days')} 天计，合计 {value('meal_total')} 元。"
        f"发票日期：{dates}。报销总额 {value('amount')} 元，{approval_text}，"
        f"于 {value('submitted_month')} 提交，{note_text}。{dup_text}。")


def _print_report(task_id: str, facts: dict, result: dict) -> None:
    print(f"任务 {task_id}")
    print(f"  违规规则  {', '.join(result['violated']) or '无'}")
    if result["unknown"]:
        detail = "；".join(f"{rid}（缺 {', '.join(fields) or '未知字段'}）"
                          for rid, fields in sorted(result["unknown"].items()))
        print(f"  判不了    {detail}")
    else:
        print("  判不了    无")
    print(f"  字段读取  {result['reads']} 次   "
          f"查表成本 {SYMBOLIC_COST} token 当量（未调模型）")

    if not result["verdicts"]:
        return
    print()
    print(f"  {'规则':<5}{'结论':<10}{'读取':>4}  缺失字段")
    for rid, verdict in result["verdicts"].items():
        missing = ", ".join(verdict["missing"]) or "-"
        print(f"  {rid:<5}{STATUS_TEXT.get(verdict['status'], verdict['status']):<10}"
              f"{verdict['reads']:>4}  {missing}")


def _compare_with_model(facts: dict, result: dict, task_id: str) -> int:
    """用真实 DeepSeek 判同一条任务，和查表结论对照。失败返回非 0。"""
    from .core.llm_deepseek import DeepSeekError, DeepSeekLLM

    try:
        llm = DeepSeekLLM()
        answer = llm.extract(render(facts), [])
    except DeepSeekError as exc:
        print(f"任务 {task_id}：真实模型调用失败：{exc}")
        return 2
    model_rules = sorted(answer.rules)
    same = set(model_rules) == set(result["violated"])
    print(f"  模型      {', '.join(model_rules) or '无'}  "
          f"（{answer.total_tokens} tokens）  "
          f"{'与查表一致' if same else '与查表不一致'}")
    if not same:
        only_model = sorted(set(model_rules) - set(result["violated"]))
        only_rules = sorted(set(result["violated"]) - set(model_rules))
        print(f"  差异      仅模型：{only_model or '无'}；仅查表：{only_rules or '无'}")
    return 0


def _load_objects(args) -> list[dict]:
    if args.json is not None:
        return [json.loads(args.json)]
    if args.file is not None:
        return [json.loads(line) for line in
                open(args.file, encoding="utf-8").read().splitlines()
                if line.strip()]
    raise ValueError("需要 --json 或 --file")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m mvp.task",
        description="下一条任务：结构化输入 → 逆向链判定")
    parser.add_argument("--json", help="单条任务（JSON 对象）")
    parser.add_argument("--file", help="批量任务（jsonl，一行一条）")
    parser.add_argument("--model", action="store_true",
                        help="同时调用真实 DeepSeek 对照（需 DEEPSEEK_API_KEY）")
    args = parser.parse_args(argv)

    try:
        objects = _load_objects(args)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"输入错误：{exc}", file=sys.stderr)
        return 2

    exit_code = 0
    for index, obj in enumerate(objects, 1):
        try:
            facts = load_task(obj)
        except ValueError as exc:
            print(f"第 {index} 条任务输入错误：{exc}", file=sys.stderr)
            return 2
        task_id = str(facts.get("id") or f"TASK-{index}")
        result = judge(facts)
        _print_report(task_id, facts, result)
        if args.model:
            exit_code = max(exit_code, _compare_with_model(facts, result, task_id))
        print()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())