"""验收反馈修复（T14）：把验收失败的输出喂回模型，再生成一次。

两个实现：
  FileRepairer     离线修复响应（JSONL 一行一条），用于可复现验收；
  DeepSeekRepairer 真实模型按失败原因改写。

只改“内容”，不改验收；修复是否成功仍由外部命令判定。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .core.llm_deepseek import DeepSeekLLM

SYSTEM_PROMPT = ("你是修复器。根据任务、上次内容与验收失败输出，改写内容使其通过验收。"
                 "只输出修复后的完整文件内容，不要解释，不要代码围栏。")


class RepairError(ValueError):
    """修复响应缺失，或模型输出为空。"""


def _clean(text: str) -> str:
    text = (text or "").strip()
    match = re.match(r"^```[a-zA-Z0-9_-]*\n(.*?)\n```$", text, re.S)
    return match.group(1).strip() if match else text


class FileRepairer:
    """离线修复：按调用顺序返回预置内容。"""

    vid = "repair-file-v1"

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._index = 0

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "FileRepairer":
        out: list[str] = []
        for line_no, line in enumerate(
                Path(path).read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RepairError(f"第 {line_no} 行不是合法 JSON：{exc}") from exc
            if not isinstance(obj, str):
                raise RepairError(f"第 {line_no} 行必须是 JSON 字符串")
            out.append(obj)
        return cls(out)

    def repair(self, task: str, content: str, feedback: str) -> str:
        if self._index >= len(self._responses):
            raise RepairError("修复响应不足：修复轮数多于 repair JSONL 行数")
        response = self._responses[self._index]
        self._index += 1
        return response


class DeepSeekRepairer:
    """真实模型修复；temperature=0。"""

    vid = "repair-ds-v1"

    def __init__(self, api_key: str | None = None,
                 max_tokens: int = 800) -> None:
        self.max_tokens = max_tokens
        self.llm = DeepSeekLLM(api_key=api_key, temperature=0.0,
                               max_tokens=max_tokens)

    def repair(self, task: str, content: str, feedback: str) -> str:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"任务：{task}\n\n上次内容：\n{content}\n\n"
                                        f"验收失败输出：\n{feedback}"},
        ]
        text, _, _ = self.llm.chat(messages, max_tokens=self.max_tokens)
        fixed = _clean(text)
        if not fixed:
            raise RepairError("模型返回空修复内容")
        return fixed