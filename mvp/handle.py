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
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .core.contracts import Judgment, Status, Step, Trace, fingerprint
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


# ---------------------------------------------------------------- 主流程

def new_task_id() -> str:
    return f"H-{datetime.now():%Y%m%d-%H%M%S}-{os.getpid() % 10000:04d}"


def _step(node_id: str, action: str, output: dict, judgment: Judgment,
          latency_ms: int, cost: int = 0) -> Step:
    return Step(node_id=node_id, level=1, action=action,
                input_hash=fingerprint(output)[:16], output=output,
                judgment=judgment, cost=cost, latency_ms=latency_ms)


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
           timeout: float = TIMEOUT) -> HandleResult:
    """执行一条任务并用外部命令验收；无论成败都落一条轨迹。"""
    executor = executor or FileWriteExecutor()
    store = store or RunStore(RUNS_DIR / "handle")
    task_id = spec.task_id or new_task_id()
    content = load_task_text(spec.text)
    subgoals, source = decompose(content, spec.verify)
    trace = Trace(task_id=task_id, task_text=spec.text,
                  verifier_vid=VERIFIER_VID,
                  facts={"verify": spec.verify, "cwd": str(cwd),
                         "executor": executor.name,
                         "decomposition": source or "none"})

    if subgoals:
        return _handle_subgoals(spec, trace, subgoals, source, executor,
                                store, cwd, timeout)

    parsed = executor.match(content)
    if parsed is None:
        trace.steps.append(_step(f"{task_id}#dispatch", "dispatch",
                                 {"reason": "no_executor", "text": spec.text},
                                 Judgment.GIVEUP, 0))
        trace.outcome = Status.UNKNOWN
        store.append_trace(trace)
        return HandleResult(spec=spec, trace=trace, status="unsupported",
                            executor="none")

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
        return HandleResult(spec=spec, trace=trace, status="failed",
                            executor=executor.name)

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

    return HandleResult(spec=spec, trace=trace,
                        status="passed" if rc == 0 else "failed",
                        executor=executor.name, verify_rc=rc,
                        verify_output=output, execution=execution)


def _handle_subgoals(spec: TaskSpec, trace: Trace, subgoals: list[SubGoal],
                     source: str, executor: Executor, store: RunStore,
                     cwd: str | Path, timeout: float) -> HandleResult:
    """逐个执行子目标、逐个验收；任务文件来源再跑一次全局验收。"""
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
            return HandleResult(spec=spec, trace=trace, status="unsupported",
                                executor="none")
        try:
            execution = executor.run(parsed)
        except Exception as exc:
            trace.steps.append(_step(f"{task_id}#exec", "execute",
                                     {"error": f"{type(exc).__name__}: {exc}"},
                                     Judgment.GIVEUP, 0))
            trace.outcome = Status.FAIL
            store.append_trace(trace)
            store.append_negative(trace, f"execute_error: {type(exc).__name__}")
            return HandleResult(spec=spec, trace=trace, status="failed",
                                executor=executor.name)

    passed = 0
    last_rc = -1
    for index, sub in enumerate(subgoals, 1):
        ok = True
        started = time.monotonic()
        if sub.action:
            parsed = executor.match(sub.action)
            if parsed is None:
                ok = False
                trace.steps.append(_step(
                    f"{task_id}#sg{index}", "subgoal",
                    {"label": sub.label, "action": sub.action,
                     "error": "no_executor"}, Judgment.GIVEUP, 0))
            else:
                try:
                    executor.run(parsed)
                except Exception as exc:
                    ok = False
                    trace.steps.append(_step(
                        f"{task_id}#sg{index}", "subgoal",
                        {"label": sub.label, "action": sub.action,
                         "error": f"{type(exc).__name__}: {exc}"},
                        Judgment.GIVEUP,
                        int((time.monotonic() - started) * 1000)))
        if ok:
            rc, output = _run_verify(sub.verify, cwd, timeout)
            last_rc = rc
            ok = rc == 0
            trace.steps.append(_step(
                f"{task_id}#sg{index}", "subgoal",
                {"label": sub.label, "verify": sub.verify, "rc": rc,
                 "output": output}, Judgment.OK if ok else Judgment.RETRY,
                int((time.monotonic() - started) * 1000)))
        if ok:
            passed += 1

    global_rc = 0
    if source == "file":
        global_rc, output = _run_verify(spec.verify, cwd, timeout)
        trace.steps.append(_step(f"{task_id}#verify", "verify",
                                 {"rc": global_rc, "output": output},
                                 Judgment.OK if global_rc == 0 else Judgment.RETRY,
                                 0))
    else:
        output = ""
    all_ok = passed == len(subgoals) and global_rc == 0
    trace.outcome = Status.PASS if all_ok else Status.FAIL
    store.append_trace(trace)
    if not all_ok:
        store.append_negative(trace, f"subgoals_passed={passed}/{len(subgoals)}")

    return HandleResult(spec=spec, trace=trace,
                        status="passed" if all_ok else "failed",
                        executor=executor.name if source == "verify" else "plan",
                        verify_rc=global_rc if source == "file" else last_rc,
                        verify_output=output, execution=execution,
                        subgoals=subgoals, subgoals_passed=passed)


def render(result: HandleResult, explain: bool = False) -> str:
    lines = [f"任务 {result.trace.task_id}",
             f"  执行  {result.executor}"]
    if result.status == "unsupported":
        lines.append("unsupported: 没有执行器能处理这条任务")
        lines.append("  可用模式：在 <路径> 写入 <内容> / 在 <路径> 追加 <内容> / "
                     "在 <路径> 末尾加一行 <内容>")
        return "\n".join(lines)
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
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="任意任务 + 验收命令（M1）")
    parser.add_argument("--task", required=True, help="任务描述文本或任务文件路径")
    parser.add_argument("--verify", required=True, help="全局验收命令（rc=0 算通过）")
    parser.add_argument("--explain", action="store_true", help="打印分解出的子目标")
    parser.add_argument("--id", default="", help="任务 id，缺省自动生成")
    parser.add_argument("--runs", default="", help="产物目录，缺省 runs/handle")
    parser.add_argument("--timeout", type=float, default=TIMEOUT)
    args = parser.parse_args(argv)

    spec = TaskSpec(text=args.task, verify=args.verify, task_id=args.id)
    store = RunStore(Path(args.runs) if args.runs else RUNS_DIR / "handle")
    result = handle(spec, store=store, timeout=args.timeout)
    print(render(result, explain=args.explain))
    if result.status == "unsupported":
        return 2
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())