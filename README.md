# harness-lab

一个可在本地跑的实验台，用来回答一个问题：**把历史轨迹沉淀下来、按相似度复用，到底能省多少成本、代价是什么质量损失。**

答案不是靠讨论得出的，是这个库里的代码在公开数据集上跑出来的。核心结论：**降本来自「不调用」，不来自「调得更好」** —— 检索增强（few-shot）反而涨成本，查表复用才降本，但只在「输出空间可枚举」的任务上成立。

---

## 30 秒跑起来（macOS）

前提：Python **3.10+**。macOS 自带的 `python3` 往往是 3.9，先确认：

```bash
python3 -V
# 低于 3.10 就装一个：brew install python@3.12
```

然后：

```bash
cd harness-lab
python3 -m mvp
```

**不需要 `pip install` 任何东西** —— 核心包只用标准库。这一步会跑完三条路径的对照实验，把产物写进 `runs/`。

想看测试：

```bash
python3 -m unittest mvp.test_mvp mvp.test_integration mvp.test_promotion mvp.test_evaluator mvp.test_router mvp.test_backward mvp.test_budget mvp.test_miner mvp.test_scenarios mvp.test_subgoal mvp.test_llm_deepseek -v
```

90 个测试，同样零依赖。

---

## 需要分析工具时

`tools/` 下的真实数据复现用到 numpy / pandas / pyarrow / scikit-learn：

```bash
./bootstrap.sh
```

它会建 `.venv`、装依赖、跑测试、跑 demo。只有核心 demo 需求时可 `./bootstrap.sh --core` 跳过依赖安装。

之后用 venv：

```bash
source .venv/bin/activate
make real-check     # 在 Banking77 / GSM8K 上复现查表实验
make cluster        # 聚类检验演示（验「有没有任务分布」）
```

---

## 入口

| 命令 | 做什么 | 要依赖吗 |
|---|---|---|
| `python3 -m mvp` | 三路径对照：纯模型 / 检索增强 / 指纹命中 | 否 |
| `python3 -m mvp.demo_real_task --fresh` | 一个真实任务（调研型）走沉淀管道，演示置信分层 | 否 |
| `python3 -m mvp.demo_dual_loop` | 双环三轮：探索 → 复核 → 自动晋升 → 命中复用（含时效核验） | 否 |
| `python3 -m mvp.evaluator` | 留出集评估：经验与索引只用训练段，按 §6.4 判红 | 否 |
| `python3 -m mvp.route_and_sentry --demo` | 路由决策 + 哨兵巡检（演示目录里埋了四类问题） | 否 |
| `python3 -m mvp.miner --demo` | 从负证据挖候选短路规则（support / counter） | 否 |
| `python3 -m mvp.budget --demo` | 预算切分与守恒（禁止透支、未用归还） | 否 |
| `python3 tools/real_check.py --dataset all` | 真实数据集上的覆盖率-准确率-降本三角 | 是 |
| `python3 tools/cluster_check.py --demo` | 聚类检验，判定任务有无分布 | 否（demo）/ 是（真实数据） |

`make help` 有同样的清单。

**接真实模型**（把上面的仿真换成 DeepSeek 真实调用）：

```bash
export DEEPSEEK_API_KEY=sk-...
python3 tools/real_llm_check.py --n 20
```

成本取 API 返回的 `usage`，成功率仍由同一套验证器判定。适配器在 `mvp/llm_deepseek.py`，只用标准库；缺 key 时以退出码 2 报错。

---

## 目录

```
mvp/                 核心实验（纯标准库）
  contracts.py       数据结构：Trace / Step / Verdict / Goal
  tasks.py           报销判定域 + 真值规则引擎（合成任务）
  rules.py           规则库：7 条规则变成 AND-OR 条件树
  backward.py        逆向推理：三态求值 + 短路 + 确定否定
  llm.py             模型接口 + 仿真实现
  llm_deepseek.py    DeepSeek 真实 API 适配器
  verifier.py        判定型验证器
  store.py           经验库 + 记录器
  persistence.py     四类 append-only 产物（含 usage）
  snapshot.py        快照实体（验证器版本 + 经验 id + 时效）
  promotion.py       自动晋升（按复用记录，不再人工拍置信度）
  evaluator.py       留出集评估 + §6.4 判红门
  router.py          只读聚合统计的路由决策
  sentry.py          渐进失效探针（只报警，不改数据）
  runner.py          三路径对照 + 敏感性分析
  paths.py           路径解析（不依赖 cwd）
  demo_real_task.py  调研任务沉淀演示
  demo_dual_loop.py  双环三轮演示（探索 → 复核 → 晋升 → 复用）
  route_and_sentry.py 路由与哨兵演示
  test_*.py          90 个测试（单元 / 集成 / 晋升 / 评估 / 路由 / 逆向 / 契约场景）

tools/
  real_check.py      真实数据集检验（Banking77 / GSM8K）
  real_c_check.py    C 路径真实化（真实答案索引 + 真实模型补未命中）
  real_llm_check.py  真实 API 的 A/B 对照
  cluster_check.py   H1 聚类检验

data/                700KB 公开数据集，已内置，可离线
docs/                设计文档、契约、基线声明、实验报告
runs/                运行产物（自动生成，已 gitignore）
```

