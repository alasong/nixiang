"""无偏采样：保留一部分流量纯随机进探索，防止「越成功样本越偏」。

问题在 R4（成功锁死）：收敛命中率越高，进探索的任务越少；剩下的探索样本
全是「没命中」的，和到达流量不是同一分布。系统越成功，探索样本越偏，
对没见过的族估计越错。

做法：按任务 id 的哈希抽固定比例强制走探索，**不经任何路由筛选**。抽样是
确定性的（id + salt），同一任务集重跑抽到同一批，比例可测。

度量：探索样本与全量流量在族标签上的分布差，用总变差距离（TV）：
    TV = 0.5 * Σ |p_explore(k) − p_all(k)|
0 表示同分布，1 表示完全不重叠。设计里「分布偏移超阈值 → 调整路由」靠它。
"""
from __future__ import annotations

import hashlib

DEFAULT_RATE = 0.05     # 5% 强制探索：比例是拍的，按族数/流量标定
SALT = "unbiased-v1"


def selected(task_id: str, rate: float = DEFAULT_RATE,
             salt: str = SALT) -> bool:
    """确定性抽样：同一 id 同一 salt 永远同一结论。"""
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    digest = hashlib.sha256(f"{salt}:{task_id}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 1_000_000 < int(rate * 1_000_000)


def assign(task_id: str, indexed: bool, rate: float = DEFAULT_RATE,
           salt: str = SALT) -> str:
    """路由结论：被抽中的走探索（哪怕索引里有），否则命中走收敛，没命中走探索。"""
    if selected(task_id, rate, salt):
        return "explore"
    return "converge" if indexed else "explore"


def explore_share(tasks, index: set[str], rate: float = DEFAULT_RATE) -> dict:
    """按策略分派后，收敛/探索各多少条。"""
    converge = sum(1 for t in tasks
                   if assign(t.id, t.id in index, rate) == "converge")
    return {"converge": converge, "explore": len(tasks) - converge,
            "rate": rate, "total": len(tasks)}


def distribution(items, key=None) -> dict[str, float]:
    """把一组任务摊成族标签上的比例分布。"""
    if key is None:
        key = lambda t: t.scene_tag() if hasattr(t, "scene_tag") else str(t)
    counts: dict[str, int] = {}
    for item in items:
        bucket = key(item)
        counts[bucket] = counts.get(bucket, 0) + 1
    total = sum(counts.values())
    return {k: v / total for k, v in counts.items()} if total else {}


def tv_distance(p: dict[str, float], q: dict[str, float]) -> float:
    """总变差距离。缺的桶按 0 算——分布不同时不能只比交集。"""
    buckets = set(p) | set(q)
    return 0.5 * sum(abs(p.get(b, 0.0) - q.get(b, 0.0)) for b in buckets)


def bias(tasks, index: set[str], rate: float = DEFAULT_RATE, key=None) -> dict:
    """探索样本相对全量流量的偏倚。tv 越大，探索越不像真实流量。"""
    explore = [t for t in tasks if assign(t.id, t.id in index, rate) == "explore"]
    all_dist = distribution(tasks, key)
    explore_dist = distribution(explore, key)
    return {
        "tv": tv_distance(explore_dist, all_dist),
        "explore_rate": len(explore) / len(tasks) if tasks else 0.0,
        "explore": explore_dist,
        "all": all_dist,
    }


def demo() -> None:
    """索引覆盖与族相关时，抽样率如何把探索样本拉回真实分布。"""
    from ..core.tasks import build_tasks

    tasks = build_tasks(200)
    # 模拟「收敛只覆盖了容易的那部分」：索引里只有高金额任务
    index = {t.id for t in tasks if t.amount > 2000}

    print("无偏采样（200 条任务；索引只覆盖 amount>2000，刻意和族相关）")
    print("=" * 74)
    print(f"{'抽样率':>6}{'探索条数':>8}{'探索占比':>8}{'TV 距离':>9}"
          f"{'覆盖族数':>8}")
    for rate in (0.0, 0.05, 0.2, 0.5):
        result = bias(tasks, index, rate)
        print(f"{rate:>6.2f}{int(result['explore_rate'] * len(tasks)):>8}"
              f"{result['explore_rate'] * 100:>7.1f}%{result['tv']:>9.3f}"
              f"{len(result['explore']):>8}")
    print("\nTV 随抽样率下降：不加抽样时探索样本全是被索引漏掉的高金额族之外的"
          "任务，\n与到达流量不是同一分布；强制随机 5% 就能把大多数族重新拉进样本。")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="无偏采样")
    ap.add_argument("--demo", action="store_true", help="抽样率与分布偏移对照")
    args = ap.parse_args()
    if args.demo:
        demo()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()