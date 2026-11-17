"""判定型验证器。

契约 3.2：三态（pass/fail/unknown）+ kind + confidence + 双版本绑定。
本实现是纯判定型，不调外部服务，因此不会出现 unknown 以外的失败态。
"""
from __future__ import annotations

from .contracts import Kind, Status, Verdict


class SetMatchVerifier:
    """集合完全相等才算通过。

    decisive 型：判断来自确定性规则引擎，无噪声。
    """
    vid = "vf-decisive-1"

    def __init__(self, truth: dict[str, set[str]]) -> None:
        self._truth = truth

    def check(self, task_id: str, predicted: set[str]) -> Verdict:
        if task_id not in self._truth:
            return Verdict(Status.UNKNOWN, Kind.DECISIVE, 0.0, "no_ground_truth")

        expected = self._truth[task_id]
        if predicted == expected:
            return Verdict(Status.PASS, Kind.DECISIVE, 1.0, "exact_match")

        missing = expected - predicted
        extra = predicted - expected
        detail = f"missing={sorted(missing)} extra={sorted(extra)}"
        return Verdict(Status.FAIL, Kind.DECISIVE, 1.0, detail)


class ConsistencyVerifier:
    """一致性验证器：模型两次输出是否相同。

    仅用于演示 unknown 态的存在意义，MVP 主流程不使用。
    它判不了对错，只能判"稳不稳定"，所以给 unknown 而不是 pass/fail。
    """
    vid = "vf-consistency-1"

    def check(self, a: set[str], b: set[str]) -> Verdict:
        if a == b:
            return Verdict(Status.PASS, Kind.SCORED, 0.6, "stable")
        return Verdict(Status.UNKNOWN, Kind.SCORED, 0.0, "unstable")
