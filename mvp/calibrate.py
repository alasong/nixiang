"""rubric 校准：评审分与人类分的一致率。

输入一行一个样本（JSONL）：{"task": ..., "content": ..., "human": 0-100}；
评审可以离线注入（--judge-from）或真实模型（--judge）。

判据：|judge - human| ≤ tolerance 记为一致；一致率 ≥ min-agree 退出 0，否则 1。
它只做算术与门限；**真实一致率要有你的人工标注样本**。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from .judge import DeepSeekJudge, FileJudge, JudgeError


@dataclass(frozen=True)
class Sample:
    task: str
    content: str
    human: int


def load_samples(path: str | Path) -> list[Sample]:
    samples: list[Sample] = []
    for line_no, line in enumerate(
            Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"第 {line_no} 行不是合法 JSON：{exc}") from exc
        missing = [key for key in ("task", "content", "human") if key not in obj]
        if missing:
            raise ValueError(f"第 {line_no} 行缺字段：{', '.join(missing)}")
        try:
            human = int(obj["human"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"第 {line_no} 行 human 不是整数") from exc
        samples.append(Sample(str(obj["task"]), str(obj["content"]), human))
    if not samples:
        raise ValueError("没有样本")
    return samples


def evaluate(samples: list[Sample], judge, tolerance: int = 10) -> dict:
    details: list[dict] = []
    diffs: list[int] = []
    for sample in samples:
        judged = judge.score(sample.task, sample.content).score
        diff = judged - sample.human
        diffs.append(diff)
        details.append({"task": sample.task, "human": sample.human,
                        "judge": judged, "diff": diff})
    n = len(diffs)
    agree = sum(1 for diff in diffs if abs(diff) <= tolerance)
    return {
        "n": n,
        "agree": agree,
        "tolerance": tolerance,
        "mae": sum(abs(diff) for diff in diffs) / n,
        "bias": sum(diffs) / n,
        "details": details,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m mvp.calibrate")
    parser.add_argument("--samples", required=True,
                        help="JSONL：task/content/human(0-100)")
    parser.add_argument("--judge", action="store_true",
                        help="用 DeepSeek 评审（需要 key）")
    parser.add_argument("--judge-from", default="",
                        help="从 JSONL 读离线评分（可复现）")
    parser.add_argument("--tolerance", type=int, default=10)
    parser.add_argument("--min-agree", type=float, default=0.8)
    args = parser.parse_args(argv)

    samples = load_samples(args.samples)
    try:
        judge = (FileJudge.from_jsonl(args.judge_from)
                 if args.judge_from else DeepSeekJudge())
        report = evaluate(samples, judge, tolerance=args.tolerance)
    except (JudgeError, ValueError) as exc:
        print(f"校准失败：{exc}")
        return 2

    for row in report["details"]:
        print(f"  {row['task'][:18]:18s} human={row['human']:3d} "
              f"judge={row['judge']:3d} diff={row['diff']:+4d}")
    rate = report["agree"] / report["n"]
    print(f"agree={report['agree']}/{report['n']} "
          f"mae={report['mae']:.1f} bias={report['bias']:+.1f} "
          f"tolerance=±{report['tolerance']}")
    return 0 if rate >= args.min_agree else 1


if __name__ == "__main__":
    sys.exit(main())