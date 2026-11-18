"""自由文本任务形式化（T12）：把任务拆成可执行计划（子目标 + 验收命令）。

两个实现：
  FileFormalizer     离线计划文件，用于可复现验收；
  DeepSeekFormalizer 真实模型生成计划，用于 demo。

两者都只产出 markdown；是否可执行由 `mvp.handle.parse_task_file` 再校验。
"""
from __future__ import annotations

import re
from pathlib import Path

from .core.llm_deepseek import DeepSeekLLM

SYSTEM_PROMPT = """你是任务规划器。把用户任务拆成 1-4 个可执行子目标，只输出 markdown：

## 子目标

1. 在 <绝对路径> 写入 <内容>
   验收：<shell 命令，rc=0 通过>
2. 在 <绝对路径> 追加 <内容>
   验收：<shell 命令>

规则：动作只能是“在 X 写入/追加”；验收命令必须能独立运行；不要解释，不要代码围栏。"""


class FormalizeError(ValueError):
    """计划文件缺失或模型输出无法当作计划解析。"""


def _clean(text: str) -> str:
    text = (text or "").strip()
    match = re.match(r"^```[a-zA-Z0-9_-]*\n(.*?)\n```$", text, re.S)
    return match.group(1).strip() if match else text


class FileFormalizer:
    """离线计划：直接把计划文件当形式化结果。"""

    vid = "formalize-file-v1"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def formalize(self, task: str) -> str:
        if not self.path.is_file():
            raise FormalizeError(f"计划文件不存在：{self.path}")
        return self.path.read_text(encoding="utf-8")


class DeepSeekFormalizer:
    """真实模型规划：temperature=0，固定提示词。"""

    vid = "formalize-ds-v1"

    def __init__(self, api_key: str | None = None,
                 max_tokens: int = 600) -> None:
        self.max_tokens = max_tokens
        self.llm = DeepSeekLLM(api_key=api_key, temperature=0.0,
                               max_tokens=max_tokens)

    def formalize(self, task: str) -> str:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"任务：{task}"},
        ]
        text, _, _ = self.llm.chat(messages, max_tokens=self.max_tokens)
        return _clean(text)