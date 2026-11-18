"""开放任务的结构代理验收：只查形式，不查观点对错。"""
import re
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
headings = len(re.findall(r"^##\s+", text, re.M))
digits = len(re.findall(r"\d", text))
checks = {
    "数据项目": "数据项目" in text,
    "自动化项目": "自动化项目" in text,
    "超级助手": "超级助手" in text,
    "标题>=3": headings >= 3,
    "长度>=500": len(text.strip()) >= 500,
    "数字>=3": digits >= 3,
    "含结论": "结论" in text,
}
failed = [name for name, ok in checks.items() if not ok]
print(f"headings={headings} chars={len(text.strip())} digits={digits} "
      f"failed={failed}")
assert not failed, f"failed={failed}"
