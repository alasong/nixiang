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
| T3 | 改仓库里的真文件并通过检查 | act | g3 | `python3 -m mvp.handle --task "在 README 末尾加一行 ## 验收" --verify "grep -q '## 验收' README.md && make docs-check"` | rc=0，输出含 `verify: pass` | todo | 0 | — |
| T4 | 同类任务第二次 0 次模型调用（内环复用） | reuse | g4 | `python3 -m mvp.handle --task "在 /tmp/xm-hello.txt 写入 hello" --verify "grep -q hello /tmp/xm-hello.txt" --twice` | rc=0，输出含 `model_calls=0` | todo | 0 | — |
| T5 | 复合任务拆子目标、逐个验收（逆向分解） | decompose | g5 | `python3 -m mvp.handle --task tasks/T2.md --verify "make docs-check" --explain` | rc=0，输出含 `子目标 3/3` | done | 0 | 2026-11-18 rc=0 自动验收通过 |
| T6 | 借鉴 goal-tree 的覆盖检查/红检/失败阻断，落进 board | meta | g0 | `python3 -m unittest mvp.tests.test_board_goals -q` | rc=0 | done | 0 | 2026-11-18 rc=0 自动验收通过 |

## 目标条目

| gid | 条目 |
|---|---|
| g0 | 看板具备目标覆盖检查、开工前红检、失败阻断 |
| g1 | 判定型任务能交能判 |
| g2 | 任意任务 + 验收命令，系统动手做完 |
| g3 | 改仓库真文件并通过检查 |
| g4 | 同类任务第二次 0 次模型调用 |
| g5 | 复合任务拆子目标、逐个验收 |

## 验收协议

1. 开工：`python3 -m mvp.board start <id>`；验收已通过会被拒。
2. 收工：`python3 -m mvp.board verify <id>`，通过则 `done` 并写证据。
3. 失败：`尝试` 自动 +1，连续 3 次转 `blocked`，写清卡点。
4. 全局：`python3 -m mvp.board coverage` 检查有没有目标条目没人认领。
