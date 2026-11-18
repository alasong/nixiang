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

仓库根目录的 `TASKS.md` 就是看板：任务表 9 列（`id / 任务 / 能力 / 覆盖 / 验收命令 / 期望 / 状态 / 尝试 / 证据`），
外加一张目标条目表。状态只有 `todo / doing / done / blocked`，同时最多 1 个 doing，未完成不超过 5 个。

```bash
python3 -m mvp.board              # 看板视图
python3 -m mvp.board coverage     # 目标条目有没有人认领（缺口退出码 1）
python3 -m mvp.board start T2     # todo → doing（先跑红检：验收已通过会被拒）
python3 -m mvp.board verify T2    # 跑验收，过则 done；连续 3 次不过转 blocked
```

三条硬规矩：没有验收命令不许开工；验收已经通过不许开工；目标条目没人认领就是缺口。
设计取舍见 `docs/board设计_2026-11.md`。

---

## 下一条任务（怎么把任务交给系统）

两条入口，别混用：

| 入口 | 接什么 | 边界 |
|---|---|---|
| `python3 -m mvp.task` | 结构化判定任务（差旅报销字段） | 没有验证器的自由文本不接 |
| `python3 -m mvp.handle` | 任意任务文本 + 验收命令（M0/M1） | 确定性模式 + 任务文件分解；匹配不上返回 `unsupported`（rc=2），不硬猜 |

任意任务 + 验收命令（M0，执行 → 验收 → 轨迹落盘）：

```bash
python3 -m mvp.handle --task "在 /tmp/xm-hello.txt 写入 hello" \
  --verify "grep -q hello /tmp/xm-hello.txt"
# 任务 H-20261118-111419-6133
#   执行  write_file
#   验收  rc=0  grep -q hello /tmp/xm-hello.txt
# verify: pass
```

退出码：0 验收通过、1 执行或验收失败、2 没有执行器能处理。轨迹落
`runs/handle/traces.jsonl`（append-only，含任务原文、验收命令、两步记录）。

任务级逆向分解（M1）：任务文件写 `## 子目标`，每个子目标必须带 `验收：` 命令；
或者验收命令本身是 `A && B && C` 合取。`--explain` 打出分解树：

```bash
python3 -m mvp.handle --task tasks/T2.md --verify "make docs-check" --explain
#   子目标 3/3
#     子目标 1: 在 /tmp/xm-sg-a.txt 写入 alpha  验收：grep -q alpha /tmp/xm-sg-a.txt
#     子目标 2: 在 /tmp/xm-sg-b.txt 写入 beta   验收：grep -q beta /tmp/xm-sg-b.txt
#     子目标 3: 在 /tmp/xm-sg-c.txt 写入 gamma  验收：grep -q gamma /tmp/xm-sg-c.txt
# verify: pass
```

没有 `验收：` 的子目标会被分解器丢弃，不许执行；任务文件来源的子目标全过后，
全局验收仍要单独通过。

同类任务第二次（T4）：`--twice` 连跑两次，第二次命中计划缓存：

```bash
python3 -m mvp.handle --task "在 /tmp/xm-hello.txt 写入 hello" \
  --verify "grep -q hello /tmp/xm-hello.txt" --twice
# --- 第二次（复用计划）---
#   复用  计划缓存
# model_calls=0
```

`model_calls=0` 在 M0/M1 是结构性的（这两层本来不调模型）；接真实模型后，
这个计数才有区分力。

多 Agent 并行子目标（M2a）：写域不重叠时 `--agents N` 真并行；`--audit` 打过程审计：

```bash
python3 -m mvp.handle --task tasks/T7.md --verify "make docs-check" --agents 3 --audit
# agents=3  wall_ms=323
# process 6/6  入口=✓ 验收=✓ 分解=✓ 并发=✓ 归因=✓ 留痕=✓
```

写域冲突（多个子目标写同一路径）会自动退回串行，并打印 `stop=write_conflict`。

N 路候选 + 质量分（M2b）：在通过验收的候选里取最高分。

```bash
# 离线候选集（可复现，T8 卡用它验收）
python3 -m mvp.handle --task "写一段包含 nixiang 的说明" --out /tmp/x.txt \
  --verify "grep -q nixiang /tmp/x.txt" --score "wc -l < /tmp/x.txt" \
  --candidates-from tasks/T8-candidates.jsonl
#   候选  3 份  通过 2  选中 3
# quality best=3 single=0 candidates=3

# 真实模型采样（需要 DEEPSEEK_API_KEY）
python3 -m mvp.handle --task "用两三段介绍 nixiang 项目" --out /tmp/y.txt \
  --verify "grep -q nixiang /tmp/y.txt" --score "wc -m < /tmp/y.txt" --candidates 3
# quality best=311 single=223 candidates=3   （实测读数，见报告）
```

`--score` 是外部质量分命令（输出整数）；选择器只会在通过验收的候选里取分最高
的那份。质量分本身是不是"好"，决定这次选择的实际意义——长度不等于质量。

跨域压测（M4）：8 个任务 × 3 次，5 种任务形状：

```bash
python3 -m mvp.stress --cases tasks/M4/cases.jsonl --repeat 3
# 汇总 cases=8 runs=24 passed=24 failed=0
```

它只换任务形状与验收命令（文档/数据/代码/文本/复合），不换领域知识、
不测语义质量。

语义 rubric 评审（T10）与墙钟预算（T11）：

