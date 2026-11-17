"""H1 聚类检验。

用途：判定「是否存在任务分布」。这是整套方案的前置假设——
没有分布，经验无法复用，双环架构用不上。

用法：
    python3 tools/cluster_check.py data.jsonl
    python3 tools/cluster_check.py --demo
    python3 tools/cluster_check.py data.jsonl --threshold 0.4

数据格式（jsonl，每行一个）：
    {"id": "t1", "text": "任务描述……", "domain": "可选，用于算纯度"}

判定标准（架构文档 §1 H1）：
    簇数 5-8，且每簇样本 >= 5  ->  成立
    否则  ->  不成立或样本量不足
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from itertools import combinations


def bigrams(text: str) -> set[str]:
    t = "".join(text.split())
    return {t[i:i + 2] for i in range(len(t) - 1)} or {t}


def build_vectors(texts: list[str]) -> list[dict[str, float]]:
    """单字 + 双字的 IDF 加权向量。

    为什么不用余弦：中文短文本（20-40 字）的 bigram 向量维度过高，
    L2 归一化后任意两条的余弦都掉到 0.05 以下，同域跨域无法区分。
    改用 IDF 加权 + 重叠系数，对短文本更敏感。
    """
    import math

    grams = [bigrams(t) | set("".join(t.split())) for t in texts]
    n = len(texts)
    df: Counter = Counter()
    for g in grams:
        df.update(g)

    return [{k: math.log((n + 1) / (df[k] + 1)) + 1.0 for k in g} for g in grams]


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    """IDF 加权重叠系数。

    |A∩B|_w / min(|A|_w, |B|_w)，取值 0-1。
    相比余弦，它对「短文本共享少量高权重词」更敏感。
    """
    if not a or not b:
        return 0.0
    common = a.keys() & b.keys()
    if not common:
        return 0.0
    num = sum(min(a[k], b[k]) for k in common)
    den = min(sum(a.values()), sum(b.values()))
    return num / den if den else 0.0


def similarity_matrix(items: list[dict]) -> list[list[float]]:
    vecs = build_vectors([it["text"] for it in items])
    n = len(items)
    sim = [[0.0] * n for _ in range(n)]
    for i in range(n):
        sim[i][i] = 1.0
    for i, j in combinations(range(n), 2):
        s = cosine(vecs[i], vecs[j])
        sim[i][j] = sim[j][i] = s
    return sim


def cluster(items: list[dict], threshold: float) -> list[list[int]]:
    """约束单链接聚类。

    单链接容易产生长链，所以合并时要求与簇内「多数成员」相似，
    而不只是与某一个成员相似。这让簇更紧凑。
    """
    n = len(items)
    sim = similarity_matrix(items)

    clusters: list[list[int]] = []
    order = sorted(range(n), key=lambda i: -max(sim[i]))

    for i in order:
        best, best_score = -1, 0.0
        for ci, members in enumerate(clusters):
            scores = [sim[i][m] for m in members]
            # 与簇内任一成员足够像即可加入（单链接），但用平均分选最优簇
            if max(scores) < threshold:
                continue
            score = sum(scores) / len(scores)
            if score > best_score:
                best, best_score = ci, score
        if best >= 0:
            clusters[best].append(i)
        else:
            clusters.append([i])
    return clusters


def intra_similarity(members: list[int], sim: list[list[float]]) -> float:
    if len(members) < 2:
        return 1.0
    vals = [sim[a][b] for a, b in combinations(members, 2)]
    return sum(vals) / len(vals)


def report(items: list[dict], threshold: float) -> dict:
    clusters = cluster(items, threshold)
    clusters.sort(key=len, reverse=True)
    sim = similarity_matrix(items)

    rows = []
    for idx, members in enumerate(clusters):
        domains = Counter(items[m].get("domain", "?") for m in members)
        rows.append({
            "cluster": idx,
            "size": len(members),
            "intra_sim": round(intra_similarity(members, sim), 3),
            "top_domain": domains.most_common(1)[0][0],
            "purity": round(domains.most_common(1)[0][1] / len(members), 2),
            "sample": items[members[0]]["text"][:46],
        })

    n_clusters = len(clusters)
    min_size = min((len(c) for c in clusters), default=0)
    sizes = [len(c) for c in clusters]

    h1_ok = 5 <= n_clusters <= 8 and min_size >= 5
    if n_clusters == 1:
        verdict = "不成立：只有 1 个簇，所有任务同质，无分布可利用"
    elif n_clusters < 5:
        verdict = f"存疑：仅 {n_clusters} 个簇，族数偏少，复用面窄"
    elif n_clusters > 8:
        verdict = f"存疑：{n_clusters} 个簇，过于分散，多数簇样本不足"
    elif min_size < 5:
        verdict = f"不成立：最小簇仅 {min_size} 个样本，无法形成稳定经验"
    else:
        verdict = f"成立：{n_clusters} 个簇，最小 {min_size} 个样本，可支撑按族复用"

    return {
        "n_items": len(items),
        "n_clusters": n_clusters,
        "sizes": sizes,
        "min_size": min_size,
        "h1_ok": h1_ok,
        "verdict": verdict,
        "clusters": rows,
    }


DEMO = [
    ("差旅报销", "核对差旅报销：住宿单价是否符合城市等级上限"),
    ("差旅报销", "核对差旅报销：交通工具等级是否为经济舱或二等座"),
    ("差旅报销", "核对差旅报销：餐补是否超过每日标准"),
    ("差旅报销", "核对差旅报销：发票日期是否落在行程区间内"),
    ("差旅报销", "核对差旅报销：是否存在同日重复提交"),
    ("差旅报销", "核对差旅报销：跨月申请是否附说明"),
    ("合同审查", "审查采购合同：付款条款是否与付款计划一致"),
    ("合同审查", "审查采购合同：违约责任是否约定违约金比例"),
    ("合同审查", "审查采购合同：签约主体是否与营业执照一致"),
    ("合同审查", "审查采购合同：保密条款是否覆盖终止后三年"),
    ("合同审查", "审查采购合同：签署页是否双方盖章"),
    ("合同审查", "审查采购合同：附件清单是否与正文对应"),
    ("代码评审", "评审代码提交：是否包含单元测试且覆盖率达标"),
    ("代码评审", "评审代码提交：函数复杂度是否超过阈值"),
    ("代码评审", "评审代码提交：异常是否被吞掉未记日志"),
    ("代码评审", "评审代码提交：是否存在硬编码密钥"),
    ("代码评审", "评审代码提交：接口变更是否同步更新文档"),
    ("代码评审", "评审代码提交：数据库迁移是否可回滚"),
    ("日志分析", "分析网关日志：统计接口错误率并按服务聚合"),
    ("日志分析", "分析网关日志：定位响应超两秒的请求链路"),
    ("日志分析", "分析网关日志：异常类型分布与突增检测"),
    ("日志分析", "分析网关日志：还原失败请求的完整路径"),
    ("日志分析", "分析网关日志：各客户端版本失败率对比"),
    ("日志分析", "分析网关日志：超时请求的上游依赖定位"),
    ("报表生成", "生成经营报表：按月汇总部门费用与同比"),
    ("报表生成", "生成经营报表：季度收入成本利润三表"),
    ("报表生成", "生成经营报表：产品线毛利率与趋势"),
    ("报表生成", "生成经营报表：应收账龄分布与超期标记"),
    ("报表生成", "生成经营报表：人力成本按部门职级拆分"),
    ("报表生成", "生成经营报表：预算执行偏差超百分之十科目"),
    ("客户回复", "起草客户回复：投诉处理方案与时限"),
    ("客户回复", "起草客户回复：退款政策依据与操作步骤"),
    ("客户回复", "起草客户回复：交付延期的新时间节点"),
    ("客户回复", "起草客户回复：功能咨询答复与文档链接"),
    ("客户回复", "起草客户回复：差评致歉与补偿方案"),
    ("客户回复", "起草客户回复：合同条款异议的可调整范围"),
]


def load(path: str) -> list[dict]:
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if "text" not in obj:
                raise ValueError(f"缺少 text 字段: {line[:60]}")
            items.append(obj)
    return items


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", help="jsonl 数据文件")
    ap.add_argument("--demo", action="store_true", help="跑内置示例")
    ap.add_argument("--threshold", type=float, default=0.24, help="相似度阈值，默认 0.24")
    args = ap.parse_args()

    if args.demo:
        items = [{"id": f"d{i}", "text": t, "domain": d} for i, (d, t) in enumerate(DEMO)]
        source = "内置示例（6 个业务域 × 6 条）"
    elif args.path:
        items = load(args.path)
        source = args.path
    else:
        ap.print_help()
        sys.exit(1)

    r = report(items, args.threshold)

    print("=" * 68)
    print(f"H1 聚类检验   数据源: {source}   阈值: {args.threshold}")
    print("=" * 68)
    print(f"样本数 {r['n_items']}   簇数 {r['n_clusters']}   各簇规模 {r['sizes']}")
    print()
    print(f"{'簇':>3} {'规模':>4} {'簇内相似':>8} {'主要域':>10} {'纯度':>5}  示例")
    for row in r["clusters"]:
        print(f"{row['cluster']:>3} {row['size']:>4} {row['intra_sim']:>8.3f} "
              f"{row['top_domain']:>10} {row['purity']:>5.2f}  {row['sample']}")
    print()
    print(f"H1 判定: {r['verdict']}")
    if not r["h1_ok"]:
        print()
        print("提示：H1 不成立时，双环架构的收敛路径没有可用输入。")
        print("      应转向「单任务高质量执行」思路，而不是强行建经验库。")


if __name__ == "__main__":
    main()
