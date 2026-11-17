"""真实 DeepSeek API 下的 A/B 对照。

指标全部来自 API 返回的真实用量：
- 成功率：验证器按「集合完全相等」判定
- 平均成本：usage.prompt_tokens + usage.completion_tokens
- 解析失败 / 平均延迟：适配器自己计数

同一批任务再用仿真模型跑一遍作参照，看「设定值」和「真实测量」差多少。

用法：
    export DEEPSEEK_API_KEY=sk-...
    python3 tools/real_llm_check.py --n 6
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mvp.core.contracts import Status
from mvp.core.llm import SimulatedLLM
from mvp.core.llm_deepseek import DeepSeekLLM
from mvp.loop.runner import build_experience, compare, run_path
from mvp.core.tasks import build_tasks, ground_truth, structural_key
from mvp.core.verifier import SetMatchVerifier


def _report(result, client) -> None:
    s = result.stats
    calls = client.calls or 1
    print(f"\n{result.name}")
    print(f"  成功率 {s['success_rate']*100:5.1f}%   平均成本 {s['avg_cost']:7.0f} tokens"
          f"   平均尝试 {s['avg_attempts']:.2f} 次")
    print(f"  调用 {client.calls} 次   解析失败 {client.parse_errors} 次"
          f"   平均延迟 {client.latency_ms / calls:.0f} ms")
    if client.unknown_codes:
        print(f"  模型输出了无效编号（已丢弃）: {sorted(set(client.unknown_codes))}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=6, help="任务数，默认 6（每次调用都花真钱）")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    client_a = DeepSeekLLM()
    if not client_a.api_key:
        print("缺少 DEEPSEEK_API_KEY：\n    export DEEPSEEK_API_KEY=sk-...", file=sys.stderr)
        raise SystemExit(2)

    tasks = build_tasks(n=args.n, seed=args.seed)
    truth = {t.id: ground_truth(t) for t in tasks}
    verifier = SetMatchVerifier(truth)
    store = build_experience()
    client_b = DeepSeekLLM()

    print(f"模型 {client_a.model}   任务 {len(tasks)} 条   temperature={client_a.temperature}")
    print("=" * 72)

    base = run_path("A 真实模型（无经验）", tasks, truth, verifier, store,
                    use_shots=False, llm=client_a)
    enh = run_path("B 真实模型 + 2 条经验", tasks, truth, verifier, store,
                   use_shots=True, llm=client_b)
    for result, client in ((base, client_a), (enh, client_b)):
        _report(result, client)

    cmp = compare(base, enh)
    print("\n" + "-" * 72)
    print(f"B vs A   成本 {cmp['cost_change_pct']:+.2f}%   "
          f"成功率 {cmp['success_change_pp']:+.2f}pp")

    sim = run_path("仿真基线", tasks, truth, verifier, store, use_shots=False,
                   llm=SimulatedLLM(truth, seed=7))
    print(f"\n仿真基线（同一批任务）  成功率 {sim.stats['success_rate']*100:.1f}%   "
          f"平均成本 {sim.stats['avg_cost']:.0f} tokens")

    keys = {structural_key(t) for t in tasks}
    dup_rate = 1 - len(keys) / len(tasks)
    print(f"结构指纹重复率 {dup_rate*100:.0f}%   —— 这部分任务走 C 路径零调用，"
          f"理论降本同比例（构造值，非实测）")

    bad = [t for t in base.traces if t.outcome != Status.PASS]
    if bad:
        print("\n未通过的任务（前 3 条）")
        for t in bad[:3]:
            last = t.steps[-1] if t.steps else None
            got = sorted(last.output) if last else []
            print(f"  {t.task_id}  应有 {sorted(truth[t.task_id])}  模型给 {got}")


if __name__ == "__main__":
    main()