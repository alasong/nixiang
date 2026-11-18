"""任意任务入口（M1）：任务文本/任务文件 + 验收命令 → 逆向分解 → 执行 → 逐个验收 → 轨迹。

M0 只做确定性模式（内环），不调模型；M1 在它上面加"任务级逆向分解"：

  1. 任务文件里有 ``## 子目标`` 段时，按验收条件拆成子目标，逐个执行、逐个验收；
  2. 验收命令是 ``A && B && C`` 时，按合取拆成子目标，逐个跑；
  3. 都没有就退回 M0 的单动作 + 单验收。

验收永远是外部 shell 命令，实现与模型都不得自评（v0.1 原则 P1）。匹配不上
执行器就返回 unsupported，不把"判不了"伪装成"做完了"。

用法：
    python3 -m mvp.handle --task "在 /tmp/xm-hello.txt 写入 hello" \\
        --verify "grep -q hello /tmp/xm-hello.txt"
    python3 -m mvp.handle --task tasks/T2.md --verify "make docs-check" --explain

退出码：0 验收通过；1 执行或验收失败；2 没有执行器能处理（unsupported）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .candidates import CandidateError, DeepSeekCandidateProvider, load_jsonl
from .formalize import DeepSeekFormalizer, FileFormalizer, FormalizeError
from .judge import DeepSeekJudge, FileJudge, JudgeError
from .repair import DeepSeekRepairer, FileRepairer, RepairError
from .core.contracts import Judgment, Status, Step, Trace, fingerprint
from .core.llm_deepseek import DeepSeekError
from .core.paths import ROOT, RUNS_DIR
from .core.persistence import RunStore

VERIFIER_VID = "vf-shell-1"
TIMEOUT = 60.0
TAIL = 500


@dataclass(frozen=True)
class TaskSpec:
    """一条任务：做什么（text）+ 怎么算成了（verify）。"""
    text: str
    verify: str
    task_id: str = ""


@dataclass(frozen=True)
class SubGoal:
    """一个子目标：动作（可空=纯验收）+ 它自己的验收命令。"""
    label: str
    action: str
    verify: str


@dataclass(frozen=True)
class CachedPlan:
    """计划缓存：同一任务第二次跑时直接复用，不再重新分解。"""
    subgoals: list[SubGoal]
    source: str


@dataclass
class HandleResult:
    spec: TaskSpec
    trace: Trace
    status: str                 # passed | failed | unsupported
    executor: str
    verify_rc: int = -1
    verify_output: str = ""
    execution: dict = field(default_factory=dict)
    subgoals: list[SubGoal] = field(default_factory=list)
    subgoals_passed: int = 0
    plan_reused: bool = False
    model_calls: int = 0
    agents_used: int = 1
    stop_reason: str = ""
    wall_ms: int = 0
    audit: list[tuple[str, bool]] = field(default_factory=list)
    candidates_total: int = 0
    candidates_passed: int = 0
    selected: int = 0
    best_score: int = 0
    single_score: int = 0
    judge_vid: str = ""
    budget_ms: int = 0
    formalizer_vid: str = ""
    repaired: int = 0

    @property
    def passed(self) -> bool:
        return self.status == "passed"


# ---------------------------------------------------------------- 执行器

class Executor:
    """执行器接口：匹配不中就返回 None，绝不硬猜。"""

    name = "base"

    def match(self, text: str) -> dict | None:
        raise NotImplementedError

    def run(self, parsed: dict) -> dict:
        raise NotImplementedError


class FileWriteExecutor(Executor):
    """确定性文件写入/追加模式：不调模型，成本 0。

    支持：``在 <路径> 写入 <内容>``、``在 <路径> 追加 <内容>``、
    ``在 <路径> 末尾加一行 <内容>``、``write <内容> to <路径>``。
    """

    name = "write_file"
    _zh_line = re.compile(
        r"在\s+(?P<path>[^\s，,。]+)\s*末尾\s*(?:加|追加|添加)\s*(?:一行)?\s*(?P<content>.+)")
    _zh_append = re.compile(r"在\s+(?P<path>[^\s，,。]+)\s*追加\s*(?P<content>.+)")
    _zh_write = re.compile(r"在\s+(?P<path>[^\s，,。]+)\s*写入\s*(?P<content>.+)")
    _en_write = re.compile(r"write\s+(?P<content>.+?)\s+to\s+(?P<path>\S+)", re.I)

    def match(self, text: str) -> dict | None:
        for mode, pattern in (("append", self._zh_line),
                              ("append", self._zh_append),
                              ("write", self._zh_write),
                              ("write", self._en_write)):
            m = pattern.search(text.strip())
            if m:
                content = m.group("content").strip()
                content = content.strip("\"'“”「」")
                if content:
                    return {"mode": mode, "path": m.group("path").strip(),
                            "content": content}
        return None

    def run(self, parsed: dict) -> dict:
        path = Path(parsed["path"]).expanduser()
        if not path.suffix:
            alt = path.with_suffix(".md")      # README → README.md（两边都有时优先 .md）
            if alt.exists():
                path = alt
        path.parent.mkdir(parents=True, exist_ok=True)
        data = parsed["content"]
        if not data.endswith("\n"):
            data += "\n"
        with path.open("a" if parsed["mode"] == "append" else "w",
                       encoding="utf-8") as f:
            f.write(data)
        return {"output": {"path": str(path), "mode": parsed["mode"],
                           "bytes": len(data.encode("utf-8"))}}


# ---------------------------------------------------------------- 分解

_SECTION = re.compile(r"^#{1,6}\s*(子目标|subgoals?)\s*$", re.I)
_NUM = re.compile(r"^\s*\d+[.、)]\s*(.+?)\s*$")
_VERIFY = re.compile(r"^\s*验收\s*[:：]\s*(.+?)\s*$")


def parse_task_file(content: str) -> list[SubGoal]:
    """从任务文件的 ``## 子目标`` 段解析子目标；没有该段返回空列表。

    每个子目标必须带 ``验收：<命令>``，没有验收条目的条目直接丢弃——
    不能验收的子目标不许执行。
    """
    in_section = False
    pending: SubGoal | None = None
    out: list[SubGoal] = []
    for line in content.splitlines():
        stripped = line.strip()
        if _SECTION.match(stripped):
            in_section = True
            continue
        if not in_section:
            continue
        if stripped.startswith("#"):
            break
        m = _NUM.match(line)
        if m:
            if pending is not None and pending.verify:
                out.append(pending)
            pending = SubGoal(label=m.group(1), action=m.group(1), verify="")
            continue
        v = _VERIFY.match(line)
        if v and pending is not None:
            out.append(SubGoal(label=pending.label, action=pending.action,
                               verify=v.group(1)))
            pending = None
    if pending is not None and pending.verify:
        out.append(pending)
    return [s for s in out if s.verify]


def split_conjunction(command: str) -> list[str]:
    """按引号外的 ``&&`` 拆分验收命令；引号里的 && 不拆。"""
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(command):
        ch = command[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if command.startswith("&&", i):
            part = "".join(buf).strip()
            if part:
                parts.append(part)
            buf = []
            i += 2
            continue
        buf.append(ch)
        i += 1
    part = "".join(buf).strip()
    if part:
        parts.append(part)
    return parts


def load_task_text(text: str) -> str:
    """任务文本是文件路径时读出内容；否则原样返回。"""
    if "\n" in text:
        return text
    path = Path(text).expanduser()
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return text


def decompose(content: str, verify: str) -> tuple[list[SubGoal], str]:
    """从任务文件或验收命令反推子目标；返回 (子目标, 来源)。"""
    subs = parse_task_file(content)
    if subs:
        return subs, "file"
    parts = split_conjunction(verify)
    if len(parts) > 1:
        return [SubGoal(f"验收 {i + 1}", "", part)
                for i, part in enumerate(parts)], "verify"
    return [], ""


# ---------------------------------------------------------------- 计划缓存

def _plan_key(spec: TaskSpec) -> str:
    return fingerprint({"text": spec.text, "verify": spec.verify})


def _save_plan(store: RunStore, spec: TaskSpec, subgoals: list[SubGoal],
               source: str) -> None:
    rec = {
        "key": _plan_key(spec),
        "task_text": spec.text,
        "verify": spec.verify,
        "source": source,
        "subgoals": [{"label": s.label, "action": s.action, "verify": s.verify}
                     for s in subgoals],
        "logged_at": datetime.now().isoformat(timespec="seconds"),
    }
    with (store.root / "plans.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _load_plan(store: RunStore, spec: TaskSpec) -> CachedPlan | None:
    path = store.root / "plans.jsonl"
    if not path.exists():
        return None
    key = _plan_key(spec)
    found: dict | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("key") == key:
            found = rec
    if found is None:
        return None
    return CachedPlan(
        [SubGoal(s["label"], s["action"], s["verify"])
         for s in found.get("subgoals", [])],
        found.get("source", ""))


# ---------------------------------------------------------------- 过程审计

def _process_audit(result: HandleResult, trace: Trace, spec: TaskSpec,
                   subs: list[SubGoal], agents: int) -> list[tuple[str, bool]]:
    """过程层 6 项：入口、验收、分解、并发、归因、留痕。"""
    steps = trace.steps
    verify_steps = [s for s in steps if s.action in ("verify", "subgoal")]
    attribution = all(
        (s.output.get("rc") is not None) or s.output.get("error")
        for s in steps if s.judgment in (Judgment.RETRY, Judgment.GIVEUP))
    expected = min(agents, max(1, len(subs)))
    concurrency = result.agents_used == expected or (
        result.stop_reason in ("write_conflict", "single")
        and result.agents_used == 1)
    return [
        ("入口", result.status != "unsupported" and result.executor != "none"),
        ("验收", bool(spec.verify) and bool(verify_steps)),
        ("分解", result.status != "unsupported"
                and (bool(subs) or result.executor != "none")),
        ("并发", result.agents_used >= 1 and concurrency),
        ("归因", attribution),
        ("留痕", bool(steps)),
    ]


def _finish(result: HandleResult, trace: Trace, spec: TaskSpec,
            subs: list[SubGoal], agents: int) -> HandleResult:
    result.audit = _process_audit(result, trace, spec, subs, agents)
    return result


# ---------------------------------------------------------------- 主流程

def new_task_id() -> str:
    return f"H-{datetime.now():%Y%m%d-%H%M%S}-{os.getpid() % 10000:04d}"


def _step(node_id: str, action: str, output: dict, judgment: Judgment,
          latency_ms: int, cost: int = 0) -> Step:
    return Step(node_id=node_id, level=1, action=action,
                input_hash=fingerprint(output)[:16], output=output,
                judgment=judgment, cost=cost, latency_ms=latency_ms)


_INT_RE = re.compile(r"-?\d+")


def _run_score(command: str, cwd: str | Path, timeout: float) -> int:
    """跑质量分命令，取输出里第一个整数；命令失败或缺省记 0。"""
    if not command:
        return 0
    rc, output = _run_verify(command, cwd, timeout)
    if rc != 0:
        return 0
    match = _INT_RE.search(output)
    return int(match.group(0)) if match else 0


def _run_verify(command: str, cwd: str | Path, timeout: float) -> tuple[int, str]:
    try:
        proc = subprocess.run(command, shell=True, cwd=str(cwd),
                              capture_output=True, text=True, timeout=timeout)
        return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()[-TAIL:]
    except subprocess.TimeoutExpired:
        return -1, f"timeout after {timeout}s"
    except Exception as exc:
        return -1, f"{type(exc).__name__}: {exc}"


def handle(spec: TaskSpec, executor: Executor | None = None,
           store: RunStore | None = None, cwd: str | Path = ROOT,
           timeout: float = TIMEOUT,
           cached: CachedPlan | None = None,
           agents: int = 1, budget_ms: int = 0,
           plan_text: str | None = None, progress: bool = False,
           formalizer_vid: str = "") -> HandleResult:
    """执行一条任务并用外部命令验收；无论成败都落一条轨迹。"""
    executor = executor or FileWriteExecutor()
    store = store or RunStore(RUNS_DIR / "handle")
    task_id = spec.task_id or new_task_id()
    content = plan_text if plan_text is not None else load_task_text(spec.text)
    if cached is not None:
        subgoals, source = cached.subgoals, cached.source
    else:
        subgoals, source = decompose(content, spec.verify)
    trace = Trace(task_id=task_id, task_text=spec.text,
                  verifier_vid=VERIFIER_VID,
                  facts={"verify": spec.verify, "cwd": str(cwd),
                         "executor": executor.name,
                         "formalizer_vid": formalizer_vid,
                         "decomposition": source or "none"})

    if subgoals:
        return _handle_subgoals(spec, trace, subgoals, source, executor,
                                store, cwd, timeout, cached, agents,
                                budget_ms, progress, formalizer_vid)

    parsed = executor.match(content)
    if parsed is None:
        trace.steps.append(_step(f"{task_id}#dispatch", "dispatch",
                                 {"reason": "no_executor", "text": spec.text},
                                 Judgment.GIVEUP, 0))
        trace.outcome = Status.UNKNOWN
        store.append_trace(trace)
        return _finish(HandleResult(spec=spec, trace=trace, status="unsupported",
                                    executor="none", agents_used=0,
                                    stop_reason="unsupported"),
                       trace, spec, [], agents)

    trace.attempts = 1
    started = time.monotonic()
    try:
        execution = executor.run(parsed)
    except Exception as exc:                       # 执行器自身出错也要留痕
        trace.steps.append(_step(f"{task_id}#exec", "execute",
                                 {"error": f"{type(exc).__name__}: {exc}"},
                                 Judgment.GIVEUP,
                                 int((time.monotonic() - started) * 1000)))
        trace.outcome = Status.FAIL
        store.append_trace(trace)
        store.append_negative(trace, f"execute_error: {type(exc).__name__}")
        return _finish(HandleResult(spec=spec, trace=trace, status="failed",
                                    executor=executor.name),
                       trace, spec, [], agents)

    trace.steps.append(_step(f"{task_id}#exec", "write_file"
                             if executor.name == "write_file" else "execute",
                             execution.get("output", execution), Judgment.OK,
                             int((time.monotonic() - started) * 1000)))

    rc, output = _run_verify(spec.verify, cwd, timeout)
    trace.steps.append(_step(f"{task_id}#verify", "verify",
                             {"rc": rc, "output": output},
                             Judgment.OK if rc == 0 else Judgment.RETRY, 0))
    trace.outcome = Status.PASS if rc == 0 else Status.FAIL
    store.append_trace(trace)
    if rc != 0:
        store.append_negative(trace, f"verify_failed: rc={rc}")

    if rc == 0 and cached is None:
        _save_plan(store, spec, [], "")
    return _finish(HandleResult(spec=spec, trace=trace,
                                status="passed" if rc == 0 else "failed",
                                executor=executor.name, verify_rc=rc,
                                verify_output=output, execution=execution,
                                plan_reused=cached is not None,
                                stop_reason="single"),
                   trace, spec, [], agents)


def _run_subgoal(sub: SubGoal, executor: Executor, cwd: str | Path,
                 timeout: float) -> dict:
    """执行一个子目标（动作 + 自身验收），返回结果字典；不写 trace（线程安全）。"""
    started = time.monotonic()
    if sub.action:
        parsed = executor.match(sub.action)
        if parsed is None:
            return {"ok": False, "error": "no_executor",
                    "ms": int((time.monotonic() - started) * 1000)}
        try:
            executor.run(parsed)
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                    "ms": int((time.monotonic() - started) * 1000)}
    rc, output = _run_verify(sub.verify, cwd, timeout)
    return {"ok": rc == 0, "rc": rc, "output": output,
            "ms": int((time.monotonic() - started) * 1000)}


def _handle_subgoals(spec: TaskSpec, trace: Trace, subgoals: list[SubGoal],
                     source: str, executor: Executor, store: RunStore,
                     cwd: str | Path, timeout: float,
                     cached: CachedPlan | None = None,
                     agents: int = 1, budget_ms: int = 0,
                     progress: bool = False,
                     formalizer_vid: str = "") -> HandleResult:
    """执行子目标；能并行就并行（写域互斥），最后跑全局验收。"""
    task_id = trace.task_id
    trace.attempts = 1
    execution: dict = {}
    # 纯验收子目标（来自 && 拆分）先执行一次全局动作
    if source == "verify":
        parsed = executor.match(trace.task_text)
        if parsed is None:
            trace.steps.append(_step(f"{task_id}#dispatch", "dispatch",
                                     {"reason": "no_executor", "text": spec.text},
                                     Judgment.GIVEUP, 0))
            trace.outcome = Status.UNKNOWN
            store.append_trace(trace)
            return _finish(HandleResult(spec=spec, trace=trace,
                                        status="unsupported", executor="none",
                                        agents_used=0, stop_reason="unsupported"),
                           trace, spec, subgoals, agents)
        try:
            execution = executor.run(parsed)
        except Exception as exc:
            trace.steps.append(_step(f"{task_id}#exec", "execute",
                                     {"error": f"{type(exc).__name__}: {exc}"},
                                     Judgment.GIVEUP, 0))
            trace.outcome = Status.FAIL
            store.append_trace(trace)
            store.append_negative(trace, f"execute_error: {type(exc).__name__}")
            return _finish(HandleResult(spec=spec, trace=trace, status="failed",
                                        executor=executor.name),
                           trace, spec, subgoals, agents)

    # 写域冲突检测：同一路径的多个子目标不能并行
    paths: list[str] = []
    for sub in subgoals:
        if sub.action:
            parsed = executor.match(sub.action)
            if parsed is not None:
                paths.append(parsed["path"])
    conflict = len(paths) != len(set(paths))
    parallel = agents > 1 and len(subgoals) > 1 and not conflict
    if parallel:
        stop_reason = ""
    elif conflict:
        stop_reason = "write_conflict"
    elif len(subgoals) == 1:
        stop_reason = "single"
    else:
        stop_reason = "serial"
    workers = min(agents, len(subgoals)) if parallel else 1

    started = time.monotonic()
    outcomes: list[dict | None] = [None] * len(subgoals)
    if parallel:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_run_subgoal, sub, executor, cwd, timeout): index
                for index, sub in enumerate(subgoals)}
            for future in as_completed(futures):
                index = futures[future]
                outcome = future.result()
                outcomes[index] = outcome
                if progress:
                    print(f"[progress] 子目标 {index + 1}/{len(subgoals)} "
                          f"{'pass' if outcome['ok'] else 'fail'} "
                          f"{outcome['ms']}ms", flush=True)
    else:
        for index, sub in enumerate(subgoals):
            outcome = _run_subgoal(sub, executor, cwd, timeout)
            outcomes[index] = outcome
            if progress:
                print(f"[progress] 子目标 {index + 1}/{len(subgoals)} "
                      f"{'pass' if outcome['ok'] else 'fail'} "
                      f"{outcome['ms']}ms", flush=True)
    wall_ms = int((time.monotonic() - started) * 1000)

    passed = 0
    last_rc = -1
    for index, (sub, oc) in enumerate(zip(subgoals, outcomes), 1):
        assert oc is not None
        if "rc" in oc:
            last_rc = oc["rc"]
        out = {"label": sub.label, "verify": sub.verify}
        out.update({k: oc[k] for k in ("rc", "output", "error") if k in oc})
        judgment = (Judgment.OK if oc["ok"]
                    else (Judgment.GIVEUP if oc.get("error") else Judgment.RETRY))
        trace.steps.append(_step(f"{task_id}#sg{index}", "subgoal", out,
                                 judgment, oc["ms"]))
        if oc["ok"]:
            passed += 1

    over_budget = bool(budget_ms) and wall_ms > budget_ms
    global_rc = 0
    output = ""
    if source == "file" and not over_budget:
        global_rc, output = _run_verify(spec.verify, cwd, timeout)
        trace.steps.append(_step(f"{task_id}#verify", "verify",
                                 {"rc": global_rc, "output": output},
                                 Judgment.OK if global_rc == 0 else Judgment.RETRY,
                                 0))
    if over_budget:
        global_rc = -1
        output = f"budget_exhausted ({wall_ms}ms > {budget_ms}ms)"
        trace.steps.append(_step(f"{task_id}#budget", "budget",
                                 {"wall_ms": wall_ms, "budget_ms": budget_ms},
                                 Judgment.GIVEUP, 0))
    all_ok = passed == len(subgoals) and global_rc == 0 and not over_budget
    trace.outcome = Status.PASS if all_ok else Status.FAIL
    store.append_trace(trace)
    if not all_ok:
        store.append_negative(trace, f"subgoals_passed={passed}/{len(subgoals)}")

    if all_ok and cached is None:
        _save_plan(store, spec, subgoals, source)
    return _finish(HandleResult(
        spec=spec, trace=trace,
        status="passed" if all_ok else "failed",
        executor=executor.name if source == "verify" else "plan",
        verify_rc=global_rc if source == "file" else last_rc,
        verify_output=output, execution=execution,
        subgoals=subgoals, subgoals_passed=passed,
        plan_reused=cached is not None,
        agents_used=workers,
        stop_reason="budget_exhausted" if over_budget else stop_reason,
        wall_ms=wall_ms, budget_ms=budget_ms,
        formalizer_vid=formalizer_vid),
        trace, spec, subgoals, agents)


def handle_twice(spec: TaskSpec, executor: Executor | None = None,
                 store: RunStore | None = None, cwd: str | Path = ROOT,
                 timeout: float = TIMEOUT) -> tuple[HandleResult, HandleResult]:
    """连跑两次：第二次命中计划缓存，不重新分解（模型调用计数为 0）。"""
    store = store or RunStore(RUNS_DIR / "handle")
    first = handle(spec, executor=executor, store=store, cwd=cwd, timeout=timeout)
    plan = _load_plan(store, spec)
    second = handle(spec, executor=executor, store=store, cwd=cwd,
                    timeout=timeout, cached=plan)
    return first, second


def handle_candidates(spec: TaskSpec, out: str | Path, contents: list[str],
                      score_command: str = "", store: RunStore | None = None,
                      cwd: str | Path = ROOT, timeout: float = TIMEOUT,
                      model_calls: int = 0, judge=None,
                      budget_ms: int = 0, progress: bool = False,
                      formalizer_vid: str = "", repairer=None,
                      repair_rounds: int = 0) -> HandleResult:
    """N 份候选逐份写入 out、验收、打分，留下分数最高且通过的那份。"""
    if not contents:
        raise CandidateError("没有候选")
    store = store or RunStore(RUNS_DIR / "handle")
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    task_id = spec.task_id or new_task_id()
    trace = Trace(task_id=task_id, task_text=spec.text,
                  verifier_vid=VERIFIER_VID,
                  facts={"verify": spec.verify, "out": str(path),
                         "score": score_command,
                         "judge_vid": judge.vid if judge is not None else "",
                         "formalizer_vid": formalizer_vid,
                         "repairer_vid": repairer.vid if repairer is not None else "",
                         "budget_ms": budget_ms,
                         "candidates": len(contents)})
    trace.attempts = 1
    started = time.monotonic()
    best: tuple[int, int, str] | None = None
    passed = 0
    single = 0
    last_rc, last_output = -1, ""
    stopped = False
    repaired_total = 0
    for index, content in enumerate(contents, 1):
        if budget_ms and int((time.monotonic() - started) * 1000) > budget_ms:
            stopped = True
            break
        data = content if content.endswith("\n") else content + "\n"
        path.write_text(data, encoding="utf-8")
        rc, output = _run_verify(spec.verify, cwd, timeout)
        repaired_here = 0
        while rc != 0 and repairer is not None and repaired_here < repair_rounds:
            fixed = None
            for _ in range(2):          # 空响应/异常重试一次，再放弃
                try:
                    fixed = repairer.repair(spec.text, data, output)
                    break
                except Exception:
                    continue
            if not fixed:
                break
            data = fixed if fixed.endswith("\n") else fixed + "\n"
            path.write_text(data, encoding="utf-8")
            rc, output = _run_verify(spec.verify, cwd, timeout)
            repaired_here += 1
            trace.steps.append(_step(
                f"{task_id}#repair{index}-{repaired_here}", "repair",
                {"round": repaired_here, "rc": rc, "output": output},
                Judgment.OK if rc == 0 else Judgment.RETRY, 0))
        repaired_total += repaired_here
        last_rc, last_output = rc, output
        score = 0
        if rc == 0:
            if judge is not None:
                try:
                    judged = judge.score(spec.text, data)
                    score = judged.score
                except Exception:
                    score = 0
            else:
                score = _run_score(score_command, cwd, timeout)
        if index == 1:
            single = score
        ok = rc == 0
        if ok:
            passed += 1
        trace.steps.append(_step(
            f"{task_id}#cand{index}", "candidate",
            {"index": index, "rc": rc, "score": score,
             "output": output, "repaired": repaired_here},
            Judgment.OK if ok else Judgment.RETRY, 0))
        if progress:
            print(f"[progress] 候选 {index}/{len(contents)} "
                  f"{'pass' if ok else 'fail'} score={score}", flush=True)
        if ok and (best is None or score > best[0]):
            best = (score, index, data)

    if best is not None and not stopped:
        path.write_text(best[2], encoding="utf-8")
        rc, output = _run_verify(spec.verify, cwd, timeout)
    elif stopped:
        rc = -1
        output = f"budget_exhausted ({budget_ms}ms)"
    else:
        rc, output = last_rc, last_output
    ok = best is not None and not stopped and rc == 0
    if stopped:
        trace.steps.append(_step(f"{task_id}#budget", "budget",
                                 {"budget_ms": budget_ms}, Judgment.GIVEUP, 0))
    else:
        trace.steps.append(_step(f"{task_id}#verify", "verify",
                                 {"rc": rc, "output": output},
                                 Judgment.OK if ok else Judgment.RETRY, 0))
    trace.outcome = Status.PASS if ok else Status.FAIL
    store.append_trace(trace)
    if not ok:
        store.append_negative(trace, f"no_candidate_passed ({len(contents)})")
    result = HandleResult(
        spec=spec, trace=trace, status="passed" if ok else "failed",
        executor="candidates", verify_rc=rc, verify_output=output,
        model_calls=model_calls,
        stop_reason="budget_exhausted" if stopped else "candidates",
        budget_ms=budget_ms,
        judge_vid=judge.vid if judge is not None else "",
        formalizer_vid=formalizer_vid, repaired=repaired_total,
        wall_ms=int((time.monotonic() - started) * 1000),
        candidates_total=len(contents), candidates_passed=passed,
        selected=best[1] if best else 0,
        best_score=best[0] if best else 0, single_score=single)
    return _finish(result, trace, spec, [], 1)


def _audit_lines(result: HandleResult) -> list[str]:
    ok = sum(1 for _, passed in result.audit if passed)
    detail = " ".join(f"{name}={'✓' if passed else '✗'}"
                      for name, passed in result.audit)
    return [f"  agents={result.agents_used}  wall_ms={result.wall_ms}"
            + (f"  stop={result.stop_reason}" if result.stop_reason else ""),
            f"process {ok}/{len(result.audit)}  {detail}"]


def render(result: HandleResult, explain: bool = False,
           show_audit: bool = False) -> str:
    lines = [f"任务 {result.trace.task_id}",
             f"  执行  {result.executor}"]
    if result.plan_reused:
        lines.append("  复用  计划缓存")
    if result.formalizer_vid:
        lines.append(f"  formalized={result.formalizer_vid}")
    if result.repaired:
        lines.append(f"  repaired={result.repaired}")
    if result.status == "unsupported":
        lines.append("unsupported: 没有执行器能处理这条任务")
        lines.append("  可用模式：在 <路径> 写入 <内容> / 在 <路径> 追加 <内容> / "
                     "在 <路径> 末尾加一行 <内容>")
        if show_audit:
            lines.extend(_audit_lines(result))
        return "\n".join(lines)
    if result.judge_vid:
        lines.append(f"  judge={result.judge_vid}")
    if result.stop_reason == "budget_exhausted":
        lines.append(f"  stop=budget_exhausted  budget_ms={result.budget_ms}")
    if result.candidates_total:
        lines.append(f"  候选  {result.candidates_total} 份  通过 "
                     f"{result.candidates_passed}  选中 {result.selected}")
        lines.append(f"quality best={result.best_score} "
                     f"single={result.single_score} "
                     f"candidates={result.candidates_total}")
    if result.subgoals:
        lines.append(f"  子目标 {result.subgoals_passed}/{len(result.subgoals)}")
        if explain:
            for index, sub in enumerate(result.subgoals, 1):
                lines.append(f"     子目标 {index}: {sub.action or '（纯验收）'}"
                             f"  验收：{sub.verify}")
    lines.append(f"  验收  rc={result.verify_rc}  {result.spec.verify}")
    if result.verify_output:
        lines.append("  输出  " + result.verify_output.replace("\n", " | "))
    lines.append("verify: " + ("pass" if result.passed else "fail"))
    if show_audit:
        lines.extend(_audit_lines(result))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="任意任务 + 验收命令（M1）")
    parser.add_argument("--task", required=True, help="任务描述文本或任务文件路径")
    parser.add_argument("--verify", required=True, help="全局验收命令（rc=0 算通过）")
    parser.add_argument("--explain", action="store_true", help="打印分解出的子目标")
    parser.add_argument("--twice", action="store_true",
                        help="连跑两次，第二次复用计划缓存")
    parser.add_argument("--agents", type=int, default=1,
                        help="并行执行子目标的 worker 数（默认 1）")
    parser.add_argument("--audit", action="store_true",
                        help="打印过程审计 6 项")
    parser.add_argument("--out", default="", help="候选/内容写入路径")
    parser.add_argument("--candidates", type=int, default=0,
                        help="用 DeepSeek 生成 N 份候选")
    parser.add_argument("--candidates-from", default="",
                        help="从 JSONL 读候选（离线、可复现）")
    parser.add_argument("--score", default="", help="质量分命令，输出整数")
    parser.add_argument("--judge", action="store_true",
                        help="用 DeepSeek 按 rubric 评审候选")
    parser.add_argument("--judge-from", default="",
                        help="从 JSONL 读离线评分（可复现）")
    parser.add_argument("--budget-ms", type=int, default=0,
                        help="墙钟预算（毫秒），超时即停")
    parser.add_argument("--formalize", action="store_true",
                        help="用 DeepSeek 把自由文本任务转成计划")
    parser.add_argument("--formalize-from", default="",
                        help="从计划文件读（离线、可复现）")
    parser.add_argument("--progress", action="store_true",
                        help="实时打印子目标/候选进度")
    parser.add_argument("--repair", type=int, default=0,
                        help="失败后把验收输出喂回模型的最大修复轮数")
    parser.add_argument("--repair-from", default="",
                        help="从 JSONL 读离线修复响应（可复现）")
    parser.add_argument("--auto-plan", action="store_true",
                        help="从验收谓词反推动作（grep -q / test -f / &&）")
    parser.add_argument("--width", type=int, default=2,
                        help="自动规划最多尝试几个候选方案")
    parser.add_argument("--depth", type=int, default=1,
                        help="自动规划允许的重分解深度（0=只有朴素方案）")
    parser.add_argument("--explain-plan", action="store_true",
                        help="打印 AND-OR 图与证明树")
    parser.add_argument("--id", default="", help="任务 id，缺省自动生成")
    parser.add_argument("--runs", default="", help="产物目录，缺省 runs/handle")
    parser.add_argument("--timeout", type=float, default=TIMEOUT)
    args = parser.parse_args(argv)

    spec = TaskSpec(text=args.task, verify=args.verify, task_id=args.id)
    store = RunStore(Path(args.runs) if args.runs else RUNS_DIR / "handle")
    if args.auto_plan:
        from .backward_plan import plan_text
        from .planner import Plan, build_plan, render_plan
        from .strategy import StrategyStore, shape_of
        plan = build_plan(args.verify, depth=max(0, args.depth))
        if plan is None:
            print("自动规划失败：验收条件里没有可反推动作（支持 grep -q / test -f / &&）")
            return 2
        shape = shape_of(args.verify)
        sstore = StrategyStore(store.root / "strategies.jsonl")
        learned = sstore.best(shape)
        branches = list(plan.branches)
        if learned is not None:
            branches.sort(key=lambda b: (b.name != learned, -b.priority))
        ordered = Plan(tuple(branches), plan.depth, plan.atoms)
        stats = {b.name: sstore.p(shape, b.name) for b in ordered.branches}
        width = max(1, args.width)
        outcomes: list[bool] = []
        result = None
        for branch in ordered.branches[:width]:
            result = handle(spec, store=store, timeout=args.timeout,
                            agents=max(1, args.agents),
                            budget_ms=args.budget_ms,
                            plan_text=plan_text(list(branch.subgoals)),
                            progress=args.progress,
                            formalizer_vid="backward-plan-v1")
            outcomes.append(result.passed)
            sstore.record(shape, branch.name, result.passed)
            if result.passed:
                break
        print(render(result, explain=args.explain, show_audit=args.audit))
        print(f"search={len(outcomes)}/{len(ordered.branches)}")
        print(f"or={len(ordered.branches)} tried={len(outcomes)} depth={ordered.depth}")
        print(f"strategy={'hit' if learned is not None else 'miss'}")
        if args.explain_plan:
            print(render_plan(ordered, outcomes, stats))
        return 0 if result is not None and result.passed else 1
    if args.formalize or args.formalize_from:
        try:
            formalizer = (FileFormalizer(args.formalize_from)
                          if args.formalize_from else DeepSeekFormalizer())
            plan_text = formalizer.formalize(args.task)
        except (FormalizeError, DeepSeekError) as exc:
            print(f"形式化失败：{exc}")
            return 2
        if not parse_task_file(plan_text):
            print("形式化失败：计划里没有可解析的子目标（需要 ## 子目标 + 验收：）")
            return 2
        result = handle(spec, store=store, timeout=args.timeout,
                        agents=max(1, args.agents),
                        budget_ms=args.budget_ms, plan_text=plan_text,
                        progress=args.progress,
                        formalizer_vid=formalizer.vid)
        print(render(result, explain=args.explain, show_audit=args.audit))
        return 0 if result.passed else 1
    if args.candidates_from or args.candidates:
        if not args.out:
            parser.error("--candidates / --candidates-from 需要 --out")
        try:
            if args.candidates_from:
                contents = load_jsonl(args.candidates_from)
                model_calls = 0
            else:
                provider = DeepSeekCandidateProvider()
                contents = provider.generate(args.task, args.candidates)
                model_calls = provider.calls
            repairer = None
            if args.repair_from:
                repairer = FileRepairer.from_jsonl(args.repair_from)
            elif args.repair > 0:
                repairer = DeepSeekRepairer()
            judge = None
            if args.judge_from:
                judge = FileJudge.from_jsonl(args.judge_from)
            elif args.judge:
                judge = DeepSeekJudge()
            result = handle_candidates(
                spec, args.out, contents, score_command=args.score,
                store=store, timeout=args.timeout, model_calls=model_calls,
                judge=judge, budget_ms=args.budget_ms,
                repairer=repairer, repair_rounds=max(0, args.repair))
        except (CandidateError, DeepSeekError, JudgeError, RepairError) as exc:
            print(f"候选失败：{exc}")
            return 2
        print(render(result, explain=args.explain, show_audit=args.audit))
        return 0 if result.passed else 1
    if args.twice:
        first, second = handle_twice(spec, store=store, timeout=args.timeout)
        print(render(first))
        print("--- 第二次（复用计划）---")
        print(render(second))
        print("model_calls=0")
        result = second
    else:
        result = handle(spec, store=store, timeout=args.timeout,
                        agents=max(1, args.agents),
                        budget_ms=args.budget_ms, progress=args.progress)
        print(render(result, explain=args.explain, show_audit=args.audit))
    if result.status == "unsupported":
        return 2
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())