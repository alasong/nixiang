# 这个项目要交什么

一句话：**用户把任意任务交进来，拿回通过验收的结果。**
双环、逆向推理是手段；验收通过才算交付。

## 清单（最多 5 行，一次只做一行）

1. ✅ 判定型任务能交能判（当前样例：报销单）
   验收：`python3 -m mvp.task --json '{"amount": 8000, "has_director_approval": false}'`
   退出 0，且输出含 `R1`
   （这是「判」的能力，还不是「做」的能力）

2. ❌ 交任意任务 + 验收命令，系统自己动手做完
   验收：`python3 -m mvp.handle --task "在 /tmp/xm-hello.txt 写入 hello" --verify "grep -q hello /tmp/xm-hello.txt"`
   退出 0
   （现在 rc=1：`mvp.handle` 还不存在）

3. ❌ 改仓库里的真文件，并通过检查（不是玩具文件）
   验收：`python3 -m mvp.handle --task "在 README 末尾加一行 ## 验收" --verify "grep -q '## 验收' README.md && make docs-check"`
   退出 0

4. ❌ 内环复用：同类任务第二次 0 次模型调用（双环落到运行路径）
   验收：同一条任务连跑两遍，第二遍输出「模型调用 0 次」
   `python3 -m mvp.handle --task "在 /tmp/xm-hello.txt 写入 hello" --verify "grep -q hello /tmp/xm-hello.txt" --twice`
   退出 0 且第二遍日志里 `model_calls=0`

5. ❌ 逆向分解：一条复合任务拆成子目标，逐个验收（逆向推理落到运行路径）
   验收：`python3 -m mvp.handle --task tasks/T2.md --verify "make docs-check" --explain` 退出 0，
   且输出含 `子目标 3/3`

## 两条规矩

- 每行必须能写成**一条命令**，跑完退出 0 才算交；写不出命令的那行，不许开工。
- **一次只做一行**；做不完就把这行改小，不要回去重新画图。

## 为什么只有这几行

「加个监控模块」上不了这张清单——它没有验收命令。
凡是上不了这张清单的活，都是自嗨。