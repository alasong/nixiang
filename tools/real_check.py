"""真实数据下的「查表复用」可行性检验。

回答三个问题：
  Q1 表面重复率有多高？（语义近邻是否经常出现）
  Q2 出现近邻时，答案能否复用？命中准确率 vs 模型准确率
  Q3 需要多少条历史数据才有效？（冷启动曲线）

方法：索引集 → 查询集，TF-IDF 余弦取 top-k 近邻，阈值 τ 扫描。
指标：覆盖率 = 相似度 ≥ τ 的查询占比；命中准确率 = 命中样本中复用答案正确的比例。

推导出核心约束（见 REPORT）：
    若 a < p，则覆盖率上限 c ≤ Δacc / (p − a)
    其中 a=命中准确率, p=模型准确率, Δacc=允许的质量损失
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

DATA = Path(__file__).resolve().parent.parent / "data"


# ---------------------------------------------------------------- 数据加载

def load_banking77() -> tuple[list[str], np.ndarray]:
    df = pd.read_parquet(DATA / "b77_train.parquet")
    return df["text"].tolist(), df["label"].to_numpy()


def b77_names() -> dict[int, str]:
    """从 README 的 dataset_info 里解析 77 个意图名。"""
    txt = (DATA / "b77_readme.md").read_text()
    out = {}
    for m in re.finditer(r"^\s+'(\d+)':\s*([A-Za-z_0-9]+)\s*$", txt, re.M):
        out[int(m.group(1))] = m.group(2)
    return out


def coarse_map(names: dict[int, str]) -> dict[int, str]:
    """把细粒度意图映射到「业务动作」家族。

    判据：合并后同族内走同一个处理流程。这是真实审批场景关心的粒度——
    财务不会因为「转账待处理」和「充值待处理」走两套流程，但会因为它俩
    和「卡支付待处理」走同一套。
    """
    rules = [
        ("pending", lambda n: n.startswith("pending_")),
        ("card", lambda n: "card" in n and not n.startswith("pending_")),
        ("transfer", lambda n: "transfer" in n and not n.startswith("pending_")),
        ("topup", lambda n: "top_up" in n or "topup" in n),
        ("refund", lambda n: "refund" in n.lower()),
        ("cash", lambda n: "cash" in n or "atm" in n or "withdraw" in n),
        ("fee", lambda n: "fee" in n or "charge" in n),
        ("verify", lambda n: "verify" in n or "identity" in n or "passcode" in n
                            or "pin" in n or "security" in n),
    ]
    out = {}
    for k, n in names.items():
        out[k] = next((tag for tag, f in rules if f(n)), "other")
    return out


def load_gsm8k() -> tuple[list[str], np.ndarray]:
    df = pd.read_parquet(DATA / "gsm8k_test.parquet")

    def final_num(ans: str) -> str:
        m = re.findall(r"####\s*([\-0-9\.,]+)", ans)
        if m:
            return m[-1].replace(",", "").strip()
        m = re.findall(r"([\-0-9][0-9,]*\.?[0-9]*)", ans)
        return m[-1].replace(",", "").strip() if m else ans.strip()

    return df["question"].tolist(), np.array([final_num(a) for a in df["answer"]])


# ---------------------------------------------------------------- 向量与近邻

def vectorize(train: list[str], query: list[str]):
    """词级 + 字符级 TF-IDF 拼接。字符级对短文本和拼写差异更稳。"""
    vw = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, min_df=1)
    vc = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5),
                         sublinear_tf=True, min_df=1)
    Xw = vw.fit_transform(train)
    Qw = vw.transform(query)
    Xc = vc.fit_transform(train)
    Qc = vc.transform(query)
    from scipy.sparse import hstack
    X = normalize(hstack([Xw, Xc]).astype(np.float32))
    Q = normalize(hstack([Qw, Qc]).astype(np.float32))
    return X, Q


def topk(X, Q, k: int, block: int = 512):
    """分块算余弦 top-k，避免 6000×4003 稠密矩阵炸内存。"""
    idx = np.empty((Q.shape[0], k), dtype=np.int32)
    sim = np.empty((Q.shape[0], k), dtype=np.float32)
    for s in range(0, Q.shape[0], block):
        e = min(s + block, Q.shape[0])
        S = (Q[s:e] @ X.T).toarray()
        part = np.argpartition(-S, kth=k - 1, axis=1)[:, :k]
        rows = np.arange(e - s)[:, None]
        vals = S[rows, part]
        order = np.argsort(-vals, axis=1)
        idx[s:e] = part[rows, order]
        sim[s:e] = vals[rows, order]
    return idx, sim


# ---------------------------------------------------------------- 核心评估

def evaluate(y_idx, y_q, idx, sim, taus, k_vote=5):
    """对每个阈值算覆盖率、命中准确率、投票准确率。"""
    rows = []
    nn1 = y_idx[idx[:, 0]]
    # k 近邻多数投票（只在相似度 > τ 的近邻里投）
    for tau in taus:
        hit = sim[:, 0] >= tau
        cov = hit.mean()
        acc1 = (nn1[hit] == y_q[hit]).mean() if hit.sum() else float("nan")

        # top-k 投票，过滤低于阈值的邻居
        pred = []
        valid = []
        for i in range(len(y_q)):
            nb = [(sim[i, j], y_idx[idx[i, j]]) for j in range(idx.shape[1])
                  if sim[i, j] >= tau]
            if not nb:
                pred.append(None)
                valid.append(False)
                continue
            vals = [v for _, v in nb[:k_vote]]
            pred.append(max(set(vals), key=vals.count))
            valid.append(True)
        pred = np.array(pred, dtype=object)
        valid = np.array(valid)
        accv = (pred[valid] == y_q[valid]).mean() if valid.sum() else float("nan")

        rows.append({
            "tau": round(float(tau), 3),
            "coverage": round(float(cov), 3),
            "hit_n": int(hit.sum()),
            "acc_1nn": round(float(acc1), 4) if hit.sum() else None,
            "acc_vote": round(float(accv), 4) if valid.sum() else None,
            "acc_gap": round(float(accv) - float(acc1), 4) if valid.sum() else None,
        })
    return rows


# ---------------------------------------------------------------- 冷启动曲线

def bucket_stats(y_idx, y_q, idx, sim, edges):
    """按最近邻相似度分桶，看每档的 1NN 准确率。

    阈值累积统计会被小样本带偏（高阈值只剩几十条），分桶能看到单调性。
    """
    out = []
    s = sim[:, 0]
    nn = y_idx[idx[:, 0]]
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (s >= lo) & (s < hi)
        if m.sum() < 5:
            continue
        out.append({
            "bucket": f"[{lo:.1f},{hi:.1f})",
            "n": int(m.sum()),
            "share": round(float(m.mean()), 3),
            "acc_1nn": round(float((nn[m] == y_q[m]).mean()), 3),
        })
    return out


def cold_start(train, y_idx, query, y_q, sizes, tau_list, seed=0):
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(train))
    out = []
    for n in sizes:
        if n > len(train):
            continue
        sub = perm[:n]
        X, Qv = vectorize([train[i] for i in sub], query)
        ysub = y_idx[sub]
        idx, sim = topk(X, Qv, k=5, block=256)
        rec = {"n_index": n}
        for tau in tau_list:
            hit = sim[:, 0] >= tau
            cov = float(hit.mean())
            acc = (ysub[idx[:, 0]][hit] == y_q[hit]).mean() if hit.sum() else float("nan")
            rec[f"cov@{tau}"] = round(cov, 3)
            rec[f"acc@{tau}"] = None if np.isnan(acc) else round(float(acc), 3)
        out.append(rec)
    return out


# ---------------------------------------------------------------- 主流程

def run(name, texts, labels, split=0.6, taus=None, sizes=None, variants=None, seed=0):
    # Banking77 按 label 排序存储，顺序切分会切出「训练集没有的类」。必须打乱。
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(texts))
    texts = [texts[i] for i in perm]
    labels = labels[perm]
    if variants:
        variants = {k: v[perm] for k, v in variants.items()}

    n = int(len(texts) * split)
    train, query = texts[:n], texts[n:]
    y_idx, y_q = labels[:n], labels[n:]

    X, Q = vectorize(train, query)
    idx, sim = topk(X, Q, k=5)
    taus = taus or [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95]

    print("=" * 78)
    print(f"{name}   索引 {len(train)} / 查询 {len(query)}   类别数 {len(set(labels.tolist()))}")
    print("=" * 78)

    s = sim[:, 0]
    print("\n最近邻相似度分布")
    print("  " + "  ".join(f"P{q}={np.percentile(s, q):.3f}" for q in (25, 50, 75, 90, 95, 99)))

    print("\n按相似度分桶（1NN 复用准确率）")
    print(f"{'区间':>12} {'样本数':>8} {'占比':>7} {'1NN准确':>9}")
    for b in bucket_stats(y_idx, y_q, idx, sim,
                          [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.001]):
        print(f"{b['bucket']:>12} {b['n']:>8} {b['share']*100:>6.1f}% {b['acc_1nn']*100:>8.1f}%")

    print("\n阈值扫描（累积）")
    print(f"{'tau':>6} {'覆盖率':>8} {'命中数':>7} {'1NN准确':>9} {'5NN投票':>9} {'投票增益':>9}")
    rows = evaluate(y_idx, y_q, idx, sim, taus)
    for r in rows:
        a1 = f"{r['acc_1nn']*100:>8.2f}" if r["acc_1nn"] is not None else "     —"
        av = f"{r['acc_vote']*100:>8.2f}" if r["acc_vote"] is not None else "     —"
        g = f"{r['acc_gap']*100:>+8.2f}" if r["acc_gap"] is not None else "     —"
        print(f"{r['tau']:>6.2f} {r['coverage']*100:>7.1f}% {r['hit_n']:>7} {a1} {av} {g}")

    # 粒度对比：同一套近邻，只改输出空间的粗细
    if variants:
        print("\n颗粒度对比（同一套近邻，只改答案需要多精细）")
        head = f"{'粒度方案':<16}{'类别数':>7}" + "".join(
            f"{f'cov@{t}':>9}{'准确':>8}" for t in (0.4, 0.5))
        print(head)
        for vname, vlabels in variants.items():
            vi, vq = vlabels[:n], vlabels[n:]
            line = f"{vname:<16}{len(set(vlabels.tolist())):>7}"
            for t in (0.4, 0.5):
                hit = sim[:, 0] >= t
                acc = (vi[idx[:, 0]][hit] == vq[hit]).mean() if hit.sum() else float("nan")
                line += f"{hit.mean()*100:>8.1f}%{acc*100:>7.1f}%"
            print(line)

    if sizes:
        print("\n冷启动：索引规模 → 覆盖率/1NN准确率")
        print(f"{'索引量':>8}" + "".join(f"{f'τ={t}':>18}" for t in (0.4, 0.5, 0.6)))
        cs = cold_start(train, y_idx, query, y_q, sizes, [0.4, 0.5, 0.6])
        for r in cs:
            cells = []
            for t in (0.4, 0.5, 0.6):
                c, a = r[f"cov@{t}"], r[f"acc@{t}"]
                cells.append(f"{c*100:>7.1f}%/{('—' if a is None else f'{a*100:.0f}%'):>7}")
            print(f"{r['n_index']:>8}" + "".join(f"{x:>18}" for x in cells))

    return {"name": name, "n_index": len(train), "n_query": len(query),
            "rows": rows, "buckets": bucket_stats(y_idx, y_q, idx, sim,
                                                  [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.001])}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="all", choices=["all", "banking77", "gsm8k"])
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    results = []
    if a.dataset in ("all", "banking77"):
        t, y = load_banking77()
        names = b77_names()
        cmap = coarse_map(names)
        coarse = np.array([cmap.get(int(v), "other") for v in y], dtype=object)
        print("业务动作家族分布:", {k: int((coarse == k).sum()) for k in sorted(set(coarse))})
        results.append(run("Banking77（意图判定）", t, y,
                           sizes=[50, 100, 200, 400, 800, 1600, 3200],
                           variants={"细粒度(77 意图)": y, "粗粒度(业务动作)": coarse}))
    if a.dataset in ("all", "gsm8k"):
        t, y = load_gsm8k()
        results.append(run("GSM8K（数值求解，开放输出）", t, y, split=0.6,
                           sizes=[50, 100, 200, 400, 800]))

    print("\n" + "=" * 78)
    print("覆盖率上限：c ≤ Δ/(p−a)，Δ=5pp 质量容忍，p=模型准确率")
    print("=" * 78)
    print(f"{'数据集':<32}{'a(τ=0.5,5NN)':>14}{'p=0.80':>10}{'p=0.90':>10}{'p=0.95':>10}")
    for r in results:
        cand = [x for x in r["rows"] if x["acc_vote"] is not None]
        if not cand:
            continue
        pick = min(cand, key=lambda x: abs(x["tau"] - 0.5))
        line = f"{r['name'][:30]:<32}{pick['acc_vote']*100:>13.1f}%"
        for p in (0.80, 0.90, 0.95):
            line += f"{min(1.0, 0.05 / max(p - pick['acc_vote'], 1e-9))*100:>9.1f}%"
        print(line)

    if a.json:
        Path(a.json).write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(f"\n已保存 {a.json}")


if __name__ == "__main__":
    main()
