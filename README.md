# harness-lab

一个跑在本地的**双环嵌套系统实验台**：

- **内环**（快、确定）：在已知空间里用规则/指纹命中，不调模型就把事做对；
- **外环**（慢、随机）：调模型探索，记录轨迹、判对错、沉淀经验与规则，扩大已知空间。

它验证三个假设（`docs/融合双环架构设计v1.0.md` §1）：

| 编号 | 假设 | 现状 |
|---|---|---|
| H1 | 同类任务反复出现，簇内 ≥5 条 | 未验：真实业务域 0 条 |
| H2 | 有判定型验证器且覆盖足够 | 合成域成立（7 条规则，判定型）；真实域待定 |
| H3 | 复用能降本且不掉质量 | 合成域 −52.3% 成本/+8.3pp；Banking77 τ≈0.45 −72.2%/+4.0pp |

**口径要说准**：设计目标是**预算约束下的近似最优**，不追求全局最优（`docs/双引擎逆向推理设计文档.md` §1.2；
`docs/融合双环架构设计v1.0.md` §0 明确修正过 v0.1 的「任务最优」说法）。按「最优」验收，这个项目永远过不了。

核心结论一句：**降本来自「不调用」，不来自「调得更好」**——检索增强（few-shot）涨成本，规则命中才降本，且只在「输出空间可枚举」的任务上成立。

---

## 现在能做什么

| 能力 | 命令 | 本机读数 |
|---|---|---|
| 逆向推理判定（AND-OR 展开、三态、短路） | `python3 -m mvp.loop.backward --demo` | 与正向规则引擎 30/30 一致；读取 410 vs 基线 420 |
| 双环三轮（内环未命中 → 外环探索 → 内环复用） | `python3 -m mvp.demos.demo_dual_loop` | 探索 5 步 → 复核 6 步 → 复用 3 步 |
| 三路径成本对照（基线 / 检索增强 / 指纹命中） | `python3 -m mvp` | C 路径 −52.3% 成本、+8.3pp 质量 |
| 留出集评估 + 双跑差异归因 | `python3 -m mvp.loop.evaluator` | C 过门；12 条留出中 9 条差异 100% 可解释 |
| **从零学规则并切换** | `python3 -m mvp.demos.self_improve` | 冷启动 78%/1375 token → 学出 7 条后 100%/10 token |
| 规则回放验证（激活门） | `python3 -m mvp.loop.validator --demo` | 7 条 100%；坏规则误伤 36 条判红 |
| 规则挖掘（学条件→结论） | `python3 -m mvp.loop.learner --demo` | 7/7 条学出，留出零误伤零漏报 |
| 运行时监控（4 项） | `make regress` / `python3 -m mvp.loop.probe --demo` / `python3 -m mvp.loop.sampling --demo` / `make adversarial` | 回归绿、探针 14% 报差异、TV 0.885→0.542、对抗 100% |

全部零第三方依赖，只用标准库。

### 运行时主线 vs 组件 / 演示（别把 demo 当主线）

| 机制 | 现在的落点 | 说明 |
|---|---|---|
| 内环（收敛） | `runner.run_path_cached` + `mvp/task.py` | 指纹命中 / 规则判定，不调模型 |
| 外环（探索） | `runner.run_path` + `learner` | 模型执行 → 记录 → 台账 → 挖掘 |
| 双环调度（按任务路由内外环） | **只在 `demo_dual_loop.py` / `e2e.py` 里演示** | 主实验仍是 A/B/C 三路径对照，没有在线路由 |
| 逆向链作为运行路径 | **只在结构化域接通**（`mvp/task.py`、learner 回放、validator） | 主实验的 C 路径走的是指纹命中，不是逆向展开 |

这张表是「哪里真接了、哪里只是演示」的边界。下面的「已知限界」有对应条目。

---

## 看板：TASKS.md

仓库根目录的 `TASKS.md` 就是看板，一个任务一行、固定 7 列（`id / 任务 / 能力 / 验收命令 / 期望 / 状态 / 证据`）。
状态只有 `todo / doing / done / blocked`，同时最多 1 个 doing，未完成不超过 5 个。

```bash
python3 -m mvp.board            # 看板视图
python3 -m mvp.board start T2   # todo → doing
python3 -m mvp.board verify T2  # 跑验收命令，过则 done 并写证据
```

