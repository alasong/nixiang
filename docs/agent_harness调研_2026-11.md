# Agent Harness 最新进展调研

**日期** 2026-11-17 ｜ **方法** 3 轮检索，交叉 9 个来源（含 1 篇 arXiv 源码研究、4 家厂商发布公告、2 篇实践白皮书、2 篇研究博客）
**结论可信度** 高（多个独立来源互证）；标注 ⚠️ 的为单一来源或厂商自报数字

---

## TL;DR

1. **"Harness 工程"在 2026 年初被确立为一门学科**。里程碑是 arXiv 2609.00006 对 11 个生产级编码 harness 的源码剖析。
2. **两个反直觉共识**：约 400 万行代码里，没有任何 harness 导入通用 agent 框架；也没有任何一个用向量检索代码。
3. **量化证据确认 harness 本身值钱**：同模型换 harness，成本差 5 倍，tokens per solved task 差 70 倍，成功率最多差 13.7pp。
4. **研究前沿的共识方向**：把状态从上下文挪进 harness（无状态 agent、fork-and-flush），压缩决策自动化，harness 行为蒸馏进权重。
5. **对你的设计最相关的一条**：Anthropic 官方警告 harness "编码了模型做不到什么的假设，会随模型进步过时" —— 这正是我们之前推导的"规则库限制强模型"问题的官方印证。

---

## 一、学科化：七大子系统与两大缺失

arXiv 2609.00006（2026-07）对 Claude Code、Codex CLI、Gemini CLI、Mistral Vibe、OpenHands、Aider、Mini-SWE-Agent、Hermes、Pi、OpenCode、OpenClaw 做了源码级剖析，外加元 harness Omnigent 作对照。七个标准子系统，每个都存在从极简到极繁的完整谱系：

| 子系统 | 最简实现 | 最繁实现 |
|---|---|---|
| Agent loop | Mini-SWE-Agent：线性 while 循环 + 一个 bash 工具 | OpenHands：事件日志 + 并行动作 |
| 模型连接 | 一个 LiteLLM 调用 | Hermes：5 种传输、29 个 provider profile |
| 工具 | 只有 bash | Claude Code：43 个类型化工具 |
| 记忆与上下文 | 全量历史原样保留 | Codex：跨会话自维护记忆 |
| 权限与安全 | 步数与成本上限 | Codex：规则 + LLM 审查器 + 三操作系统沙箱 |
| 子 agent | Aider：无（刻意） | Claude Code：子 agent 可再开子 agent |
| 扩展 | Python 协议 | Pi：一切都是扩展；Codex：插件市场 |

**关键观察一：loop 复杂度不预测结果。** Mini-SWE-Agent 约 100 行 Python、1 个工具，SWE-bench Verified 达 74%+。大型 harness 的大部分代码花在 benchmark 不测的东西上（OpenCode 约 3/5 非测试代码是客户端 UI）。

**关键观察二：两个贯穿全样本的缺失。**

- **无框架**：所有 loop 都手写在自家 async 原语上。讽刺的是，"harness engineering"这个词是在 LangChain 里造出来的，但没有 harness 发布时带 LangChain 库。
- **无代码向量检索**：全部靠 ripgrep、glob、tree-sitter、AGENTS.md/CLAUDE.md 这类上下文文件。理由：代码有精确结构且分钟级变化，embedding 索引过期太快。embedding 只出现在对话记忆里。

**关键观察三：Skills 超过 MCP**（9/11 vs 8/11）。社区共识的分工建议：skills 装可复用流程与领域知识，MCP 接外部系统。Skills 在一个季度内长出了应用商店的配套（注册中心、信任等级、隔离检疫），并出现第一批 agent 自己写给自己的 skill。

**关键观察四：收敛变成模仿。** 4 月各 harness 还是独立发现相同想法，7 月已能在源码里追出传播路径：Codex 几乎逐字采纳 Claude Code 的 hooks 事件词汇（PreToolUse/PostToolUse/PreCompact…）；Codex 能导入 Claude Code 会话并迁移配置；OpenHands 读 Claude Code 插件清单。

---

## 二、平台化：四家大厂的动向

