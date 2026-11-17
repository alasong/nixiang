"""DeepSeek 适配器的离线测试：不联网，只测提示词组装与输出解析。

真实 API 调用不在这里测（要 key 和网络），用 tools/real_llm_check.py 手动跑。
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from mvp.core.llm import Shot
from mvp.core.llm_deepseek import DeepSeekError, DeepSeekLLM, build_messages, parse_rules
from mvp.core.tasks import RULE_ORDER


class TestParseRules(unittest.TestCase):
    def test_plain_array(self) -> None:
        rules, unknown, ok = parse_rules('["R1", "R4"]')
        self.assertTrue(ok)
        self.assertEqual(rules, {"R1", "R4"})
        self.assertEqual(unknown, [])

    def test_empty_array_is_valid_output(self) -> None:
        rules, _, ok = parse_rules("经核对无违规，[]")
        self.assertTrue(ok)
        self.assertEqual(rules, set())

    def test_code_fence_is_tolerated(self) -> None:
        rules, _, ok = parse_rules('```json\n["R2"]\n```')
        self.assertTrue(ok)
        self.assertEqual(rules, {"R2"})

    def test_unknown_code_is_dropped_not_fatal(self) -> None:
        rules, unknown, ok = parse_rules('["R1", "R9", "r3"]')
        self.assertTrue(ok)
        self.assertEqual(rules, {"R1", "R3"})   # 大小写归一
        self.assertEqual(unknown, ["R9"])

    def test_garbage_is_parse_failure(self) -> None:
        for text in ("", "无违规", "R1,R4", "{'R1': true}"):
            _, _, ok = parse_rules(text)
            self.assertFalse(ok, f"{text!r} 应判定为解析失败")


class TestPrompt(unittest.TestCase):
    def test_system_prompt_lists_all_rules(self) -> None:
        messages = build_messages("报销申请单 X", [])
        system = messages[0]["content"]
        for code in RULE_ORDER:
            self.assertIn(code, system)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[-1]["role"], "user")

    def test_shots_become_experience_message(self) -> None:
        shot = Shot("E1", "tier1-high", {"R1"}, "金额超 5000 必须核对总监审批栏。")
        messages = build_messages("报销申请单 X", [shot])
        self.assertEqual(len(messages), 3)
        self.assertIn("金额超 5000", messages[1]["content"])
        self.assertEqual(messages[0]["role"], "system")


class TestKeyHandling(unittest.TestCase):
    def test_missing_key_raises_with_hint(self) -> None:
        # 显式传空串 = 没有 key，不得回落到环境变量（否则测试会真的发请求）
        client = DeepSeekLLM(api_key="")
        with self.assertRaises(DeepSeekError) as ctx:
            client.extract("报销申请单 X", [])
        self.assertIn("DEEPSEEK_API_KEY", str(ctx.exception))
        self.assertEqual(client.calls, 0)

    def test_env_key_is_used_when_not_passed(self) -> None:
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-from-env"}):
            self.assertEqual(DeepSeekLLM().api_key, "sk-from-env")


if __name__ == "__main__":
    unittest.main()