没有验收命令的活不许开工——这是这张看板唯一的硬规矩。

---

## 下一条任务（怎么把任务交给系统）

先把边界说清：**目前只接结构化任务**（差旅报销这套字段）。自由文本任务没有
对应验证器与领域规则，接进来只会把「判不了」伪装成「判完了」，所以不接。

单条任务：

```bash
python3 -m mvp.task --json '{"id":"T-1","amount":8000,"has_director_approval":false,
  "city_tier":1,"hotel_per_night":500,"transport_class":"商务舱",
  "meal_days":3,"meal_total":200,"invoice_dates":["2026-01-01","2026-01-03"],
  "trip_start":"2026-01-01","trip_end":"2026-01-02","submitted_month":"2026-01",
  "has_note":false,"same_day_dup":false}'
```

输出（每条任务的判定、成本、读取次数，以及哪些规则因为缺字段判不了）：

```
任务 T-1
  违规规则  R1, R3, R5
  判不了    无
  字段读取  14 次   查表成本 10 token 当量（未调模型）

  规则   结论          读取  缺失字段
  R1   violated     2  -
  ...
```

批量（一行一条 JSON）：

```bash
python3 -m mvp.task --file my_tasks.jsonl
```

想和真实模型对照（需要 `DEEPSEEK_API_KEY`）：

```bash
python3 -m mvp.task --json '...' --model
# 模型      R1, R3  （832 tokens）  与查表一致
```

字段：`amount` 金额、`has_director_approval` 是否总监审批、`city_tier` 1=一线、
`hotel_per_night` 住宿单价、`transport_class` 舱位、`meal_days`/`meal_total` 餐补、
`invoice_dates` 发票日期、`trip_start`/`trip_end` 行程、`submitted_month` 提交月份、
`has_note` 跨月说明、`same_day_dup` 同日重复。缺字段不算错——逆向链会返回
`unknown` 并告诉缺哪个，这正是三态语义的用处。

---

## 5 分钟上手

前提 Python **3.10+**（macOS 自带常是 3.9）：

```bash
python3 -V
```

在仓库根目录执行（以下命令同理）；主流程不需要 `pip install`，产物写进 `runs/`：

```bash
python3 -m mvp
```

跑测试与文档/状态机检：

```bash
make test        # 207 条测试，约 1 秒
make docs-check  # README 与代码是否漂移（测试数 / 入口 / make 目标）
make status      # 落地状态表引用的命令是否都存在
```

想先看「这套东西到底能干什么」，跑闭环：

```bash
python3 -m mvp.demos.self_improve
```

预期输出（合成域）：

```
阶段 1  冷启动 200 条：成功率 78.0%，平均 1375 token/条
阶段 2  从轨迹 + 台账学出 7 条规则，过激活门 7 条：['R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7']
阶段 3  切换后 200 条：成功率 100.0%，平均 10 token/条，回退模型 0 条
        同批纯模型基线：78.0%，1375 token/条
  结论：成本 -99.3%，质量 +22.0pp（合成域；成本按查表 10 token 计）
```

---

## 按目的选命令

| 你想知道 | 跑这个 | 看什么 |
|---|---|---|
| 复用到底省不省钱 | `python3 -m mvp` | 三条路径的成功率 / 平均成本 |
| 过没过 §6.4 的门 | `python3 -m mvp.loop.evaluator` | 成本降 ≥40% 且质量掉 ≤5pp 才「通过」 |
| 系统能不能自己学规则 | `python3 -m mvp.demos.self_improve` | 学出几条、切换后成本/质量 |
| 挖出的规则靠不靠谱 | `python3 -m mvp.loop.learner --demo` | 留出集误伤/漏报；学不出的会明说 |
| 验证器有没有被放松 | `make regress` / `make adversarial` | 全绿退出 0，判红退出 1 |
| 收敛有没有在做错事 | `python3 -m mvp.loop.probe --demo` | 两路答案差异率 |
| 探索样本偏不偏 | `python3 -m mvp.loop.sampling --demo` | TV 距离随抽样率下降 |
| 维护回路（巡检/晋升/淘汰/路由） | `python3 -m mvp.loop.maintenance --demo` | 告警、晋升、淘汰、路由各几条 |
| 全回路是否放行 | `python3 -m mvp.demos.e2e` | 末行「总门：通过/未通过」 |
| 交一条自己的任务 | `python3 -m mvp.task --json '...'` | 违规规则 / 判不了 / 读取次数 |
| 推进任务清单 | `python3 -m mvp.board` | 按 todo/doing/done/blocked 分列；`verify` 跑验收 |
| 看逆向链本身 | `python3 -m mvp.loop.backward --demo` | 30/30 一致、读取次数、部分信息下的确定否定 |
| 看双环怎么转 | `python3 -m mvp.demos.demo_dual_loop` | 三轮：探索 → 复核 → 命中复用 |
| 当前落地到哪一步 | `make status` + `docs/落地状态_2026-11.md` | 每条设计需求的现状与证据 |