| 厂商 | 动作 | 要点 |
|---|---|---|
| **Microsoft** | Agent Framework Harness，7-22 发布，8 月 GA | Python/.NET 双语，10 大内置能力（compaction、todo、file memory、skills、审批、OpenTelemetry）默认全开可摘除。卖点：原型与生产用同一心智模型 |
| **OpenAI** | 开源 Codex Harness，8-20，Apache 2.0 | 三组件：codex exec（无头 CLI）、SDK、app-server（JSON-RPC 长驻进程）。⚠️ 自报：harness 级优化让输出 token 降 6 倍，ARC-AGI-3 从 13.3% 升到 38.3% |
| **DeepSeek** | Harness 开发者预览，8-13，MIT | Cordis 插件系统，模型适配器/工具注册/session 日志/agent loop 全是插件。四种预设模式含 PTC（程序化工具调用）。v0.1.0-rc.5，声明会有破坏性变更 |
| **AWS** | 开源 Strands Harness，9-21 | ⚠️ 自测 6 套件：同模型下比 Claude Code 便宜 77%（$56 vs $248）且准确率高 7.9pp。核心是两条上下文规则 + 三层存储 |

另外 **Pi**（Mario Zechner 的极简 harness）在 8 月约 85k stars；⚠️ Databricks 报告它在 Opus 4.8 上 pass rate 最高且每轮上下文只有 Claude Code/Codex 的约 1/3。

## 三、量化：harness 到底值多少钱

固定模型、只换 harness 的对照研究（这是评判 harness 价值的正确方法）：

| 研究 | 设置 | 结果 |
|---|---|---|
| LangChain Deep Agents（2026-02） | GPT-5.2-Codex 固定，只换 harness，Terminal-Bench 2.0 | 52.8% → 66.5%（top 30 → top 5） |
| Terminal-Bench 2.0 论文（2026-01） | 同模型不同脚手架 | Gemini 2.5 Pro 差异 17% |
| HarnessTax（Arena，2026-09） | 7 模型 × 3 harness | 成功率相近，成本差 5 倍；9/12 组合里最佳成绩来自非厂商自家的 harness |
| The New Stack 分析（2026-08） | Aider/Claude Code/OpenClaw 同模型 | 每解决一个任务的 token 差 70 倍 |
| AWS Strands 自测 | 6 harness × 6 套件，同模型 | 成本差 77%，见上表 |

两个推论：

1. **harness 对成功率的影响存在但不稳定，对成本的影响巨大且稳定。** 这和我们在双环设计里的结论同构：调 prompt（B 路径）省钱不稳定，不调用（C 路径）省钱稳定。
2. **Terminal-Bench 作者仍坚持"选模型比选脚手架更重要"。** harness 是乘数不是引擎。

---

## 四、研究前沿：五个方向

**1. 无状态 agent（SLA，arXiv 2610.07625，Stanford/CMU/UW 等）**
最激进：agent 之间不保留对话，**harness 持有全部研究状态**（候选解 + 评分），每次调用为角色构建全新上下文。预算 10 亿 token 级的任务上，84% 的 token 减少还能拿到最好结果。
——这是"状态在 harness 不在模型"的极端化，正好是快照思想的实例。

**2. Fork-and-Flush（Microsoft/Papailiopoulos）**
长程 agent 会陷入"idea basin"：不断增长的上下文把它锚死在一条思路里。解法：定期 fork 出共享工作区但清空聊天历史的副本，从最好的继续。等算力下最多 +66%，13 个长程任务里 12 个胜过单跑和 best-of-N。
——上下文不只是成本问题，是**探索视野**问题。

**3. ReHarness（arXiv 2610.12668）**
持续学习不止写入新反思，还要**用新轨迹回头修订旧反思**（对文本状态做 backward flow）。
——这就是我们说的"负证据要能修正旧经验"，已有人做出系统。

**4. Harness-Zero（北大）+ SoL-Pi（NVIDIA/NTU/MIT）**
Harness-Zero 把专门 harness 的行为蒸馏进 Qwen3.5-9B 权重：宏平均成功率 23.3% → 44.3%，**甚至超过同一底座带 harness 部署的 41.7%**。SoL-Pi 用自动研究循环跑 152 个优化方向找出 4 个 harness 机制（动作融合、在线压缩、观察打包、证据保持约简），token 流量 -44.7~49%。
——注意 Harness-Zero 是"蒸馏"：把 harness 的程序性行为内化，正是我们说过"蒸馏不是学习、能力封顶在教师"的实例，它内化的只是 harness 的行为模式。

