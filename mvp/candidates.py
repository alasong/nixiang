"""候选生成（M2b）：真实模型生成 N 份内容，或从离线 JSONL 读取。

离线候选集是为了让验收可复现：卡片不依赖网络与 key；真实模型路径单独
跑 demo（`--candidates N`）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .core.llm_deepseek import DeepSeekLLM

SYSTEM_PROMPT = ("你是执行任务的助手。只输出要写入文件的内容，不要解释，"
                 "不要 markdown 代码围栏。")
MAX_TOKENS = 800
TEMPERATURE = 0.9


class CandidateError(ValueError):
    """候选输入不合法（JSONL 行格式、空候选等）。"""


def _clean(text: str) -> str:
    """去掉模型偶尔裹上的 ``` 围栏。"""
    text = (text or "").strip()
    match = re.match(r"^```[a-zA-Z0-9_-]*\n(.*?)\n```$", text, re.S)
    return match.group(1).strip() if match else text


def load_jsonl(path: str | Path) -> list[str]:
    """一行一个候选：JSON 字符串，或 {"content": "..."} 对象。"""
    out: list[str] = []
    for line_no, line in enumerate(
            Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CandidateError(f"第 {line_no} 行不是合法 JSON：{exc}") from exc
        if isinstance(obj, str):
            out.append(obj)
        elif isinstance(obj, dict) and isinstance(obj.get("content"), str):
            out.append(obj["content"])
        else:
            raise CandidateError(
                f"第 {line_no} 行必须是字符串或 {{\"content\": ...}}")
    return out


class DeepSeekCandidateProvider:
    """按温度采样生成 N 份候选；复用现有 DeepSeek 适配器的重试与计数。"""

    def __init__(self, api_key: str | None = None,
                 temperature: float = TEMPERATURE,
                 max_tokens: int = MAX_TOKENS) -> None:
        self.max_tokens = max_tokens
        self.llm = DeepSeekLLM(api_key=api_key, temperature=temperature,
                               max_tokens=max_tokens)

    @property
    def calls(self) -> int:
        return self.llm.calls

    def generate(self, task: str, n: int) -> list[str]:
        if n < 1:
            raise CandidateError("候选数必须 ≥ 1")
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"任务：{task}\n请直接输出要写入文件的内容。"},
        ]
        candidates: list[str] = []
        for index in range(1, n + 1):
            # 同一任务的第 N 版：换结构/措辞，避免近确定模型给出同质候选
            variant = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",
                 "content": f"任务：{task}\n这是第 {index} 版，请换一种结构与措辞；"
                            "只输出要写入文件的内容。"},
            ]
            content, _, _ = self.llm.chat(variant, max_tokens=self.max_tokens)
            candidates.append(_clean(content))
        return candidates