`make help` 有同样的清单。

---

## 一次完整闭环（手动拆开跑）

```bash
# 1. 跑任务、落轨迹（带结构化 facts）
python3 -m mvp.demos.e2e

# 2. 从轨迹 + 标签台账学规则，回放验证
python3 -m mvp.loop.learner --demo

# 3. 对照「纯模型 vs 符号优先」的成本与质量
python3 -m mvp.demos.self_improve

# 4. 监控三件套
make regress
make adversarial
python3 -m mvp.loop.probe --demo
```

学规则的前提是**标签台账有内容**：`labels.jsonl` 由验收环节写（合成域是真值引擎，
真实域是人工复核/下游回调）。台账覆盖不足 50% 时 `learn_from_ledger` 直接拒绝学习，
并在报告里写明原因——宁可空手回去等标签。

---

## 接真实模型

把仿真换成 DeepSeek 真实调用（适配器 `mvp/core/llm_deepseek.py`，只用标准库）：

```bash
export DEEPSEEK_API_KEY=sk-...
python3 tools/real_llm_check.py --n 20
```

成本取 API 返回的 `usage`，成功率仍由同一套验证器判定；缺 key 以退出码 2 报错。
实测参考：`deepseek-chat` 在 Banking77 77 类上准确率 73.8%、平均 583 token/条。

---

## 需要分析工具时

`tools/` 下的真实数据复现要 numpy / pandas / pyarrow / scikit-learn:

```bash
./bootstrap.sh          # 建 .venv、装依赖、跑测试
./bootstrap.sh --core   # 只跑核心（不装依赖，最快）
```

之后用 venv：

```bash
source .venv/bin/activate
make real-check   # Banking77 / GSM8K 上复现查表实验
make cluster      # 聚类检验演示（验「有没有任务分布」）
```

---

## 接你自己的任务（三步）

**第一步：任务描述攒成 jsonl**（最近工作记录取 50 条，一行一条）：

```jsonl
{"id": "t1", "text": "审查采购合同的付款条款是否与付款计划一致", "domain": "合同审查"}
{"id": "t2", "text": "核对报销单住宿标准是否超出城市上限", "domain": "差旅报销"}
```

`text` 写「这件事要做什么」，不写结论；`domain` 可留空。

**第二步：跑聚类或直接按标签统计**

```bash
python3 tools/cluster_check.py my_tasks.jsonl --threshold 0.24
python3 tools/family_stats.py --jsonl my_tasks_with_family.jsonl   # 有族标签时优先
```

**第三步：按判据决策**

| 结果 | 结论 |
|---|---|
| 5–8 个簇，每簇 ≥5 条 | 有分布，架构成立，继续 |
| 簇数很多、每簇 1–2 条 | 没有分布，**换单任务高质量执行思路，不要做这套** |
| 全部并成 1–2 个大簇 | 阈值偏低或描述太笼统，调 `--threshold` 重跑 |

真实文本上聚类会误判：400 条 Banking77 查询里，标签口径 9 族成立，聚类口径 τ=0.24 并成 1 簇。
有标签优先用 `family_stats.py`。这一步过不了，后面的 MVP、逆向链、双环调度都不必做。

---

## 目录

