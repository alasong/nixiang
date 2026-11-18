"""沉淀物落盘。对应契约 v2 的 §2（权限矩阵）与 §3（可重放）。

设计约束
--------
1. **append-only**：只用 "a" 模式，永不改写已有行。契约 §2 明确内环可写失败
   日志，但不得修改快照与历史轨迹。
2. **双版本绑定**：每条记录都带 snapshot_vid / verifier_vid，否则验证器升级后
   无法判断该记录是按哪套标准判的（契约 §3.2）。
3. **可重放**：Step.output 是结构化结果，能被任意版本的验证器重新判定。
   这条不成立，"能力提升"就没有载体 —— 见 REAL_DATA_REPORT 第九节的推演。

三类沉淀物
----------
traces.jsonl      执行轨迹（外环探索 + 内环收敛都写这里）
negative.jsonl    负证据（失败路径，内环 append-only 写入）
experience.jsonl  经验条目（带置信分层，外环写、内环只读高置信层）
usage.jsonl       复用记录（哪条经验被用上、结果如何，晋升与淘汰的依据）
retired.jsonl     淘汰墓碑（只追加，加载经验库时过滤掉这些 id）
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .contracts import Judgment, Kind, Status, Step, Trace, Verdict
from .llm import Shot
from .store import ExperienceItem, ExperienceStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- 序列化

def encode_trace(t: Trace) -> dict:
    d = t.to_dict()
    d["logged_at"] = _now()
    return d


def decode_trace(d: dict) -> Trace:
    steps = [
        Step(
            node_id=s["node_id"],
            level=s["level"],
            action=s["action"],
            input_hash=s["input_hash"],
            output=s["output"],
            judgment=Judgment(s["judgment"]),
            cost=s["cost"],
            latency_ms=s["latency_ms"],
            nondeterministic=s.get("nondeterministic", False),
        )
        for s in d.get("steps", [])
    ]
    return Trace(
        task_id=d["task_id"],
        task_text=d.get("task_text", ""),
        facts=d.get("facts") or {},
        steps=steps,
        snapshot_vid=d.get("snapshot_vid", "v0"),
        verifier_vid=d.get("verifier_vid", "vf0"),
        outcome=Status(d.get("outcome", "unknown")),
        attempts=d.get("attempts", 0),
    )


def encode_experience(it: ExperienceItem) -> dict:
    return {
        "shot": {
            "id": it.shot.id,
            "scene_tag": it.shot.scene_tag,
            "covers": sorted(it.shot.covers),
            "text": it.shot.text,
        },
        "trace_id": it.trace_id,
        "snapshot_vid": it.snapshot_vid,
        "verdict": it.verdict.to_dict(),
        "logged_at": it.logged_at or _now(),
    }


def decode_experience(d: dict) -> ExperienceItem:
    s = d["shot"]
    v = d["verdict"]
    return ExperienceItem(
        shot=Shot(s["id"], s["scene_tag"], set(s["covers"]), s["text"]),
        trace_id=d["trace_id"],
        snapshot_vid=d["snapshot_vid"],
        verdict=Verdict(Status(v["status"]), Kind(v["kind"]),
                        float(v["confidence"]), v.get("detail", "")),
        logged_at=d.get("logged_at", ""),
    )


# ---------------------------------------------------------------- 存储

class RunStore:
    """append-only 的落盘层。进程重启后可从文件完整恢复。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.traces_file = self.root / "traces.jsonl"
        self.negative_file = self.root / "negative.jsonl"
        self.experience_file = self.root / "experience.jsonl"
        self.usage_file = self.root / "usage.jsonl"
        self.retire_file = self.root / "retired.jsonl"
        self.labels_file = self.root / "labels.jsonl"
        self.probes_file = self.root / "probes.jsonl"
        # 空文件也创建：下游消费者不必处理「文件不存在」这条路径
        for p in (self.traces_file, self.negative_file, self.experience_file,
                  self.usage_file, self.retire_file, self.labels_file,
                  self.probes_file):
            p.touch()

    # -- 写

    def append_trace(self, t: Trace) -> None:
        with self.traces_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(encode_trace(t), ensure_ascii=False) + "\n")

    def append_negative(self, t: Trace, reason: str) -> None:
        """负证据。契约 §2：内环可写，append-only，不改快照。"""
        rec = {
            "task_id": t.task_id,
            "snapshot_vid": t.snapshot_vid,
            "verifier_vid": t.verifier_vid,
            "outcome": t.outcome.value,
            "reason": reason,
            "node_ids": [s.node_id for s in t.steps],
            "input_hashes": sorted({s.input_hash for s in t.steps}),
            "cost": t.total_cost,
            "logged_at": _now(),
        }
        with self.negative_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def append_experience(self, it: ExperienceItem) -> None:
        with self.experience_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(encode_experience(it), ensure_ascii=False) + "\n")

    def append_retire(self, shot_ids: list[str], reason: str) -> None:
        """淘汰墓碑：只追加一条记录，不改历史经验行（append-only 不变）。"""
        rec = {"shot_ids": sorted(shot_ids), "reason": reason, "logged_at": _now()}
        with self.retire_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def append_label(self, task_id: str, labels: list[str] | set[str],
                     source: str = "verifier", confidence: float = 1.0) -> None:
        """验收结果落盘：这条任务应该被判为违反哪些规则。

        写入者是真值来源（验证器判定、人工复核、下游反馈）；挖掘器只读。
        append-only：同一任务的新结论追加一行，读取时取最新一条。
        """
        rec = {
            "task_id": task_id,
            "labels": sorted(labels),
            "source": source,
            "confidence": confidence,
            "logged_at": _now(),
        }
        with self.labels_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def append_probe(self, result) -> None:
        """哨兵探针结果：两条路径的答案是否一致。只追加，供差异率报警。"""
        rec = result.to_dict()
        rec["logged_at"] = _now()
        with self.probes_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def append_usage(self, shot_ids: list[str], task_id: str, outcome: str,
                     note: str = "") -> None:
        """记一次复用：哪几条经验被用上、结果如何。

        契约里没有这张表，但晋升与淘汰都需要它 —— 没有使用记录，经验库里
        的条目永远是「写进去时什么样，以后还是什么样」。
        """
        rec = {
            "shot_ids": sorted(shot_ids),
            "task_id": task_id,
            "outcome": outcome,
            "note": note,
            "logged_at": _now(),
        }
        with self.usage_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # -- 读

    def _read(self, p: Path) -> list[dict]:
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    def load_traces(self) -> list[Trace]:
        return [decode_trace(d) for d in self._read(self.traces_file)]

    def load_negative(self) -> list[dict]:
        return self._read(self.negative_file)

    def load_usage(self) -> list[dict]:
        return self._read(self.usage_file)

    def load_probes(self) -> list[dict]:
        return self._read(self.probes_file)

    def load_labels(self) -> dict[str, set[str]]:
        """当前标签视图：同一任务取最后一条（append-only，不删历史行）。"""
        view: dict[str, set[str]] = {}
        for rec in self._read(self.labels_file):
            view[rec["task_id"]] = set(rec.get("labels", []))
        return view

    def load_retired(self) -> set[str]:
        return {sid for rec in self._read(self.retire_file)
                for sid in rec.get("shot_ids", [])}

    def load_experience(self) -> ExperienceStore:
        """加载时给出「当前视图」。

        两条规则，都不改历史行：
          1. 墓碑里的 id 过滤掉（淘汰）
          2. 同一 id 只保留置信度最高的一条（晋升追加了 decisive 副本，
             原来的 scored 行还在 —— 不去重会在下次维护时重复晋升）
        """
        retired = self.load_retired()
        best: dict[str, ExperienceItem] = {}
        for d in self._read(self.experience_file):
            item = decode_experience(d)
            if item.shot.id in retired:
                continue
            current = best.get(item.shot.id)
            if current is None or _confidence_rank(item) > _confidence_rank(current):
                best[item.shot.id] = item
        store = ExperienceStore()
        for item in best.values():
            store.add(item)
        return store

    # -- 观测

    def counts(self) -> dict:
        return {
            "traces": len(self._read(self.traces_file)),
            "negative": len(self._read(self.negative_file)),
            "experience": len(self._read(self.experience_file)),
            "usage": len(self._read(self.usage_file)),
            "retired": len(self._read(self.retire_file)),
            "labels": len(self._read(self.labels_file)),
            "probes": len(self._read(self.probes_file)),
        }


def _confidence_rank(it: ExperienceItem) -> tuple[int, int]:
    """经验条目的置信排名：decisive+pass > 其它 pass > 其余。"""
    decisive = 1 if it.verdict.kind == Kind.DECISIVE else 0
    passed = 1 if it.verdict.status == Status.PASS else 0
    return (decisive, passed)


def replay_verdict(trace: Trace, verifier) -> list[Verdict]:
    """用当前版本的验证器重判一条历史轨迹。

    契约 §3.2 要求"result 可序列化且能被任意版本验证器重判"。这里演示的正是
    这条路径：不重跑任务，只读 Step.output 再判一次。验证器升级因此不必重建历史。
    """
    out = []
    for s in trace.steps:
        if s.action == "rule_hit":
            out.append(verifier.check(trace.task_id, set(s.output)))
        else:
            out.append(verifier.check(trace.task_id, set(s.output)))
    return out
