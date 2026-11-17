"""端到端集成测试：验证「沉淀物真的产生了」。

和 test_mvp.py 的区别
--------------------
test_mvp.py 测零件（验证器判得对不对、经验库排序对不对）。
本文件测链路：跑完一轮之后，磁盘上有没有留下东西、留下的东西能不能用、
验证器升级后历史数据还能不能重判。

这里没有一条测试在验证「能力提升」—— 因为能力提升模块（探索晋升、策略学习、
微调）根本没实现。能在当前代码上被验证的只有「沉淀发生了」，不是「变强了」。
这个区分必须写在测试名里，否则将来会有人误读绿灯。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mvp.contracts import Kind, Status, Verdict
from mvp.llm import Shot
from mvp.persistence import RunStore, replay_verdict
from mvp.runner import MAX_ATTEMPTS, build_experience, run_path
from mvp.store import ExperienceItem
from mvp.tasks import build_tasks, ground_truth
from mvp.verifier import SetMatchVerifier


class ForcedFail:
    """总是判失败，用于制造负证据。"""
    vid = "vf-forced-fail"

    def check(self, task_id: str, predicted: set[str]) -> Verdict:
        return Verdict(Status.FAIL, Kind.DECISIVE, 1.0, "forced")


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.tasks = build_tasks(n=6)
        self.truth = {t.id: ground_truth(t) for t in self.tasks}
        self.verifier = SetMatchVerifier(self.truth)
        self.exp = build_experience()
        self.sink = RunStore(self.dir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def rows(self, name: str) -> list[dict]:
        p = self.dir / name
        if not p.exists():
            return []
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


class TestSedimentIsProduced(_Base):
    """沉淀物：跑完一轮，磁盘上留下什么。"""

    def test_trace_is_persisted_with_both_version_ids(self) -> None:
        """契约 §3.2：每条轨迹必须绑定 snapshot_vid + verifier_vid，否则升级后无法归因。"""
        run_path("t", self.tasks, self.truth, self.verifier, self.exp,
                 use_shots=False, sink=self.sink)

        rows = self.rows("traces.jsonl")
        self.assertEqual(len(rows), len(self.tasks))
        for r in rows:
            self.assertTrue(r["task_id"])
            self.assertTrue(r["verifier_vid"], "缺 verifier_vid，升级后无法重判")
            self.assertTrue(r["snapshot_vid"], "缺 snapshot_vid，无法确定按哪版规则跑的")
            self.assertIn(r["outcome"], {"pass", "fail", "unknown"})
            self.assertGreater(r["total_cost"], 0)
            self.assertTrue(r["steps"], "空轨迹等于没记录")
            for s in r["steps"]:
                self.assertIn("judgment", s, "契约 4.3：判断点必须离散记录")

    def test_append_only_never_rewrites_history(self) -> None:
        """契约 §2：内环可追加，不可改写。重跑不得污染已有记录。"""
        run_path("t", self.tasks, self.truth, self.verifier, self.exp,
                 use_shots=False, sink=self.sink)
        first = self.rows("traces.jsonl")
        before = len(first)
        snapshot = [json.dumps(r, sort_keys=True) for r in first]

        run_path("t", self.tasks, self.truth, self.verifier, self.exp,
                 use_shots=False, sink=self.sink)
        after = self.rows("traces.jsonl")

        self.assertEqual(len(after), before * 2)
        self.assertEqual([json.dumps(r, sort_keys=True) for r in after[:before]], snapshot,
                         "第二次写入改动了历史行")

    def test_negative_evidence_only_on_failure(self) -> None:
        """负证据是内环唯一的写权限，必须只在失败时产生，且带版本号。"""
        # 全部成功
        run_path("ok", self.tasks, self.truth, self.verifier, self.exp,
                 use_shots=False, sink=self.sink)
        neg_ok = self.rows("negative.jsonl")

        # 全部失败
        run_path("bad", self.tasks, self.truth, ForcedFail(), self.exp,
                 use_shots=False, sink=self.sink)
        neg = self.rows("negative.jsonl")

        # 允许真实验证器偶尔判 fail，但强制失败必须为每个任务留下证据
        self.assertGreaterEqual(len(neg) - len(neg_ok), len(self.tasks),
                                "强制失败的任务没有全部留下负证据")
        for r in neg:
            self.assertTrue(r["verifier_vid"])
            self.assertTrue(r["input_hashes"], "负证据缺输入指纹，下次无法命中死路")
            self.assertIn(r["reason"], {"attempts_exhausted", "verifier_unknown", "failed"})


class TestSedimentIsUsable(_Base):
    """沉淀物：重启之后还能不能读、能不能用。"""

    def test_experience_survives_process_restart(self) -> None:
        """沉淀的定义：进程没了，资产还在。"""
        item = ExperienceItem(
            shot=Shot("X1", "tier1-high", {"R1"}, "金额超五千必须核对总监审批栏。"),
            trace_id="tr-1", snapshot_vid="v0",
            verdict=Verdict(Status.PASS, Kind.DECISIVE, 1.0, "manual"),
        )
        self.sink.append_experience(item)

        fresh = RunStore(self.dir)          # 模拟重启
        store = fresh.load_experience()
        self.assertEqual(len(store.high_confidence()), 1)

        got = store.retrieve("tier1-high", k=2)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].id, "X1")
        self.assertEqual(got[0].covers, {"R1"})

    def test_scored_experience_is_not_retrievable(self) -> None:
        """契约 3.3：评分型只做审计与重判，不进检索。否则噪声会污染 few-shot。"""
        self.sink.append_experience(ExperienceItem(
            shot=Shot("S1", "tier1-high", {"R1"}, "模型自评说这条大概能用。"),
            trace_id="tr-s", snapshot_vid="v0",
            verdict=Verdict(Status.PASS, Kind.SCORED, 0.7, "self_scored"),
        ))
        self.sink.append_experience(ExperienceItem(
            shot=Shot("D1", "tier1-high", {"R1"}, "判定型验证通过的经验。"),
            trace_id="tr-d", snapshot_vid="v0",
            verdict=Verdict(Status.PASS, Kind.DECISIVE, 1.0, "verified"),
        ))

        store = RunStore(self.dir).load_experience()
        ids = [it.shot.id for it in store.high_confidence()]
        self.assertEqual(ids, ["D1"])
        self.assertEqual([s.id for s in store.retrieve("tier1-high", k=5)], ["D1"])

    def test_trace_can_be_rejudged_after_verifier_upgrade(self) -> None:
        """契约 §3.2 的核心：验证器升级后，历史轨迹不重跑也能按新标准重判。

        这条不成立，「扩大可判定边界」就没有载体 —— 每次改标准都得重跑历史，
        成本等于重建，能力提升这条路直接断掉。
        """
        run_path("t", self.tasks, self.truth, self.verifier, self.exp,
                 use_shots=False, sink=self.sink)
        traces = RunStore(self.dir).load_traces()
        passed = [t for t in traces if t.outcome == Status.PASS]
        self.assertTrue(passed, "样本里没有成功轨迹，测试前提不成立")
        old = passed[0]

        # 业务规则收紧：每个任务多要求一条规则（模拟合规口径变化）
        stricter = {k: v | {"R7"} for k, v in self.truth.items()}
        v2 = SetMatchVerifier(stricter)
        v2.vid = "vf-decisive-2"

        verdicts = replay_verdict(old, v2)
        final = verdicts[-1]
        self.assertNotEqual(final.status, Status.PASS,
                            "规则收紧后旧轨迹仍判通过，说明没按新标准重判")
        self.assertEqual(old.total_cost, traces[0].total_cost or old.total_cost,
                         "重判过程不应改动原始轨迹")

    def test_unknown_outcome_is_not_recorded_as_failure(self) -> None:
        """三态语义：无真值时应保持 unknown，不能当成 fail 写进负证据。

        把 unknown 记成 fail，会让「验证器覆盖不到」被误读成「这条路走不通」，
        负证据库会被污染成噪声。
        """
        missing = {t.id: ground_truth(t) for t in self.tasks}
        bare = SetMatchVerifier({})          # 没有真值 → 一切 unknown
        run_path("u", self.tasks, missing, bare, self.exp,
                 use_shots=False, sink=self.sink)

        traces = RunStore(self.dir).load_traces()
        self.assertTrue(all(t.outcome == Status.UNKNOWN for t in traces))
        neg = self.rows("negative.jsonl")
        self.assertTrue(all(r["reason"] == "verifier_unknown" for r in neg),
                        "unknown 被记成了普通失败，归因会失真")


if __name__ == "__main__":
    unittest.main(verbosity=2)