```bash
# 离线评分（可复现）
python3 -m mvp.handle --task "写一段包含 nixiang 的说明" --out /tmp/x.txt \
  --verify "grep -q nixiang /tmp/x.txt" \
  --candidates-from tasks/T10-candidates.jsonl --judge-from tasks/T10-judge.jsonl
# judge=judge-v1  /  quality best=90

# 真实模型评审（需要 DEEPSEEK_API_KEY）
python3 -m mvp.handle ... --judge

# 墙钟预算：超过就停，返回 1
python3 -m mvp.handle --task tasks/T7.md --verify "true" --agents 3 --budget-ms 50
# stop=budget_exhausted
```

评审器版本号：离线 `judge-v1`、DeepSeek `judge-ds-v1`；rubric 固定四项各 25 分
（准确/完整/可执行/无冗余）。它只保证机制可跑、可追溯；**和人类判断是否一致，
要靠标注样本校准**，仓库里还没有。

自由文本形式化（T12）+ 实时进度：

```bash
# 离线计划（可复现）
python3 -m mvp.handle --task "写一份包含 nixiang 的两行说明并保存到 /tmp/xm-f.txt" \
  --verify "grep -q nixiang /tmp/xm-f.txt" --formalize-from tasks/T12-plan.md --progress
# [progress] 子目标 1/2 pass 15ms
# formalized=formalize-file-v1

# 真实模型规划（需要 DEEPSEEK_API_KEY）
python3 -m mvp.handle --task "在 /tmp/x.txt 写入两行介绍 nixiang" \
  --verify "grep -q nixiang /tmp/x.txt" --formalize --progress
# formalized=formalize-ds-v1
```

`--progress` 也可单独用于普通任务/候选，逐条打印子目标或候选的完成情况。

真实模型小样本测量（需 key）：

```bash
zsh -ic 'python3 tools/real_measure.py'
# 基础 8 任务：single 8/8；难任务 4：single 3/4，hard-exact 全败且未被救回
```

读数与限制见 `docs/真实模型测量_2026-11.md`（三轮：基础全过、严格验收全过、难任务 3/4 且系统性失败未被救回）。

rubric 校准（有标注样本时用）：

```bash
# 离线样本（JSONL：task/content/human），可复现
python3 -m mvp.calibrate --samples tasks/T13-samples.jsonl \
  --judge-from tasks/T13-judge.jsonl
# agree=3/3 mae=6.7 bias=0.0 tolerance=±10

# 真实模型评审（需要 key）
python3 -m mvp.calibrate --samples my_samples.jsonl --judge
```

判据：`|judge - human| ≤ tolerance` 算一致；一致率低于 `--min-agree` 返回 1。
它只负责算术与门限；**样本必须是你的人工标注**，否则就是自评自测。

验收反馈修复（T14）：

```bash
# 离线修复响应（可复现）
python3 -m mvp.handle --task "写一行中文，恰好 20 个汉字，且包含 nixiang" \
  --out /tmp/xm-r.txt --verify "python3 tasks/T14-verify.py /tmp/xm-r.txt" \
  --candidates-from tasks/T14-candidates.jsonl \
  --repair-from tasks/T14-repair.jsonl --repair 1
# repaired=1  /  verify: pass

# 真实模型：生成 1 份，失败后最多修 2 轮
python3 -m mvp.handle ... --candidates 1 --repair 2 --progress
```

对“有反馈就能改”的失败有用：3 个信息差任务 initial 0/3、修复救回 3/3
（`rescue_rate=1.00`，含空响应重试）；对精确计数/硬约束这类能力边界，反馈修复无效。
实录见 `docs/验收反馈修复_2026-11.md`。

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
make test        # 302 条测试，约 1 秒
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
| 推进任务清单 | `python3 -m mvp.board` | 按 todo/doing/done/blocked 分列；`coverage` 查缺口 |
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
  handle.py          任意任务 + 验收命令：执行 → 验收 → 轨迹（python3 -m mvp.handle）
  board.py           文本看板：解析/推进/验收/目标覆盖（python3 -m mvp.board）
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
  candidates.py      候选生成：离线 JSONL 或 DeepSeek 采样（N 路候选）
  stress.py          跨域压测：多域任务 × 重复（python3 -m mvp.stress）
  measure.py         1 份 vs N 份候选的小样本测量（真实模型读数）
  repair.py          验收反馈修复（失败原因喂回再生成）
  calibrate.py       rubric 校准：judge 与人类分的一致率（离线样本可复现）
  judge.py           语义 rubric 评审：离线 judge-v1 / DeepSeek judge-ds-v1
  formalize.py       自由文本 → 子目标计划：离线 formalize-file-v1 / DeepSeek formalize-ds-v1
  repair.py          验收反馈修复：失败原因喂回再生成（repair-file-v1 / repair-ds-v1）
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
  tests/             302 个测试（单元 / 集成 / 契约场景 / 不变量）

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
| `docs/通用超级助手_差距与开发报告_2026-11.md` | 通用超级助手（双闭环 + 逆向）的差距矩阵、目标架构与 M0–M4 验收 |
| `docs/落地状态_2026-11.md` | 设计条目 → 现状 → 证据命令（`make status` 机检） |
| `docs/迭代状态_2026-11.md` | 每个部件的读数与未验证项 |
| `docs/融合双环架构设计v1.0.md` | 双环架构、§6.4 判据、§7 演进、§8 监控 |
| `docs/双引擎逆向推理设计文档.md` | 逆向链、规则生命周期、P0–P5 阶段验收 |
| `docs/双环嵌套接口契约v2.md` | 权限矩阵、快照/验证器/预算守恒、五个必过场景 |
| `docs/沉淀产物说明.md` | 七类产物逐字段说明 |
| `docs/真实数据报告.md` | Banking77 / GSM8K 的完整读数 |
