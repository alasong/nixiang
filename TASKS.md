# TASKS

北极星：**用户把任意任务交进来，拿回通过验收的结果。**

## 模板

- 任务表固定 9 列，目标表固定 2 列；不加列、不写散文。
- `状态` 只取 `todo` / `doing` / `done` / `blocked`。
- `覆盖` 填目标条目 id（没有就写 `—`）；每个目标条目必须被至少一张卡认领。
- `验收命令` 必须可执行；`期望` 写 `rc=` 与输出子串。
- `done` 必须有证据；`尝试` 记验收失败次数，连续 3 次转 `blocked`。
- 同时最多 1 个 `doing`；未完成任务不超过 5 个。
- 开工前会跑一次验收：**已经通过的不许开工**（防假任务）。

## 任务

| id | 任务 | 能力 | 覆盖 | 验收命令 | 期望 | 状态 | 尝试 | 证据 |
|---|---|---|---|---|---|---|---|---|
| T1 | 判定型任务能交能判（报销样例） | judge | g1 | `python3 -m mvp.task --json '{"amount": 8000, "has_director_approval": false}'` | rc=0，输出含 `R1` | done | 0 | 2026-11-18 rc=0 |
| T2 | 任意任务 + 验收命令，系统动手做完 | act | g2 | `python3 -m mvp.handle --task "在 /tmp/xm-hello.txt 写入 hello" --verify "grep -q hello /tmp/xm-hello.txt"` | rc=0，输出含 `verify: pass` | done | 0 | 2026-11-18 rc=0 自动验收通过 |
| T3 | 改仓库里的真文件并通过检查 | act | g3 | `python3 -m mvp.handle --task "在 README 末尾加一行 ## 验收" --verify "grep -q '## 验收' README.md && make docs-check"` | rc=0，输出含 `verify: pass` | done | 1 | 2026-11-18 rc=0 自动验收通过 |
| T4 | 同类任务第二次 0 次模型调用（内环复用） | reuse | g4 | `python3 -m mvp.handle --task "在 /tmp/xm-hello.txt 写入 hello" --verify "grep -q hello /tmp/xm-hello.txt" --twice` | rc=0，输出含 `model_calls=0` | done | 0 | 2026-11-18 rc=0 自动验收通过 |
| T5 | 复合任务拆子目标、逐个验收（逆向分解） | decompose | g5 | `python3 -m mvp.handle --task tasks/T2.md --verify "make docs-check" --explain` | rc=0，输出含 `子目标 3/3` | done | 0 | 2026-11-18 rc=0 自动验收通过 |
| T6 | 借鉴 goal-tree 的覆盖检查/红检/失败阻断，落进 board | meta | g0 | `python3 -m unittest mvp.tests.test_board_goals -q` | rc=0 | done | 0 | 2026-11-18 rc=0 自动验收通过 |
| T7 | 多 Agent 并行子目标 + 过程审计 6/6 | agents | g6,g7 | `python3 -m mvp.handle --task tasks/T7.md --verify "make docs-check" --agents 3 --audit` | rc=0，输出含 `process 6/6` | done | 0 | 2026-11-18 rc=0 自动验收通过 |
| T8 | N 路候选 + 质量分选择 | select | g8 | `python3 -m mvp.handle --task "写一段包含 nixiang 的说明" --out /tmp/xm-m2b.txt --verify "grep -q nixiang /tmp/xm-m2b.txt" --score "wc -l < /tmp/xm-m2b.txt" --candidates-from tasks/T8-candidates.jsonl` | rc=0，输出含 `candidates=3` | done | 0 | 2026-11-18 rc=0 自动验收通过 |
| T9 | 跨域压测：多域任务 × 重复 | stress | g9 | `python3 -m mvp.stress --cases tasks/M4/cases.jsonl --repeat 3` | rc=0，输出含 `passed=24` | done | 0 | 2026-11-18 rc=0 自动验收通过 |
| T10 | 语义 rubric 评审（版本化） | judge | g10 | `python3 -m mvp.handle --task "写一段包含 nixiang 的说明" --out /tmp/xm-judge.txt --verify "grep -q nixiang /tmp/xm-judge.txt" --candidates-from tasks/T10-candidates.jsonl --judge-from tasks/T10-judge.jsonl` | rc=0，输出含 `judge=judge-v1` | done | 0 | 2026-11-18 rc=0 自动验收通过 |
| T11 | 墙钟预算强制（超时即停） | budget | g11 | `python3 -m mvp.handle --task tasks/T7.md --verify "true" --agents 3 --budget-ms 50` | rc=1，输出含 `budget_exhausted` | done | 0 | 2026-11-18 rc=1 自动验收通过 |
| T12 | 自由文本任务形式化 + 实时进度 | formalize | g12 | `python3 -m mvp.handle --task "写一份包含 nixiang 的两行说明并保存到 /tmp/xm-f.txt" --verify "grep -q nixiang /tmp/xm-f.txt" --formalize-from tasks/T12-plan.md --progress` | rc=0，输出含 `formalized=formalize-file-v1` | done | 0 | 2026-11-18 rc=0 自动验收通过 |
| T13 | rubric 校准工具（一致率） | calibrate | g13 | `python3 -m mvp.calibrate --samples tasks/T13-samples.jsonl --judge-from tasks/T13-judge.jsonl` | rc=0，输出含 `agree=3/3` | done | 0 | 2026-11-18 rc=0 自动验收通过 |
| T14 | 验收反馈修复（失败原因喂回） | repair | g14 | `python3 -m mvp.handle --task "写一行中文，恰好 20 个汉字，且包含 nixiang" --out /tmp/xm-r.txt --verify "python3 tasks/T14-verify.py /tmp/xm-r.txt" --candidates-from tasks/T14-candidates.jsonl --repair-from tasks/T14-repair.jsonl --repair 1` | rc=0，输出含 `repaired=1` | done | 0 | 2026-11-18 rc=0 自动验收通过 |
| T15 | 修复空响应自动重试 | repair | g15 | `python3 -m mvp.handle --task "写一行中文，恰好 20 个汉字，且包含 nixiang" --out /tmp/xm-r2.txt --verify "python3 tasks/T14-verify.py /tmp/xm-r2.txt" --candidates-from tasks/T14-candidates.jsonl --repair-from tasks/T15-repair.jsonl --repair 1` | rc=0，输出含 `repaired=1` | done | 0 | 2026-11-18 rc=0 自动验收通过 |
| T16 | 一键全量验证 verify-all | verify | g16 | `make verify-all` | rc=0 | done | 0 | 2026-11-18 rc=0 自动验收通过 |
| T17 | 从验收反推动作（--auto-plan） | backward | g17 | `python3 -m mvp.handle --task "给我一个包含 hello 的文件" --verify "grep -q hello /tmp/xm-auto.txt" --auto-plan` | rc=0，输出含 `formalized=backward-plan-v1` | done | 0 | 2026-11-18 rc=0 自动验收通过 |
| T18 | 限宽限深搜索（同路径冲突回退） | search | g18 | `python3 -m mvp.handle --task "文件里要既有 a 又有 b" --verify "grep -q a /tmp/xm-s.txt && grep -q b /tmp/xm-s.txt" --auto-plan` | rc=0，输出含 `search=2` | done | 0 | 2026-11-18 rc=0 自动验收通过 |
| T19 | AND-OR 图 + 证明树 + priority(n) | planner | g19 | `python3 -m mvp.handle --task "三个词都要在同一个文件里" --verify "grep -q a /tmp/xm-p.txt && grep -q b /tmp/xm-p.txt && grep -q c /tmp/xm-p.txt" --auto-plan --explain-plan` | rc=0，输出含 `or=2 tried=2` | done | 0 | 2026-11-18 rc=0 自动验收通过 |
| T20 | 策略挖掘：按验收形状复用分支选择 | strategy | g20 | `python3 -m mvp.handle --task "文件里要既有 a 又有 b（第二例）" --verify "grep -q a /tmp/xm-p2.txt && grep -q b /tmp/xm-p2.txt" --auto-plan` | rc=0，输出含 `strategy=hit` | todo | 0 | — |
| T21 | 真实业务数据接入（H1） | data | g21 | `test -s data/real_tasks.jsonl && test "$(wc -l < data/real_tasks.jsonl)" -ge 50` | rc=0 | blocked | 0 | 2026-11-18 等用户提供 50 条真实任务+真值标签到 data/real_tasks.jsonl |
| T22 | rubric 校准（人工样本） | calibrate | g22 | `test -s data/calibration.jsonl && python3 -m mvp.calibrate --samples data/calibration.jsonl --judge --tolerance 10 --min-agree 0.8` | rc=0，输出含 `agree=` | blocked | 0 | 2026-11-18 等用户提供 20-30 条人工样本到 data/calibration.jsonl；跑 --judge 需要 env 有 DEEPSEEK_API_KEY |
| T23 | priority(n) 用历史成功率估计 | planner | g23 | `python3 -m mvp.handle --task "文件里要既有 a 又有 b" --verify "grep -q a /tmp/xm-pri.txt && grep -q b /tmp/xm-pri.txt" --auto-plan --explain-plan` | rc=0，输出含 `p=` | todo | 0 | — |
| T24 | handle 线质量漂移探针 | drift | g24 | `python3 -m mvp.drift --demo` | rc=0，输出含 `drift=0` | todo | 0 | — |
| T25 | 在线路由（按验收形状） | route | g25 | `python3 -m mvp.route --demo` | rc=0，输出含 `route=` | todo | 0 | — |
| T26 | 阈值标定（需真实流量） | tune | g26 | `test -s data/traffic.jsonl` | rc=0 | blocked | 0 | 2026-11-18 等真实流量样本 data/traffic.jsonl；没有它阈值只能拍 |
| T27 | 微调（排最后） | tune | g27 | `test -s data/finetune.jsonl && test -s data/real_tasks.jsonl` | rc=0 | blocked | 0 | 2026-11-18 等真实数据+前四段指标稳定；设计文档规定微调排最后 |