**5. 压缩保真（claimkeep）与压缩决策（SelfCompact）**
claimkeep：压缩摘要保 gist 丢细节（文件路径、标识符），用 PreCompact 钩子收割带置信标注的声明做逐字简报，探针恢复率 18.0% → 33.9%，代价是路径类保真从 72.2% 降到 29.9%。SelfCompact（Apple）：给模型一个 summarize 工具 + 何时该压/不该压的规则，比固定周期压缩便宜 30-70%。
——压缩是收益递减的精细活，两个方向（压什么、何时压）都在被单独研究。

---

## 五、与你的双环设计的对照

这次调研最有价值的不是事实清单，是**它给你的设计提供了外部印证和反例**：

| 你的设计 | 行业印证 | 差异/警示 |
|---|---|---|
| 状态放 harness（快照），内环只读 | SLA：harness 持有状态，agent 无状态，token -84% | 行业走得更极端：连对话都短生命周期化 |
| 负证据持久化、可修正 | ReHarness 用新轨迹回头修订旧反思 | 它做了"修正"，我们只做了"追加"——值得跟进 |
| 压缩/预算守恒 | SelfCompact、claimkeep、SoL-Pi 的四个机制 | 已细到"何时压缩"的决策本身；我们只在守恒层面 |
| Harness 行为 → 规则/经验 | Harness-Zero 蒸馏进权重，效果超过带 harness | **反例**：程序性行为内化是可行的，我们的"规则库降为配件"判断需要松一点——但注意它内化的只是 harness 行为，不是领域能力 |
| 规则库限制强模型 | Anthropic 官方：harness 编码了"模型做不到"的假设，**会过时**，要计划移除 | 完全一致，且官方把"移除组件"写进了建议 |
| 验证器决定能力上限 | 行业默认： computational（测试/lint）+ inferential（LLM judge）双轨，DUET 让 judge 与 agent 交替进化 | 我们的评分型验证器三态设计与之兼容，"judge 必须与 agent 共同进化"是新增点 |

**三个直接行动建议**：

1. **砍掉所有"防模型做不到"的组件时要有退出标准。** Anthropic 已经把这条写进官方建议，你的基线声明里"复杂度纪律"应加一条：每季度复审一次组件清单，标出哪些因模型变强而不再必要。
2. **压缩粒度按信息类型分。** claimkeep 的数据说明摘要对"散文事实"保真 7.3%、对"文件路径"保真 72.2%，任何一刀切的压缩策略都会系统性丢某一类信息。你的经验库分层设计应加"信息类型"维度。
3. **无状态模式值得做进内环作为可选项。** SLA 的 -84% token 与你的成本目标高度一致，而且实现成本低于微调路线——它只是改"谁持有状态"。

---

## 六、来源

- [Harness Engineering: Anatomy of Eleven Systems, arXiv 2609.00006](https://machinelibrary.ai/r/10.48550/arxiv.2609.00006)（核心）
- [Microsoft Agent Framework Harness 发布](https://aiadvances.org/the-agent-harness-explained-what-microsoft-shipped-and-how-to-build-on-it-in-python-f07081f40f4c) / [GA](https://byteiota.com/microsoft-agent-harness-ga)
- [OpenAI Codex Harness 开源](https://www.eneralabs.com/blog/openai-codex-harness-open-source-enterprise-2026)
- [AWS Strands Harness 成本对比](https://www.besthub.dev/articles/aws-strands-harness-cuts-agent-costs-77-by-swapping-runtime-not-model-585607e21e15)
- [无状态 agent / Fork-and-Flush / ReHarness 综述](https://bayesiansapien.github.io/cere-bro/agentic-systems/2026-10-13-stateless-agents-self-compaction-and-idea-basins)
- [claimkeep 压缩保真](https://synapsesocial.com/papers/6a8037d975c2e31742c86f52)
- [Agentic Harness 白皮书（含 harness 量化证据表）](https://keyholesoftware.com/agentic-harness/)
- [Agent Harness Landscape 目录](https://cc.bruniaux.com/guide/agent-harness-landscape)
- [Harness 源码研究摘要](https://academy.codearia.com/en/articles/harness-engineering-coding-agents-source-study)
