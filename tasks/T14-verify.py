import sys

text = open(sys.argv[1], encoding="utf-8").read().strip()
assert len(text) == 20 and "nixiang" in text, f"len={len(text)}"
