"""C 路径真实化：真实历史答案做索引，未命中才调真实模型。

背景：`mvp/runner.py` 的 C 路径索引里存的是真值（后门），命中必然正确，
所以「质量」结论不可信（README 已知限界 3）。这里换成真实设置：

  索引  = Banking77 历史段的真实标签（等价于「当年验证通过、被沉淀下来的答案」）
  查询  = 测试段新任务：1NN 相似度 ≥ τ 则命中，直接复用历史答案，零模型调用
  未命中 = 交给真实 DeepSeek 模型，成本按 API 返回的 usage 计

每个 τ 都在同一批样本上算三种读数：
  覆盖率 / 命中准确率（全量计算，不花钱）
  混合成本 = (1−c)·模型成本 + c·10 tokens
  混合准确率 = 命中样本(用历史答案) 与 未命中样本(用模型答案) 的加权

用法：
    export DEEPSEEK_API_KEY=sk-...
    .venv/bin/python tools/real_c_check.py --n 80 --tau 0.5,0.6,0.7
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mvp.core.llm_deepseek import DeepSeekLLM          # noqa: E402
from real_check import load_banking77, topk, vectorize  # noqa: E402  复用同一套检索

HIT_COST = 10      # 命中成本：一次哈希/查表，沿用仓库口径
NAME_RE = re.compile(r"^\s+'(\d+)':\s*([A-Za-z_0-9?]+)\s*$", re.M)


def load_names(path: Path) -> list[str]:
    """从数据集 README 解析 77 个类别名。问号等非标识符字符也要认。"""
    names = {int(i): n for i, n in NAME_RE.findall(path.read_text())}
    missing = sorted(set(range(77)) - set(names))
    if missing:
        print(f"警告：类别名缺 {len(missing)} 个 {missing}，缺的用 class_<id> 占位")
    return [names.get(i, f"class_{i}") for i in range(77)]


def build_prompt(names: list[str], text: str) -> list[dict[str, str]]:
    catalog = "\n".join(f"{i} {n}" for i, n in enumerate(names))
    return [
        {"role": "system",
         "content": "你是客服意图分类器。可选类别如下（编号 名称）：\n"
                    f"{catalog}\n只输出一个类别名，不要解释。"},
        {"role": "user", "content": text},
    ]


def predict(client: DeepSeekLLM, names: list[str], text: str) -> tuple[int, int]:
    content, pt, ct = client.chat(build_prompt(names, text), max_tokens=16)
    first = (content.strip().splitlines() or [""])[0].strip().strip("`'\" ")
    index = {n.lower(): i for i, n in enumerate(names)}
    hit = index.get(first.lower())
    if hit is None:                       # 兜底：输出里嵌了某个类别名
        cands = [i for n, i in index.items() if n in first.lower()]
        hit = max(cands) if cands else -1
    return hit, pt + ct


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=80, help="调用真实模型的样本数")
    ap.add_argument("--tau", default="0.5,0.6,0.7", help="阈值列表，逗号分隔")
    ap.add_argument("--names", default=str(ROOT / "data" / "b77_readme.md"),
                    help="Banking77 README（含类别名）")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--split", type=float, default=0.6, help="索引占比")
    args = ap.parse_args()

    client = DeepSeekLLM()
    if not client.api_key:
        print("缺少 DEEPSEEK_API_KEY：\n    export DEEPSEEK_API_KEY=sk-...", file=sys.stderr)
        raise SystemExit(2)

    from mvp.core.datasets import b77_readme

    names_path = Path(args.names) if args.names else b77_readme()
    if not names_path.exists():
        print(f"缺少类别名文件：{names_path}", file=sys.stderr)
        raise SystemExit(2)

    taus = [float(x) for x in args.tau.split(",")]
    names = load_names(names_path)
    texts, labels = load_banking77()

    rng = np.random.default_rng(args.seed)
    perm = rng.permutation(len(texts))
    cut = int(len(perm) * args.split)
    idx_set, qry_set = perm[:cut], perm[cut:]
    train_texts = [texts[i] for i in idx_set]
    query_texts = [texts[i] for i in qry_set]
    y_idx, y_q = labels[idx_set], labels[qry_set]

    print(f"索引 {len(idx_set)} / 查询 {len(qry_set)}   类别 {len(names)}")
    X, Q = vectorize(train_texts, query_texts)
    nn, sim = topk(X, Q, k=1)
    sim1, nn_label = sim[:, 0], y_idx[nn[:, 0]]

    pick = rng.choice(len(qry_set), size=min(args.n, len(qry_set)), replace=False)
    print(f"真实模型调用 {len(pick)} 条（模型 {client.model}）…")
    pred = np.full(len(qry_set), -1, dtype=int)
    cost = np.zeros(len(qry_set), dtype=int)
    for i in pick:
        pred[i], cost[i] = predict(client, names, query_texts[i])

    model_ok = pred[pick] == y_q[pick]
    model_cost = cost[pick]
    print(f"模型在样本上的准确率 {model_ok.mean()*100:.1f}%   "
          f"平均成本 {model_cost.mean():.0f} tokens/条   "
          f"解析失败 {int((pred[pick] < 0).sum())} 条")

    print(f"\n{'τ':>5}{'覆盖率':>8}{'命中准确':>9}{'混合准确':>9}"
          f"{'纯模型成本':>10}{'混合成本':>9}{'成本变化':>9}{'质量变化':>9}")
    for tau in taus:
        hit = sim1 >= tau
        coverage = float(hit.mean())
        a = float((nn_label[hit] == y_q[hit]).mean()) if hit.any() else float("nan")

        h = hit[pick]
        reused = nn_label[pick][h] == y_q[pick][h]
        blended_acc = float((reused.sum() + (~h & model_ok).sum()) / len(pick))
        blended_cost = float(((h * HIT_COST) + (~h * model_cost)).mean())
        cost_delta = (blended_cost - model_cost.mean()) / model_cost.mean() * 100
        acc_delta = (blended_acc - model_ok.mean()) * 100
        print(f"{tau:>5.2f}{coverage*100:>7.1f}%{a*100:>8.1f}%{blended_acc*100:>8.1f}%"
              f"{model_cost.mean():>10.0f}{blended_cost:>9.0f}"
              f"{cost_delta:>8.1f}%{acc_delta:>+8.1f}pp")

    print(f"\n注：覆盖率/命中准确率用全量 {len(qry_set)} 条；成本与混合准确率用 {len(pick)} 条样本，"
          f"标准误约 ±{1/np.sqrt(len(pick))*100:.0f}pp")
    print(f"命中成本按 {HIT_COST} tokens 计；模型成本来自 API usage，不含重试与延迟")
    print(f"调用 {client.calls} 次   平均延迟 {client.latency_ms/max(client.calls,1):.0f} ms")


if __name__ == "__main__":
    main()