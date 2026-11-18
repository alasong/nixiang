"""任务域：差旅报销合规判定。

选择这个域的理由：
1. 规则可完全形式化 -> 验证器是纯判定型，无噪声
2. 目标明确（输出违规规则集合）-> 可自动比对
3. 有真实的判断难度（7 条规则，需要逐一核对）

数据结构驱动 ground truth，自然语言渲染交给"模型"阅读。
这样模型看不到结构化字段，必须自己推。
"""
from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass

RULES = {
    "R1": "单笔报销金额超过 5000 元且无总监审批",
    "R2": "住宿费超过标准（一线城市 600/晚，其他 400/晚）",
    "R3": "交通工具非经济舱或二等座",
    "R4": "餐补超过 100 元/天",
    "R5": "发票日期不在行程日期范围内",
    "R6": "同一日期存在重复报销",
    "R7": "跨月报销未附说明",
}

RULE_ORDER = ["R1", "R2", "R3", "R4", "R5", "R6", "R7"]


@dataclass
class Expense:
    id: str
    city_tier: int              # 1 = 一线, 2 = 其他
    city: str
    amount: float
    has_director_approval: bool
    hotel_per_night: float
    nights: int
    transport_class: str
    meal_days: int
    meal_total: float
    invoice_dates: list[str]    # YYYY-MM-DD
    trip_start: str
    trip_end: str
    submitted_month: str        # YYYY-MM
    has_note: bool
    same_day_dup: bool

    def scene_tag(self) -> str:
        """场景标签，供经验检索使用。

        注意：标签只依赖输入特征，不依赖答案。检索看不到 ground truth。
        """
        bucket = "high" if self.amount > 5000 else ("mid" if self.amount > 2000 else "low")
        return f"tier{self.city_tier}-{bucket}"

    def render(self) -> str:
        """渲染成自然语言，模拟真实报销申请文本。"""
        approval = "已附总监审批" if self.has_director_approval else "未经总监审批"
        note = "附有跨月说明" if self.has_note else "无附加说明"
        dup = "系统提示同日存在另一笔报销" if self.same_day_dup else "系统未检测到同日重复"
        dates = "、".join(self.invoice_dates)
        return (
            f"报销申请单 {self.id}。出差地{self.city}（{'一线城市' if self.city_tier == 1 else '非一线城市'}），"
            f"行程 {self.trip_start} 至 {self.trip_end}，共 {self.nights} 晚。"
            f"交通为{self.transport_class}。住宿 {self.hotel_per_night:.0f} 元/晚 × {self.nights} 晚。"
            f"餐补按 {self.meal_days} 天计，合计 {self.meal_total:.0f} 元。"
            f"发票日期：{dates}。报销总额 {self.amount:.0f} 元，{approval}，"
            f"于 {self.submitted_month} 提交，{note}。{dup}。"
        )


def task_facts(e: Expense) -> dict:
    """任务的结构化输入（不含 id）。轨迹与挖掘共用这一份字段定义。"""
    return {k: v for k, v in e.__dict__.items() if k != "id"}


def ground_truth(e: Expense) -> set[str]:
    """规则引擎：唯一真值来源。验证器直接复用它。"""
    hit = set()
    if e.amount > 5000 and not e.has_director_approval:
        hit.add("R1")
    cap = 600 if e.city_tier == 1 else 400
    if e.hotel_per_night > cap:
        hit.add("R2")
    if e.transport_class not in ("经济舱", "二等座"):
        hit.add("R3")
    if e.meal_total > 100 * e.meal_days:
        hit.add("R4")
    if any(d < e.trip_start or d > e.trip_end for d in e.invoice_dates):
        hit.add("R5")
    if e.same_day_dup:
        hit.add("R6")
    if e.submitted_month != e.trip_start[:7] and not e.has_note:
        hit.add("R7")
    return hit


CITIES = [("北京", 1), ("上海", 1), ("深圳", 1), ("杭州", 2), ("成都", 2), ("西安", 2)]
TRANSPORTS = ["经济舱", "二等座", "商务舱", "一等座"]


def build_tasks(n: int = 30, seed: int = 42, dup_ratio: float = 0.4) -> list[Expense]:
    """生成任务集。

    刻意混入约 dup_ratio 比例的重复任务（字段完全相同，仅 id 不同），
    用于测量「规则命中」路径的杠杆。真实业务里重复申请确实存在。
    """
    rng = random.Random(seed)
    n_unique = int(n * (1 - dup_ratio))
    unique: list[Expense] = []
    for i in range(n_unique):
        city, tier = rng.choice(CITIES)
        nights = rng.randint(1, 5)
        cap = 600 if tier == 1 else 400
        hotel = rng.choice([cap * 0.7, cap * 0.9, cap, cap * 1.3])
        transport = rng.choices(TRANSPORTS, weights=[5, 5, 1, 1])[0]
        meal_days = nights + 1
        meal_total = rng.choice([meal_days * 80, meal_days * 100, meal_days * 130])
        start_day = rng.randint(1, 20)
        month = f"2026-{rng.randint(1, 6):02d}"
        start = f"{month}-{start_day:02d}"
        end_day = start_day + nights
        end = f"{month}-{min(end_day, 28):02d}"
        invoice_dates = [start, end] if rng.random() > 0.15 else [start, f"2026-{(int(month[-2:]) % 6) + 1:02d}-05"]
        amount = round(hotel * nights + meal_total + rng.choice([800, 1500, 2600, 4200]), -2)
        unique.append(Expense(
            id=f"EXP-{i:03d}",
            city_tier=tier,
            city=city,
            amount=amount,
            has_director_approval=(amount <= 5000) or rng.random() > 0.5,
            hotel_per_night=hotel,
            nights=nights,
            transport_class=transport,
            meal_days=meal_days,
            meal_total=meal_total,
            invoice_dates=invoice_dates,
            trip_start=start,
            trip_end=end,
            submitted_month=month if rng.random() > 0.2 else "2026-07",
            has_note=rng.random() > 0.5,
            same_day_dup=rng.random() < 0.15,
        ))

    dups: list[Expense] = []
    for j in range(n - n_unique):
        src = unique[j % len(unique)]
        dup = Expense(**{**asdict(src), "invoice_dates": list(src.invoice_dates)})
        dup.id = f"EXP-{n_unique + j:03d}"
        dups.append(dup)

    tasks = unique + dups
    rng.shuffle(tasks)
    return tasks


def structural_key(e: Expense) -> str:
    """结构指纹：所有影响判定的字段。不含 id。

    指纹相同即判定结论必然相同，这是「规则命中」路径成立的前提。
    """
    d = asdict(e)
    d.pop("id")
    return json.dumps(d, ensure_ascii=False, sort_keys=True)


if __name__ == "__main__":
    ts = build_tasks()
    for t in ts[:3]:
        print(t.render())
        print("  ->", sorted(ground_truth(t)))
        print()
