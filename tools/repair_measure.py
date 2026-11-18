"""可修复失败的救回率：模型开头不知道答案，但验收输出会告诉它。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mvp.candidates import DeepSeekCandidateProvider  # noqa: E402
from mvp.core.paths import RUNS_DIR  # noqa: E402
from mvp.core.persistence import RunStore  # noqa: E402
from mvp.handle import TaskSpec, handle_candidates  # noqa: E402
from mvp.repair import DeepSeekRepairer  # noqa: E402

CASES = [
    ("join-dash", "读取 /tmp/r-in-1.txt 的内容，写一行，把每个词用 - 连接",
     "join-dash", "/tmp/r-in-1.txt", "alpha beta gamma", "/tmp/r-out-1.txt"),
    ("sum", "读取 /tmp/r-in-2.txt 里的两个整数，写一行它们的和",
     "sum", "/tmp/r-in-2.txt", "3 4", "/tmp/r-out-2.txt"),
    ("reverse", "读取 /tmp/r-in-3.txt，写一行它的倒序",
     "reverse", "/tmp/r-in-3.txt", "nixiang", "/tmp/r-out-3.txt"),
]


def main() -> int:
    provider = DeepSeekCandidateProvider()
    repairer = DeepSeekRepairer()
    store = RunStore(RUNS_DIR / "repair-measure")
    rows = []
    for name, task, mode, inp, raw, out in CASES:
        Path(inp).write_text(raw + "\n", encoding="utf-8")
        contents = provider.generate(task, 1)
        verify = f"python3 tasks/repair/verify.py {mode} {inp} {out}"
        result = handle_candidates(
            TaskSpec(text=task, verify=verify), out, contents,
            repairer=repairer, repair_rounds=2, store=store)
        initial_pass = result.passed and result.repaired == 0
        repaired_pass = result.passed and result.repaired > 0
        rows.append((name, initial_pass, repaired_pass, result.repaired,
                     result.passed))

    print(f"{'任务':10s} {'initial':8s} {'repaired':9s} {'轮数':4s} {'final':6s}")
    for name, initial, repaired, rounds, passed in rows:
        print(f"{name:10s} {'pass' if initial else 'fail':8s} "
              f"{'pass' if repaired else 'fail':9s} {rounds:4d} "
              f"{'pass' if passed else 'fail':6s}")
    initial_n = sum(1 for row in rows if row[1])
    repaired_n = sum(1 for row in rows if row[2])
    failed_n = sum(1 for row in rows if not row[4])
    fail_cases = len(rows) - initial_n
    rate = repaired_n / fail_cases if fail_cases else 0.0
    print(f"汇总 tasks={len(rows)} initial_pass={initial_n} "
          f"repaired_pass={repaired_n} failed={failed_n} "
          f"rescue_rate={rate:.2f}")
    return 0 if failed_n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
