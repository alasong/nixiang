"""真实模型小样本测量（需要 DEEPSEEK_API_KEY）。

5 个任务、每个 3 份候选，比较「第 1 份」与「评审选出最好的那份」：
通过数、评审分、墙钟。读数写 runs/measure/readings.jsonl。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mvp.candidates import DeepSeekCandidateProvider  # noqa: E402
from mvp.core.paths import RUNS_DIR  # noqa: E402
from mvp.core.persistence import RunStore  # noqa: E402
from mvp.judge import DeepSeekJudge  # noqa: E402
from mvp.measure import Item, measure, summarize  # noqa: E402

BASIC = [
    Item("intro", "写两行介绍 nixiang，必须包含 nixiang",
         "grep -q nixiang /tmp/m-1.txt", "/tmp/m-1.txt"),
    Item("points", "写三条要点说明过程与方法优先",
         "grep -q 过程 /tmp/m-2.txt", "/tmp/m-2.txt"),
    Item("json", '输出一行 JSON，包含 "ok": true',
         "grep -q '\"ok\"' /tmp/m-3.txt", "/tmp/m-3.txt"),
    Item("code", "写一行 Python 代码，运行后输出 7",
         "python3 /tmp/m-4.py | grep -q 7", "/tmp/m-4.py"),
    Item("lines", "写五行关于双环的说明",
         "test $(wc -l < /tmp/m-5.txt) -ge 3", "/tmp/m-5.txt"),
    Item("strict-bullets", "写三行，每行以 '- ' 开头，内容分别是取证、结论、记录",
         "test $(grep -c '^- ' /tmp/m-6.txt) -ge 3", "/tmp/m-6.txt"),
    Item("strict-json", '输出一行 JSON，只包含 "ok" 和 "version" 两个键',
         "python3 -c \"import json;d=json.load(open('/tmp/m-7.json'));"
         "assert set(d)=={'ok','version'}\"", "/tmp/m-7.json"),
    Item("strict-lines", "写恰好两行，每行不超过 15 个汉字",
         "python3 -c \"lines=open('/tmp/m-8.txt').read().splitlines();"
         "assert len(lines)==2 and all(len(l)<=15 for l in lines)\"",
         "/tmp/m-8.txt"),
]

HARD = [
    Item("hard-shell",
         "写一个 bash 脚本：若 /tmp/h-hard-input.txt 存在，就把内容复制到 "
         "/tmp/h-hard-out.txt；否则不创建输出",
         "rm -f /tmp/h-hard-out.txt; printf 'alpha\\n' > /tmp/h-hard-input.txt; "
         "bash /tmp/h-hard.sh && grep -q alpha /tmp/h-hard-out.txt",
         "/tmp/h-hard.sh"),
    Item("hard-python",
         "写一个 Python 脚本：读取 /tmp/h-sum-in.txt 里各占一行的两个整数，打印它们的和",
         "printf '3\\n4\\n' > /tmp/h-sum-in.txt; "
         "python3 /tmp/h-sum.py | grep -qx 7",
         "/tmp/h-sum.py"),
    Item("hard-json",
         '输出一行 JSON，包含 name（字符串）、version（字符串）、count（整数 3），'
         "不要其他键",
         "python3 -c \"import json;d=json.load(open('/tmp/h-json.json'));"
         "assert set(d)=={'name','version','count'} and "
         "isinstance(d['name'],str) and isinstance(d['version'],str) and "
         "d['count']==3\"",
         "/tmp/h-json.json"),
    Item("hard-exact",
         "写一行中文，恰好 20 个汉字，且包含 nixiang",
         "python3 -c \"t=open('/tmp/h-exact.txt').read().strip();"
         "assert len(t)==20 and 'nixiang' in t\"",
         "/tmp/h-exact.txt"),
]


def main() -> int:
    parser = argparse.ArgumentParser(prog="tools/real_measure.py")
    parser.add_argument("--suite", choices=("basic", "hard", "all"),
                        default="all")
    args = parser.parse_args()
    items = {"basic": BASIC, "hard": HARD, "all": BASIC + HARD}[args.suite]

    provider = DeepSeekCandidateProvider()
    judge = DeepSeekJudge()
    store = RunStore(RUNS_DIR / "measure")
    rows = measure(items, provider, judge, n=3, store=store)
    summary = summarize(rows)

    print(f"{'任务':8s} {'single':6s} {'best':5s} {'Δ分':5s} {'选中':4s} {'wall':6s}")
    for row in rows:
        print(f"{row.name:8s} {'pass' if row.single_pass else 'fail':6s} "
              f"{'pass' if row.best_pass else 'fail':5s} "
              f"{row.best_score - row.single_score:+5d} "
              f"{row.best_index:4d} {row.wall_ms:5d}ms")
    print(f"汇总 tasks={summary['tasks']} single_pass={summary['single_pass']} "
          f"best_pass={summary['best_pass']} "
          f"single_fail={summary['single_fail']} rescued={summary['rescued']} "
          f"rescue_rate={summary['rescue_rate']:.2f} "
          f"mean_delta={summary['mean_delta']:.1f} "
          f"mean_wall={summary['mean_wall_ms']:.0f}ms")

    out = RUNS_DIR / "measure"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "readings.jsonl").open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row.__dict__, ensure_ascii=False) + "\n")
    print(f"读数 → {out / 'readings.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())