```
mvp/                 核心实验（纯标准库，按职责分包）
  __main__.py        python3 -m mvp 的入口
  task.py            下一条任务：结构化输入 → 逆向链判定（python3 -m mvp.task）
  board.py           文本看板：TASKS.md 的解析/推进/验收（python3 -m mvp.board）
  core/              领域与存储层
    contracts.py     Trace / Step / Verdict（judgment 枚举、facts、双版本号）
    tasks.py         报销判定域 + 真值规则引擎（合成任务）
    rules.py         7 条规则（AND-OR 树）+ 生命周期（candidate/active/retired、容量 300）
    verifier.py      判定型验证器
    llm.py           模型接口 + 仿真实现
    llm_deepseek.py  DeepSeek 真实 API 适配器
    store.py         经验库 + 记录器
    persistence.py   七类 append-only 产物（traces/negative/experience/usage/retired/labels/probes）
    snapshot.py      快照实体（验证器版本 + 经验 id + 时效）
    datasets.py      数据集文件定位 + 下载缓存
    paths.py         路径解析（不依赖 cwd）
  loop/              双环运行层
    runner.py        三路径对照（可接预算）
    backward.py      逆向推理：三态求值 + 短路 + 已证伪停下
    subgoal.py       L1 子目标：局部重分解，用尽才交棒
    budget.py        预算守恒（禁止透支、未用归还）
    evaluator.py     留出集评估 + §6.4 判红 + 双跑差异归因 + 偏倚诊断
    router.py        只读聚合统计的路由决策
    sentry.py        巡检告警（负证据率 / 复用失败率 / 探针差异）
    promotion.py     自动晋升（按复用记录）
    retire.py        淘汰（失败率 / 长期未用，append-only 墓碑）
    miner.py         从负证据挖候选短路规则（support / counter）
    validator.py     回放验证：混淆矩阵 + 激活门（support ≥ 5、counter = 0）
    learner.py       规则挖掘：纯合取搜索 + 析取覆盖 + 台账读取
    probe.py         哨兵探针：确定性抽样、两路答案比对、差异落盘
    sampling.py      无偏采样：强制探索比例 + TV 偏倚度量
    adversarial.py   对抗审计：近似答案识别率（漏 / 多 / 换）
    maintenance.py   维护回路：巡检 → 晋升 → 淘汰 → 路由
    self_bootstrap.py 从轨迹导出任务清单并跑 H1
    doccheck.py      README 与代码一致性检查
  demos/             可运行演示
    demo_real_task.py 调研任务沉淀演示
    demo_dual_loop.py 双环三轮（探索 → 复核 → 晋升 → 复用）
    route_and_sentry.py 路由与哨兵演示
    e2e.py           端到端回路：跑任务 → 自举 → H1 → 判红 → 维护
    self_improve.py  闭环：冷启动 → 学规则 → 符号优先
  tests/             226 个测试（单元 / 集成 / 契约场景 / 不变量）

tools/
  real_check.py      真实数据集检验（Banking77 / GSM8K）
  real_c_check.py    C 路径真实化（真实答案索引 + 真实模型补未命中）
  real_llm_check.py  真实 API 的 A/B 对照
  cluster_check.py   H1 聚类检验
  h1_sweep.py        H1 阈值扫描 + 多抽样稳定性
  family_stats.py    按族标签直接统计分布（有标签时不必聚类）
  real_e2e.py        真实数据端到端：轨迹 + 两种 H1 + 巡检 + 混合成本
  status_check.py    落地状态表机检（make status）

data/                700KB 公开数据集（两个 parquet），已内置
docs/                设计文档、契约、基线声明、实验报告、落地状态总表
runs/                运行产物（自动生成，已 gitignore）
```

---

## 产物：七类 append-only 文件

每次运行生成一个目录，磁盘上留下：

| 文件 | 内容 | 谁写 |
|---|---|---|
| `traces.jsonl` | 执行轨迹：facts、judgment、步级成本、双版本号 | 两条路径 |
| `negative.jsonl` | 负证据：失败路径 + 输入指纹 | 内环（唯一写权限） |
| `experience.jsonl` | 经验条目：(trace_id, vid, verdict) | 外环 |
| `usage.jsonl` | 复用记录：哪条经验被用上、结果如何 | 内环 |
| `retired.jsonl` | 淘汰墓碑：加载经验库时过滤这些 id | 维护回路 |
| `labels.jsonl` | 标签台账：这条任务应判违反哪些规则 | 验收环节 |
| `probes.jsonl` | 哨兵探针：同一条任务收敛答案 vs 探索答案 | 监控 |

