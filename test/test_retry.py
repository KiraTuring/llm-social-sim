#!/usr/bin/env python3
"""测试 LLM retry 机制：无 tool call 时是否正确重试。"""

import asyncio
import json
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
from llm.client import LLMClient
from core.action import ActionRegistry


def _make_response(text_content: str, tool_call: bool):
    """构造模拟的 litellm 响应"""
    class FakeFunction:
        name = "observe"
        arguments = '{"internal_monologue": "测试"}'

    class FakeMessage:
        content = text_content
        tool_calls = None

    if tool_call:
        FakeMessage.tool_calls = [type("FakeToolCall", (), {"function": FakeFunction()})()]

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

        def model_dump_json(self):
            return '{"mock": true}'

    return FakeResponse()


class TestLLMRetry(unittest.TestCase):

    def setUp(self):
        config = {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "test_key",
            "response_mode": "tool_call",
        }
        self.client = LLMClient(config, logger=None)
        self.registry = ActionRegistry()

    async def _run(self, text_first, text_second, text_third):
        """mock litellm.acompletion 依次返回三次不同结果"""

        responses = [text_first, text_second, text_third]
        call_count = 0

        async def fake_acompletion(**kwargs):
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return resp

        with patch("litellm.acompletion", new=fake_acompletion):
            _, action = await self.client.call(
                system_prompt="测试",
                messages=[{"role": "user", "content": "hello"}],
                action_registry=self.registry,
                agent_name="TestAgent",
                tick=1,
            )
            return action, call_count

    def test_first_try_success(self):
        """第一次就返回 tool call → 不重试"""
        action, calls = asyncio.run(
            self._run(
                _make_response("", tool_call=True),
                None,
                None,
            )
        )
        self.assertIsNotNone(action)
        self.assertEqual(calls, 1)

    def test_retry_then_success(self):
        """第一次没调工具，第二次返回 → 重试 1 次"""
        action, calls = asyncio.run(
            self._run(
                _make_response("我只想聊天", tool_call=False),
                _make_response("好的", tool_call=True),
                None,
            )
        )
        self.assertIsNotNone(action)
        self.assertEqual(calls, 2)

    def test_retry_exhausted(self):
        """三次都没调工具 → 返回 None"""
        action, calls = asyncio.run(
            self._run(
                _make_response("聊天1", tool_call=False),
                _make_response("聊天2", tool_call=False),
                _make_response("聊天3", tool_call=False),
            )
        )
        self.assertIsNone(action)
        self.assertEqual(calls, 3)


if __name__ == "__main__":
    unittest.main()