## 目标条目

| gid | 条目 |
|---|---|
| g0 | 看板具备目标覆盖检查、开工前红检、失败阻断 |
| g1 | 判定型任务能交能判 |
| g2 | 任意任务 + 验收命令，系统动手做完 |
| g3 | 改仓库真文件并通过检查 |
| g4 | 同类任务第二次 0 次模型调用 |
| g5 | 复合任务拆子目标、逐个验收 |
| g6 | 过程审计可机检（6 项） |
| g7 | 多 Agent 并行执行子目标（写域互斥） |
| g8 | N 路候选 + 质量分选择（离线候选可复现） |
| g9 | 跨域压测可机检（多域任务 × 重复，读数可复现） |
| g10 | 语义 rubric 评审（版本化、离线可注入） |
| g11 | 墙钟预算强制（超时即停、不超支） |
| g12 | 自由文本任务形式化（LLM/离线注入，可执行计划） |
| g13 | rubric 校准工具（离线样本可复现，真实样本待输入） |
| g14 | 验收反馈修复（失败原因喂回再生成） |
| g15 | 修复空响应自动重试（空/异常再试一次） |
| g16 | 一键全量验证（单命令串起所有机检） |
| g17 | 从验收谓词反推动作（符号逆向规划） |
| g18 | 限宽限深搜索（同路径冲突回退） |
| g19 | AND-OR 图 + 证明树 + priority(n) |
| g20 | 策略挖掘：按验收形状复用分支选择 |
| g21 | 真实业务数据接入（H1 / 真实域读数） |
| g22 | rubric 校准（人工标注样本一致率） |
| g23 | priority(n) 用历史成功率估计（替代静态成本） |
| g24 | handle 线质量漂移探针（已通过任务定期重判） |
| g25 | 在线路由（按验收形状选 auto-plan/formalize/单份） |
| g26 | 阈值标定（抽样/TV/对抗/标签覆盖，需真实流量） |
| g27 | 微调（前四段指标稳定 + 数据量，排最后） |

## 验收协议

1. 开工：`python3 -m mvp.board start <id>`；验收已通过会被拒。
2. 收工：`python3 -m mvp.board verify <id>`，通过则 `done` 并写证据。
3. 失败：`尝试` 自动 +1，连续 3 次转 `blocked`，写清卡点。
4. 全局：`python3 -m mvp.board coverage` 检查有没有目标条目没人认领。