---

## 产物长什么样

每次 `python3 -m mvp` 生成一个时间戳目录：

```
runs/20261117-134751/
  traces.jsonl      执行轨迹：双版本号 + 离散判断点 + 步级成本
  negative.jsonl    负证据：失败路径 + 输入指纹（下次遇到直接判死路）
  experience.jsonl  经验条目：(trace_id, vid, verdict)
```

三个文件都是 append-only。集成测试会逐字节验证"重跑不改历史行"。

其中 `negative.jsonl` 的 `input_hashes` 是关键字段 —— 没有它，负证据只是一堆无法利用的废记录。

---

## 三个真实数字（Banking77，10,003 条 / 77 类）

| 阈值 τ | 覆盖率 | 命中准确率 | 降本 | 质量变化 |
|---|---|---|---|---|
| 0.80 | 9.2% | 96.2% | −9% | +0.3pp |
| 0.70 | 22.6% | 92.6% | −22% | −0.1pp |
| 0.60 | 41.5% | 88.1% | −41% | −2.0pp |

对照 GSM8K（输出开放）：同样 20% 覆盖率下，命中准确率 **2.8%**，比 Banking77 的 92.6% 差 33 倍。**这条线划出了适用边界：输出空间可枚举才成立。**

冷启动：每 100 条历史换约 1.6pp 覆盖率，要拿到 40% 覆盖率需要约 1200 条同类记录。

一条能自己算上限的公式（`a` 命中准确率、`p` 模型准确率、`Δ` 可接受质量损失）：

```
覆盖率上限  c ≤ Δ / (p − a)
```

推论：**模型越强，查表越不划算。**

---

## 环境变量

| 变量 | 默认 | 用途 |
|---|---|---|
| `HARNESS_LAB_HOME` | 项目根 | 覆盖项目根（一般不用） |
| `HARNESS_LAB_RUNS` | `<root>/runs` | 产物目录，可指到外部磁盘 |

路径全部经 `mvp/paths.py` 解析，**不依赖当前工作目录** —— 从任何位置调用结果一致。

---

## 怎么用在你自己的任务上

三条命令，一天内能出结论。

**第一步：把任务描述攒成 jsonl。** 从最近的工作记录里取 50 条，一行一条：

```jsonl
{"id": "t1", "text": "审查采购合同的付款条款是否与付款计划一致", "domain": "合同审查"}
{"id": "t2", "text": "核对报销单住宿标准是否超出城市上限", "domain": "差旅报销"}
```

`text` 写"这件事要做什么"，不要写结论。`domain` 可留空，填了会额外算簇纯度。

**第二步：跑聚类。**

```bash
python3 tools/cluster_check.py my_tasks.jsonl --threshold 0.24
```

**第三步：按判据决策。**

| 结果 | 结论 |
|---|---|
| 5-8 个簇，每簇 ≥5 条 | 有分布，这套架构成立，继续 |
| 簇数很多、每簇 1-2 条 | 没有分布，**换单任务高质量执行思路，不要做这套** |
| 全部并成 1-2 个大簇 | 阈值偏低或任务描述太笼统，调 `--threshold` 重跑 |

这一步过不了，后面的 MVP、逆向链、双环调度全都不必做。这是唯一能决定方向的一步，也是唯一不能靠讨论替代的一步。

---

## 已知限界

这些不是待办，是这套代码**不能**回答的问题，写在这里防止误读：

1. **`llm.py` 是仿真模型**，准确率是设定值而非实测。换真实 API 后检索增强路径的数字可能反转。
2. **合成任务的重复率是构造的**（约 40%）。真实业务的重复率必须自己实测。
3. **指纹命中的 100% 成功率有真值后门** —— 索引里存的就是答案。降本幅度可信，质量结论不可信。
4. **成本只算 token**，没算延迟、失败人工介入、SLA 罚金。
5. **H1（你的任务里有没有分布）未验证。** 拿 50 个真实任务跑 `cluster_check.py` 才能回答，这是唯一决定这套东西该不该继续的一步。
