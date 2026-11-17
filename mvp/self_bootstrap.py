"""自举：从系统自己的轨迹里长出 H1 的任务清单。

H1 问的是「任务里有没有分布」。它需要的是历史，不是人。
只要每次执行都把任务描述写进 Trace（`task_text`），系统就能自己攒样本：

    跑任务 → 轨迹落盘（含 task_text）→ 导出 jsonl → cluster_check → H1 判定

所以「50 条真实任务」不必由人写：系统跑够 50 次，清单就有了。人只需要在
部署时决定「允许记录哪些字段」（脱敏由调用方负责）。

用法：
    python3 -m mvp.self_bootstrap --dir runs/<dir> --out tasks.jsonl [--check]
    python3 -m mvp.self_bootstrap --demo
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .contracts import Judgment, Status, Step, Trace
from .paths import RUNS_DIR
from .persistence import RunStore

CLUSTER_TOOL = Path(__file__).resolve().parent.parent / "tools" / "cluster_check.py"


def collect_tasks(paths: list[Path]) -> list[dict]:
    """从若干 traces.jsonl 里抽任务清单：按 task_text 去重，空描述跳过。"""
    seen: dict[str, dict] = {}
    for path in paths:
        sink = RunStore(path.parent)
        for trace in sink.load_traces():
            text = (trace.task_text or "").strip()
            if not text or text in seen:
                continue
            seen[text] = {"id": trace.task_id, "text": text}
    return list(seen.values())


def write_jsonl(tasks: list[dict], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for task in tasks:
            f.write(json.dumps(task, ensure_ascii=False) + "\n")
    return out


def run_cluster(path: Path, threshold: float = 0.24) -> int:
    """调用已有的 H1 工具。返回子进程退出码。"""
    cmd = [sys.executable, str(CLUSTER_TOOL), str(path), "--threshold", str(threshold)]
    return subprocess.call(cmd)


def _write_fixture(root: Path) -> None:
    """造 6 个域 × 6 条任务轨迹，模拟「系统跑了一阵子」。"""
    import shutil

    if root.exists():
        shutil.rmtree(root)
    sink = RunStore(root)
    domains = [
        ("差旅报销", "核对差旅报销：住宿单价是否符合城市等级上限"),
        ("合同审查", "审查采购合同：付款条款是否与付款计划一致"),
        ("代码评审", "评审代码提交：是否包含单元测试且覆盖率达标"),
        ("日志分析", "分析网关日志：统计接口错误率并按服务聚合"),
        ("报表生成", "生成经营报表：按月汇总部门费用与同比"),
        ("客户回复", "起草客户回复：投诉处理方案与时限"),
    ]
    for domain, text in domains:
        for i in range(6):
            tid = f"{domain}-{i}"
            trace = Trace(task_id=tid, verifier_vid="vf0", outcome=Status.PASS,
                          task_text=f"{text}（第 {i} 次）")
            trace.steps = [Step(f"{tid}#s1", 0, "run", f"h{i}",
                                {"domain": domain}, Judgment.OK, 10, 0)]
            sink.append_trace(trace)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="", help="含 traces.jsonl 的目录（可用 glob 父目录）")
    ap.add_argument("--out", default="/tmp/mvp_tasks.jsonl")
    ap.add_argument("--threshold", type=float, default=0.24)
    ap.add_argument("--check", action="store_true", help="导出后直接跑 H1 聚类")
    ap.add_argument("--demo", action="store_true", help="造一份示例轨迹再走完整流程")
    args = ap.parse_args()

    if not args.dir and not args.demo:
        ap.print_help()
        raise SystemExit(1)

    if args.demo:
        _write_fixture(RUNS_DIR / "bootstrap-demo")
        paths = sorted((RUNS_DIR / "bootstrap-demo").glob("traces.jsonl"))
    else:
        paths = sorted(Path(args.dir).rglob("traces.jsonl"))

    tasks = collect_tasks(paths)
    if not tasks:
        print("没有可用任务：轨迹里 task_text 为空。", file=sys.stderr)
        raise SystemExit(2)

    out = write_jsonl(tasks, Path(args.out))
    print(f"从 {len(paths)} 个 traces.jsonl 导出 {len(tasks)} 条任务 → {out}", flush=True)
    if len(tasks) < 10:
        print("提示：少于 10 条只适合冒烟，H1 判定需要 50 条左右。")
    print("注：轨迹里没有业务域字段，聚类报告的「主要域」会显示 ?；"
          "H1 判定只看簇数与簇规模，不依赖它。")

    if args.check or args.demo:
        print()
        code = run_cluster(out, args.threshold)
        raise SystemExit(code)


if __name__ == "__main__":
    main()