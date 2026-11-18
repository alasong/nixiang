"""可修复任务的验收器：失败时打印 expected/actual，供修复反馈使用。"""
import sys

mode, inp, out = sys.argv[1:4]
raw = open(inp, encoding="utf-8").read().strip()
actual = open(out, encoding="utf-8").read().strip()

if mode == "join-dash":
    expected = "-".join(raw.split())
elif mode == "sum":
    expected = str(sum(int(x) for x in raw.split()))
elif mode == "reverse":
    expected = raw[::-1]
else:
    raise SystemExit(f"未知 mode：{mode}")

print(f"expected={expected} actual={actual}")
assert actual == expected, f"expected={expected} actual={actual}"
