"""从验收谓词反推动作（T17）：符号逆向规划。

不调模型，只认几种可逆的验收形状：

  grep -q <内容> <路径>   → 在 <路径> 写入 <内容>
  test -f <路径>          → 在 <路径> 写入 ok
  A && B                  → 分别反推，全为 AND

任何一段认不出来就整体放弃（不半规划），返回空列表。
"""
from __future__ import annotations

import re

from .handle import SubGoal, split_conjunction

_GREP = re.compile(
    r"grep\s+-q(?:xF|x)?\s+(?:'(?P<s1>[^']*)'|\"(?P<s2>[^\"]*)\"|(?P<s3>\S+))\s+"
    r"(?P<path>\S+)")
_TESTF = re.compile(r"test\s+-f\s+(?P<path>\S+)")


def _infer(atom: str) -> tuple[str, str] | None:
    """从一个验收原子反推 (路径, 内容)；认不出返回 None。"""
    match = _GREP.fullmatch(atom)
    if match:
        content = next(group for group in
                       (match.group("s1"), match.group("s2"),
                        match.group("s3")) if group is not None)
        return match.group("path"), content
    match = _TESTF.fullmatch(atom)
    if match:
        return match.group("path"), "ok"
    return None


def plan_from_verify(verify: str) -> list[SubGoal]:
    """朴素反推：每个验收原子都当一次「写入」；认不出就返回 []。"""
    subs: list[SubGoal] = []
    for index, atom in enumerate(split_conjunction(verify), 1):
        atom = atom.strip()
        inferred = _infer(atom)
        if inferred is None:
            return []
        path, content = inferred
        subs.append(SubGoal(label=f"反推 {index}",
                            action=f"在 {path} 写入 {content}",
                            verify=atom))
    return subs


def plan_variants(verify: str, depth: int = 1) -> list[list[SubGoal]]:
    """限宽搜索的候选方案：depth=0 只有朴素方案；depth≥1 多一个同路径回退方案。"""
    atoms = [atom.strip() for atom in split_conjunction(verify)]
    inferred: list[tuple[str, str, str]] = []
    for atom in atoms:
        result = _infer(atom)
        if result is None:
            return []
        inferred.append((atom, result[0], result[1]))
    naive = [SubGoal(label=f"反推 {index}",
                     action=f"在 {path} 写入 {content}", verify=atom)
             for index, (atom, path, content) in enumerate(inferred, 1)]
    if depth < 1:
        return [naive]
    seen: set[str] = set()
    dedup: list[SubGoal] = []
    for index, (atom, path, content) in enumerate(inferred, 1):
        verb = "写入" if path not in seen else "追加"
        seen.add(path)
        dedup.append(SubGoal(label=f"反推 {index}",
                             action=f"在 {path} {verb} {content}", verify=atom))
    if all(left.action == right.action for left, right in zip(naive, dedup)):
        return [naive]
    return [naive, dedup]


def plan_text(subgoals: list[SubGoal]) -> str:
    """渲染成任务文件格式，交给现有分解器执行。"""
    lines = ["## 子目标", ""]
    for index, sub in enumerate(subgoals, 1):
        lines.append(f"{index}. {sub.action}")
        lines.append(f"   验收：{sub.verify}")
    return "\n".join(lines) + "\n"