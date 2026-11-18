"""任意任务入口（M0）：任务文本 + 验收命令 → 执行 → 验收 → 轨迹落盘。

M0 只做确定性模式（内环），不调模型：有执行器匹配就执行，匹配不上就明确
返回 unsupported，不能把"判不了"伪装成"做完了"。验收永远是外部 shell 命令，
实现与模型都不得自评——这是 v0.1 原则 P1 的落点。

用法：
    python3 -m mvp.handle --task "在 /tmp/xm-hello.txt 写入 hello" \\
        --verify "grep -q hello /tmp/xm-hello.txt"

退出码：0 验收通过；1 执行或验收失败；2 没有执行器能处理（unsupported）。

M1 会把"执行"换成逆向分解后的子目标执行；M2 会在同一接口上挂多候选。
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


@dataclass
class HandleResult:
    spec: TaskSpec
    trace: Trace
    status: str                 # passed | failed | unsupported
    executor: str
    verify_rc: int = -1
    verify_output: str = ""
    execution: dict = field(default_factory=dict)

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
    ``write <内容> to <路径>``。
    """

    name = "write_file"
    _zh_write = re.compile(r"在\s+(?P<path>[^\s，,。]+)\s*写入\s*(?P<content>.+)")
    _zh_append = re.compile(r"在\s+(?P<path>[^\s，,。]+)\s*追加\s*(?P<content>.+)")
    _en_write = re.compile(r"write\s+(?P<content>.+?)\s+to\s+(?P<path>\S+)", re.I)

    def match(self, text: str) -> dict | None:
        for mode, pattern in (("append", self._zh_append),
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


# ---------------------------------------------------------------- 主流程

def new_task_id() -> str:
    return f"H-{datetime.now():%Y%m%d-%H%M%S}-{os.getpid() % 10000:04d}"


def _step(node_id: str, action: str, output: dict, judgment: Judgment,
          latency_ms: int, cost: int = 0) -> Step:
    return Step(node_id=node_id, level=1, action=action,
                input_hash=fingerprint(output)[:16], output=output,
                judgment=judgment, cost=cost, latency_ms=latency_ms)


def handle(spec: TaskSpec, executor: Executor | None = None,
           store: RunStore | None = None, cwd: str | Path = ROOT,
           timeout: float = TIMEOUT) -> HandleResult:
    """执行一条任务并用外部命令验收；无论成败都落一条轨迹。"""
    executor = executor or FileWriteExecutor()
    store = store or RunStore(RUNS_DIR / "handle")
    task_id = spec.task_id or new_task_id()
    trace = Trace(task_id=task_id, task_text=spec.text,
                  verifier_vid=VERIFIER_VID,
                  facts={"verify": spec.verify, "cwd": str(cwd),
                         "executor": executor.name})

    parsed = executor.match(spec.text)
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

    started = time.monotonic()
    try:
        proc = subprocess.run(spec.verify, shell=True, cwd=str(cwd),
                              capture_output=True, text=True, timeout=timeout)
        rc, output = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
        judgment = Judgment.OK if rc == 0 else Judgment.RETRY
    except subprocess.TimeoutExpired:
        rc, output, judgment = -1, f"timeout after {timeout}s", Judgment.RETRY
    except Exception as exc:
        rc, output, judgment = -1, f"{type(exc).__name__}: {exc}", Judgment.GIVEUP

    tail = output.strip()[-TAIL:]
    trace.steps.append(_step(f"{task_id}#verify", "verify",
                             {"rc": rc, "output": tail}, judgment,
                             int((time.monotonic() - started) * 1000)))
    trace.outcome = Status.PASS if rc == 0 else Status.FAIL
    store.append_trace(trace)
    if rc != 0:
        store.append_negative(trace, f"verify_failed: rc={rc}")

    return HandleResult(spec=spec, trace=trace,
                        status="passed" if rc == 0 else "failed",
                        executor=executor.name, verify_rc=rc,
                        verify_output=tail, execution=execution)


def render(result: HandleResult) -> str:
    lines = [f"任务 {result.trace.task_id}",
             f"  执行  {result.executor}"]
    if result.status == "unsupported":
        lines.append("unsupported: 没有执行器能处理这条任务")
        lines.append("  可用模式：在 <路径> 写入 <内容> / 在 <路径> 追加 <内容>")
        return "\n".join(lines)
    lines.append(f"  验收  rc={result.verify_rc}  {result.spec.verify}")
    if result.verify_output:
        lines.append("  输出  " + result.verify_output.replace("\n", " | "))
    lines.append("verify: " + ("pass" if result.passed else "fail"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="任意任务 + 验收命令（M0）")
    parser.add_argument("--task", required=True, help="任务描述文本")
    parser.add_argument("--verify", required=True, help="验收命令（rc=0 算通过）")
    parser.add_argument("--id", default="", help="任务 id，缺省自动生成")
    parser.add_argument("--runs", default="", help="产物目录，缺省 runs/handle")
    parser.add_argument("--timeout", type=float, default=TIMEOUT)
    args = parser.parse_args(argv)

    spec = TaskSpec(text=args.task, verify=args.verify, task_id=args.id)
    store = RunStore(Path(args.runs) if args.runs else RUNS_DIR / "handle")
    result = handle(spec, store=store, timeout=args.timeout)
    print(render(result))
    if result.status == "unsupported":
        return 2
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())