全部 append-only，集成测试逐字节验证「重跑不改历史行」。字段含义与归属见
`docs/沉淀产物说明.md`。

---

## 真实数据读数（Banking77，10,003 条 / 77 类）

| 阈值 τ | 覆盖率 | 命中准确率 | 降本 | 质量变化 |
|---|---|---|---|---|
| 0.80 | 9.2% | 96.2% | −9% | +0.3pp |
| 0.70 | 22.6% | 92.6% | −22% | −0.1pp |
| 0.60 | 41.5% | 88.1% | −41% | −2.0pp |

上表「质量变化」按模型准确率 93% 推。换成实测 `deepseek-chat`（73.8%）后结论反转：
查表比模型准，阈值越低越划算——`tools/real_e2e.py` 扫出推荐工作点 τ≈0.45
（成本 −72.2%、质量 +4.0pp，400 条查询）。

对照 GSM8K（输出开放）：同样 20% 覆盖率下命中准确率 **2.8%**，比 Banking77 的 92.6% 差 33 倍。
**这条线划出了适用边界：输出空间可枚举才成立。**

一条能自己算上限的公式（`a` 命中准确率、`p` 模型准确率、`Δ` 可接受质量损失）：

```
覆盖率上限  c ≤ Δ / (p − a)
```

推论：**模型越强，查表越不划算。**

---

## 环境变量

| 变量 | 默认 | 用途 |
|---|---|---|
| `HARNESS_LAB_HOME` | 项目根 | 覆盖项目根 |
| `HARNESS_LAB_RUNS` | `<root>/runs` | 产物目录，可指到外部磁盘 |
| `HARNESS_LAB_CACHE` | `~/.cache/harness-lab` | 数据集附属文件缓存 |
| `HARNESS_LAB_OFFLINE` | 空 | 设 1 时禁止下载，缺文件直接报错 |

路径全部经 `mvp/core/paths.py` 解析，**不依赖当前工作目录**。

---

## 已知限界

不是待办，是这套代码**不能**回答的问题：

1. **真实业务域一条数据都没有。** 所有读数来自合成报销域与公开数据集（Banking77 / GSM8K）。
2. **规则挖掘的 7/7 依赖人给的特征模板**（字段阈值、比值、同期比较、越界判断）。规则由数据选，词表仍由人给。
3. **合成域存在共线**：R1 被学成「无总监审批」单条件，只因生成器里金额与审批相关；换不共线的域会学成两条合取。
4. **阈值多是拍的**：抽样 5%、探针 1%、TV 报警、对抗 99%、标签覆盖 50% 都没有真实流量标定。
5. **`llm.py` 是仿真模型**，准确率是设定值；换真实 API 后检索增强路径的数字可能反转。
6. **指纹命中的 100% 成功率有真值后门**——索引里存的就是答案。降本幅度可信，质量结论不可信。
7. **成本只算 token**，没算延迟、失败人工介入、SLA 罚金。
8. **H1（你的任务里有没有分布）未验证**。拿 50 个真实任务跑 `cluster_check.py` 才能回答。
9. **双环没有接成运行路径**：内外环的分工只在演示里成立，主实验仍是三路径对照；在线路由没做。
10. **逆向链只接了结构化域**：自由文本没有逆向展开，也没有对应验证器——它现在是组件，不是全局执行机制。

---

## 文档索引

| 文档 | 回答什么 |
|---|---|
| `docs/落地状态_2026-11.md` | 设计条目 → 现状 → 证据命令（`make status` 机检） |
| `docs/迭代状态_2026-11.md` | 每个部件的读数与未验证项 |
| `docs/融合双环架构设计v1.0.md` | 双环架构、§6.4 判据、§7 演进、§8 监控 |
| `docs/双引擎逆向推理设计文档.md` | 逆向链、规则生命周期、P0–P5 阶段验收 |
| `docs/双环嵌套接口契约v2.md` | 权限矩阵、快照/验证器/预算守恒、五个必过场景 |
| `docs/沉淀产物说明.md` | 七类产物逐字段说明 |
| `docs/真实数据报告.md` | Banking77 / GSM8K 的完整读数 |