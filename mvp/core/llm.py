"""LLM 接口与仿真实现。

重要声明
--------
`SimulatedLLM` 是**仿真**，不是真实模型。它按预设准确率生成判断，
用于验证框架能否运转、指标能否被度量、结论能否被算出。

仿真结果**不能**作为 H3（复用能否降本）的结论。替换 `LLMClient` 的
实现为真实 API 调用后重跑，得到的才是真结论。

仿真为什么需要看到真值：为了把准确率精确控制在设定值。真实模型不看
答案，但输出分布等价。等价性只在分布层面成立，这是仿真的固有局限。
"""
from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass

from .tasks import RULE_ORDER


@dataclass
class Shot:
    """一条经验示例。covers 标明它能帮助理解哪些规则。"""
    id: str
    scene_tag: str
    covers: set[str]
    text: str


@dataclass
class LLMResult:
    rules: set[str]
    prompt_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.output_tokens


class LLMClient(ABC):
    """模型调用接口。换成真实 API 只需实现这一个方法。"""

    @abstractmethod
    def extract(self, text: str, shots: list[Shot]) -> LLMResult:
        """给定申请文本与经验示例，输出判断出的违规规则集合。"""


class SimulatedLLM(LLMClient):
    """按规则级准确率采样的仿真模型。

    参数含义：
        p_base  无经验时的单规则判断准确率
        p_shot  有对应经验时的单规则判断准确率
        base_prompt  基础 prompt token 数
        shot_cost    每条经验附加的 token 数
    """

    def __init__(
        self,
        truth: dict[str, set[str]],
        seed: int = 7,
        p_base: float = 0.88,
        p_shot: float = 0.965,
        base_prompt: int = 550,
        shot_cost: int = 180,
        out_per_rule: int = 18,
    ) -> None:
        self._truth = truth
        self._rng = random.Random(seed)
        self.p_base = p_base
        self.p_shot = p_shot
        self.base_prompt = base_prompt
        self.shot_cost = shot_cost
        self.out_per_rule = out_per_rule

    def extract(self, text: str, shots: list[Shot]) -> LLMResult:
        covered: set[str] = set()
        for s in shots:
            covered |= s.covers

        task_id = text.split("。")[0].replace("报销申请单 ", "").strip()
        truth = self._truth.get(task_id, set())

        decided: set[str] = set()
        for rule in RULE_ORDER:
            p = self.p_shot if rule in covered else self.p_base
            correct = self._rng.random() < p
            actually_hit = rule in truth
            if correct:
                if actually_hit:
                    decided.add(rule)
            else:
                if not actually_hit:
                    decided.add(rule)          # 误报
                # 漏报则不加

        prompt = self.base_prompt + self.shot_cost * len(shots)
        output = self.out_per_rule * len(RULE_ORDER) + 40
        return LLMResult(rules=decided, prompt_tokens=prompt, output_tokens=output)
