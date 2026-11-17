"""契约数据结构，严格对齐《双环嵌套接口契约 v2》。

MVP 阶段不使用快照与路由，但字段保留，将来接入无需改结构。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    """验证器三态。契约 3.2：必须有 unknown，否则评分型会被逼着硬给结论。"""
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class Kind(str, Enum):
    """验证器类型。MVP 只实现 decisive。"""
    DECISIVE = "decisive"
    SCORED = "scored"


class Judgment(str, Enum):
    """离散判断结果。契约 4.3：必须是枚举，不能是自然语言。

    这是将来挖规则的原料、回放比对的基准、冲突检测的依据。
    """
    RULE_HIT = "rule_hit"          # 判定该规则被触发
    RULE_MISS = "rule_miss"        # 判定该规则未触发
    RULE_UNCERTAIN = "rule_uncertain"
    RETRY = "retry"
    GIVEUP = "giveup"
    OK = "ok"


@dataclass
class Verdict:
    status: Status
    kind: Kind
    confidence: float
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "kind": self.kind.value,
            "confidence": self.confidence,
            "detail": self.detail,
        }


@dataclass
class Step:
    """单步执行记录。契约 §1：level 标识所属层级。"""
    node_id: str
    level: int
    action: str
    input_hash: str
    output: Any
    judgment: Judgment
    cost: int                  # token 数
    latency_ms: int
    nondeterministic: bool = False   # 契约 §5：一旦发生重采样即置真

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "level": self.level,
            "action": self.action,
            "input_hash": self.input_hash,
            "output": self.output,
            "judgment": self.judgment.value,
            "cost": self.cost,
            "latency_ms": self.latency_ms,
            "nondeterministic": self.nondeterministic,
        }


@dataclass
class Trace:
    """一次任务的完整轨迹。契约 §3.2：必须绑定双版本号。"""
    task_id: str
    steps: list[Step] = field(default_factory=list)
    snapshot_vid: str = "v0"
    verifier_vid: str = "vf0"
    outcome: Status = Status.UNKNOWN
    attempts: int = 0

    @property
    def total_cost(self) -> int:
        return sum(s.cost for s in self.steps)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "snapshot_vid": self.snapshot_vid,
            "verifier_vid": self.verifier_vid,
            "outcome": self.outcome.value,
            "attempts": self.attempts,
            "total_cost": self.total_cost,
            "steps": [s.to_dict() for s in self.steps],
        }


def fingerprint(obj: Any) -> str:
    """输入指纹，用于回放匹配与去重。"""
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
