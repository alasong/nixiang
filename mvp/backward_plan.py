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


def plan_from_verify(verify: str) -> list[SubGoal]:
    """把验收命令反推成子目标；认不出就返回 []。"""
    subs: list[SubGoal] = []
    for index, atom in enumerate(split_conjunction(verify), 1):
        atom = atom.strip()
        match = _GREP.fullmatch(atom)
        if match:
            content = next(group for group in
                           (match.group("s1"), match.group("s2"),
                            match.group("s3")) if group is not None)
            subs.append(SubGoal(label=f"反推 {index}",
                                action=f"在 {match.group('path')} 写入 {content}",
                                verify=atom))
            continue
        match = _TESTF.fullmatch(atom)
        if match:
            subs.append(SubGoal(label=f"反推 {index}",
                                action=f"在 {match.group('path')} 写入 ok",
                                verify=atom))
            continue
        return []
    return subs


def plan_text(subgoals: list[SubGoal]) -> str:
    """渲染成任务文件格式，交给现有分解器执行。"""
    lines = ["## 子目标", ""]
    for index, sub in enumerate(subgoals, 1):
        lines.append(f"{index}. {sub.action}")
        lines.append(f"   验收：{sub.verify}")
    return "\n".join(lines) + "\n"