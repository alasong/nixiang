# TASKS

北极星：**用户把任意任务交进来，拿回通过验收的结果。**

## 模板

- 一个任务一行，列固定；不加列、不写散文。
- `状态` 只取：`todo` / `doing` / `done` / `blocked`。
- `验收` 列必须是一条可执行命令；`期望` 列写退出码 + 输出子串。
- `done` 必须有 `证据`：日期 + 退出码。
- 同时最多 1 个 `doing`；未完成任务不超过 5 个。

## 任务

| id | 任务 | 能力 | 验收命令 | 期望 | 状态 | 证据 |
|---|---|---|---|---|---|---|
| T1 | 判定型任务能交能判（报销样例） | judge | `python3 -m mvp.task --json '{"amount": 8000, "has_director_approval": false}'` | rc=0，输出含 `R1` | done | 2026-11-18 rc=0 |
| T2 | 任意任务 + 验收命令，系统动手做完 | act | `python3 -m mvp.handle --task "在 /tmp/xm-hello.txt 写入 hello" --verify "grep -q hello /tmp/xm-hello.txt"` | rc=0，输出含 `verify: pass` | todo | — |
| T3 | 改仓库里的真文件并通过检查 | act | `python3 -m mvp.handle --task "在 README 末尾加一行 ## 验收" --verify "grep -q '## 验收' README.md && make docs-check"` | rc=0，输出含 `verify: pass` | todo | — |
| T4 | 同类任务第二次 0 次模型调用（内环复用） | reuse | `python3 -m mvp.handle --task "在 /tmp/xm-hello.txt 写入 hello" --verify "grep -q hello /tmp/xm-hello.txt" --twice` | rc=0，输出含 `model_calls=0` | todo | — |
| T5 | 复合任务拆子目标、逐个验收（逆向分解） | decompose | `python3 -m mvp.handle --task tasks/T2.md --verify "make docs-check" --explain` | rc=0，输出含 `子目标 3/3` | todo | — |

## 验收协议

1. 开工：目标行 `状态` 改 `doing`，清空 `证据`。
2. 收工：跑 `验收命令`，把「日期 + rc + 关键输出」写进 `证据`。
3. 判定：rc 与输出都符合 `期望` → `done`；否则保持 `doing` 并写清卡点，或缩小验收后回 `todo`。
4. 不新增列、不改别人的行；新任务追加一行，`id` 连续。