"""DeepSeek 真实 API 适配器（OpenAI 兼容协议，零第三方依赖）。

用途：把 `SimulatedLLM` 换成真实调用，让「成本 / 成功率」从设定值变成实测值。
仿真数字不能当结论（见 llm.py 顶部声明），这里跑出来的才是。

环境变量
    DEEPSEEK_API_KEY   必填
    DEEPSEEK_BASE_URL  默认 https://api.deepseek.com
    DEEPSEEK_MODEL     默认 deepseek-chat

确定性：temperature=0 + 固定 prompt。契约 §5 要求内环同输入同输出，
所以默认不做随机采样；需要探索多样性时由调用方显式传 temperature。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

from .llm import LLMClient, LLMResult, Shot
from .tasks import RULES, RULE_ORDER

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

SYSTEM_PROMPT = (
    "你是差旅报销合规审查员。逐条核对下列规则，输出你判定为违规的规则编号。\n"
    "规则：\n"
    + "\n".join(f"{code} {desc}" for code, desc in ((c, RULES[c]) for c in RULE_ORDER))
    + "\n只输出一个 JSON 数组，元素是字符串编号，例如 [\"R1\",\"R4\"]；"
    "没有违规则输出 []。不要输出解释或任何其他文字。"
)


class DeepSeekError(RuntimeError):
    """调用失败（缺 key、网络、HTTP 错误、响应结构异常）。"""


def build_messages(text: str, shots: list[Shot]) -> list[dict[str, str]]:
    """组装 messages。shots 是程序性知识（规则说明），以 system 消息追加。"""
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if shots:
        experience = "\n".join(f"- {s.text}" for s in shots)
        messages.append({
            "role": "system",
            "content": f"以下是同类任务的历史经验，可作为核对提示：\n{experience}",
        })
    messages.append({"role": "user", "content": text})
    return messages


_JSON_ARRAY = re.compile(r"\[[^\[\]]*\]")


def parse_rules(content: str) -> tuple[set[str], list[str], bool]:
    """解析模型输出。

    返回 (合法规则集合, 被丢弃的未知编号, 是否解析成功)。
    成功但为空数组是合法输出（无违规），与解析失败必须区分开。
    """
    match = _JSON_ARRAY.search(content or "")
    if match is None:
        return set(), [], False
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return set(), [], False
    if not isinstance(raw, list):
        return set(), [], False

    valid: set[str] = set()
    unknown: list[str] = []
    for item in raw:
        code = str(item).strip().upper()
        if code in RULE_ORDER:
            valid.add(code)
        else:
            unknown.append(code)
    return valid, unknown, True


class DeepSeekLLM(LLMClient):
    """`LLMClient` 的 DeepSeek 实现。只依赖标准库，超时与重试固定。"""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        retries: int = 2,
        temperature: float = 0.0,
        max_tokens: int = 64,
    ) -> None:
        self.api_key = (os.environ.get("DEEPSEEK_API_KEY", "")
                        if api_key is None else api_key)
        self.base_url = (base_url or os.environ.get("DEEPSEEK_BASE_URL")
                         or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL
        self.timeout = timeout
        self.retries = retries
        self.temperature = temperature
        self.max_tokens = max_tokens

        # 实测计数，脚本据此区分「模型答错」与「输出解析不了」
        self.calls = 0
        self.parse_errors = 0
        self.unknown_codes: list[str] = []
        self.latency_ms = 0.0

    def require_key(self) -> None:
        if not self.api_key:
            raise DeepSeekError(
                "缺少 DEEPSEEK_API_KEY。先导出再重试：\n"
                "    export DEEPSEEK_API_KEY=sk-...\n"
                "或直接传 api_key= 参数。"
            )

    def _post(self, payload: dict) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                if e.code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    last = e
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise DeepSeekError(f"HTTP {e.code}: {detail}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < self.retries:
                    last = e
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise DeepSeekError(f"网络错误: {e}") from e
        raise DeepSeekError(f"重试后仍失败: {last}")

    def extract(self, text: str, shots: list[Shot]) -> LLMResult:
        self.require_key()
        payload = {
            "model": self.model,
            "messages": build_messages(text, shots),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        started = time.monotonic()
        data = self._post(payload)
        self.latency_ms += (time.monotonic() - started) * 1000
        self.calls += 1

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise DeepSeekError(f"响应结构异常: {str(data)[:300]}") from e

        rules, unknown, ok = parse_rules(content)
        if not ok:
            self.parse_errors += 1
        if unknown:
            self.unknown_codes.extend(unknown)

        usage = data.get("usage") or {}
        return LLMResult(
            rules=rules,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        )