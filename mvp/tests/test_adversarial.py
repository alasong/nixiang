"""对抗样本：喂「看起来对但实际错」的答案，量验证器的识别率。

变体不靠猜：拿真值答案做三类近似扰动——漏一条、多一条、换一条。它们都错，
验证器必须全部拒绝。识别率下降说明验证器被放松了（比如改成「子集即可」），
这正是 §8 说的「评分器被利用」。

这套东西测的是验证器，不是模型：答案是造出来的，不依赖模型输出。
"""
from __future__ import annotations

import unittest

from mvp.core.contracts import Kind, Status, Verdict
from mvp.core.tasks import RULE_ORDER, build_tasks, ground_truth
from mvp.core.verifier import SetMatchVerifier
from mvp.loop.adversarial import audit, detection_rate, mutations


class SubsetOk:
    """宽松验证器：只要预测是真值的子集就放行——模拟验证器被放松。"""
    vid = "vf-lenient"

    def __init__(self, truth):
        self._truth = truth

    def check(self, task_id, predicted):
        expected = self._truth.get(task_id, set())
        if predicted <= expected:
            return Verdict(Status.PASS, Kind.DECISIVE, 1.0, "subset_ok")
        return Verdict(Status.FAIL, Kind.DECISIVE, 1.0, "extra")


class TestMutations(unittest.TestCase):
    def test_three_kinds(self) -> None:
        truth = {"R1", "R2"}
        variants = mutations(truth, set(RULE_ORDER))
        kinds = {v.kind for v in variants}
        self.assertEqual(kinds, {"drop", "add", "swap"})
        for v in variants:
            self.assertNotEqual(v.predicted, truth)
            self.assertTrue(v.predicted <= set(RULE_ORDER))

    def test_empty_truth_still_mutates(self) -> None:
        variants = mutations(set(), set(RULE_ORDER))
        self.assertTrue(variants)
        self.assertTrue(all(v.predicted for v in variants))


class TestDetection(unittest.TestCase):
    def setUp(self) -> None:
        self.tasks = build_tasks(30)
        self.truth = {t.id: ground_truth(t) for t in self.tasks}

    def test_exact_verifier_rejects_all(self) -> None:
        report = detection_rate(SetMatchVerifier(self.truth), self.tasks, self.truth)
        self.assertEqual(report["overall"], 1.0)
        for kind in ("drop", "add", "swap"):
            self.assertEqual(report["by_kind"][kind], 1.0)

    def test_lenient_verifier_leaks(self) -> None:
        report = detection_rate(SubsetOk(self.truth), self.tasks, self.truth)
        self.assertLess(report["overall"], 1.0)
        self.assertEqual(report["by_kind"]["drop"], 0.0)

    def test_audit_passes_exact_and_fails_lenient(self) -> None:
        ok, _ = audit(SetMatchVerifier(self.truth), self.tasks, self.truth)
        self.assertTrue(ok.ok, ok.reasons)
        bad, report = audit(SubsetOk(self.truth), self.tasks, self.truth)
        self.assertFalse(bad.ok)
        self.assertIn("drop", " ".join(bad.reasons))


if __name__ == "__main__":
    unittest.main()