"""语义 rubric 评审（T10）：给候选内容打 0-100 分，带版本号。

两个实现：
  FileJudge      离线评分（JSONL 一行一条），用于可复现验收；
  DeepSeekJudge  真实模型按固定 rubric 打分，用于 demo。

限制：这里只保证"评审机制可跑、可版本化、可替换"，**不保证评分与人类一致**。
一致性要靠标注样本校准，仓库里还没有。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .core.llm_deepseek import DeepSeekLLM

RUBRIC = ("四项各 25 分：准确性（有没有说错）、完整性（该说的说了没）、"
          "可执行性（读者能不能照做）、无冗余（有没有凑字数）。")
SYSTEM_PROMPT = ("你是评审员。" + RUBRIC +
                 '只输出 JSON：{"score": 0-100, "reason": "一句话"}。')


class JudgeError(ValueError):
    """评分条目缺失或模型输出无法解析。"""


@dataclass(frozen=True)
class JudgeResult:
    score: int
    reason: str = ""


class FileJudge:
    """离线评审：按调用顺序给出预置分数。"""

    vid = "judge-v1"

    def __init__(self, results: list[JudgeResult]) -> None:
        self._results = list(results)
        self._index = 0

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "FileJudge":
        results: list[JudgeResult] = []
        for line_no, line in enumerate(
                Path(path).read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise JudgeError(f"第 {line_no} 行不是合法 JSON：{exc}") from exc
            results.append(JudgeResult(int(obj["score"]),
                                       str(obj.get("reason", ""))))
        return cls(results)

    def score(self, task: str, content: str) -> JudgeResult:
        if self._index >= len(self._results):
            raise JudgeError("评分条目不足：候选数多于 judge JSONL 行数")
        result = self._results[self._index]
        self._index += 1
        return result


def _parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        raise JudgeError(f"评审输出里没有 JSON：{text[:120]}")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise JudgeError(f"评审 JSON 解析失败：{exc}") from exc


class DeepSeekJudge:
    """真实模型评审；temperature=0，版本号固定。"""

    vid = "judge-ds-v1"

    def __init__(self, api_key: str | None = None,
                 max_tokens: int = 200) -> None:
        self.max_tokens = max_tokens
        self.llm = DeepSeekLLM(api_key=api_key, temperature=0.0,
                               max_tokens=max_tokens)

    def score(self, task: str, content: str) -> JudgeResult:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"任务：{task}\n\n候选内容：\n{content}"},
        ]
        text, _, _ = self.llm.chat(messages, max_tokens=self.max_tokens)
        obj = _parse_json(text)
        score = max(0, min(100, int(obj.get("score", 0))))
        return JudgeResult(score, str(obj.get("reason", "")))