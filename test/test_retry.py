#!/usr/bin/env python3
"""测试 LLM retry 机制：无 tool call + 不合法工具/参数时是否正确重试。"""

import asyncio
import json
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
from llm.client import LLMClient
from core.action import ActionRegistry
from core.actions.common import SpeakAction, ObserveAction, MoveAction


def _make_response(text_content: str, tool_call: bool, func_name: str = "observe", func_args: str = '{"internal_monologue": "测试"}'):
    """构造模拟的 litellm 响应，可自定义工具名和参数"""
    class FakeFunction:
        name = func_name
        arguments = func_args

    class FakeMessage:
        content = text_content
        tool_calls = None

    if tool_call:
        FakeMessage.tool_calls = [type("FakeToolCall", (), {
            "id": f"call_test_{func_name}",
            "type": "function",
            "function": FakeFunction(),
        })()]

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
        self.registry.register(ObserveAction())
        self.registry.register(SpeakAction())
        self.registry.register(MoveAction())
        self.agent_names = ["张三", "李四"]
        self.locations = ["主厅", "吧台"]
        self.agents_by_location = {"主厅": ["张三", "李四"], "吧台": []}
        _hearable = []
        for loc in ["吧台", "主厅"]:
            for n in self.agents_by_location.get(loc, []):
                if n != "TestAgent":
                    _hearable.append(n)
        self.base_context = {
            "agent_name": "TestAgent",
            "agent_location": "吧台",
            "agent_names": self.agent_names,
            "locations": self.locations,
            "agents_by_location": self.agents_by_location,
            "hearable_agents": _hearable,
            "adjacent_locations": [l for l in self.locations if l != "吧台"],
        }

    async def _run(self, text_first, text_second, text_third, validation_context=None):
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
                validation_context=validation_context,
            )
            return action, call_count

    # ── 原有用例：无 tool call ──

    def test_first_try_success(self):
        """第一次就返回 tool call → 不重试"""
        action, calls = asyncio.run(
            self._run(
                _make_response("", tool_call=True),
                None, None,
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

    # ── 新增：不合法的工具/参数 ──

    def test_invalid_tool_name_then_success(self):
        """第一次调用不存在的工具，第二次正常 → 重试 1 次"""
        action, calls = asyncio.run(
            self._run(
                _make_response("", tool_call=True, func_name="fly"),
                _make_response("", tool_call=True, func_name="observe"),
                None,
                validation_context=self.base_context,
            )
        )
        self.assertIsNotNone(action)
        self.assertEqual(action.action_type, "observe")
        self.assertEqual(calls, 2)

    def test_speak_to_self_rejected(self):
        """对自己说话 → 重试"""
        action, calls = asyncio.run(
            self._run(
                _make_response("", tool_call=True, func_name="speak",
                               func_args='{"target": "TestAgent", "content": "hi"}'),
                _make_response("", tool_call=True, func_name="speak",
                               func_args='{"target": "张三", "content": "hi"}'),
                None,
                validation_context=self.base_context,
            )
        )
        self.assertIsNotNone(action)
        self.assertEqual(action.action_type, "speak")
        self.assertEqual(action.target, "张三")
        self.assertEqual(calls, 2)

    def test_move_to_same_location_rejected(self):
        """移到当前位置 → 重试"""
        action, calls = asyncio.run(
            self._run(
                _make_response("", tool_call=True, func_name="move",
                               func_args='{"target": "吧台", "content": "go"}'),
                _make_response("", tool_call=True, func_name="move",
                               func_args='{"target": "主厅", "content": "go"}'),
                None,
                validation_context=self.base_context,
            )
        )
        self.assertIsNotNone(action)
        self.assertEqual(action.action_type, "move")
        self.assertEqual(action.target, "主厅")
        self.assertEqual(calls, 2)

    def test_invalid_speak_target_then_success(self):
        """第一次对不存在的人说话，第二次对正确的人 → 重试 1 次"""
        action, calls = asyncio.run(
            self._run(
                _make_response("", tool_call=True, func_name="speak",
                               func_args='{"target": "王五", "content": "hi"}'),
                _make_response("", tool_call=True, func_name="speak",
                               func_args='{"target": "张三", "content": "hi"}'),
                None,
                validation_context=self.base_context,
            )
        )
        self.assertIsNotNone(action)
        self.assertEqual(action.action_type, "speak")
        self.assertEqual(action.target, "张三")
        self.assertEqual(calls, 2)

    def test_invalid_move_target_then_success(self):
        """第一次移到不存在的位置，第二次移到正确位置 → 重试 1 次"""
        action, calls = asyncio.run(
            self._run(
                _make_response("", tool_call=True, func_name="move",
                               func_args='{"target": "后院", "content": "go"}'),
                _make_response("", tool_call=True, func_name="move",
                               func_args='{"target": "主厅", "content": "go"}'),
                None,
                validation_context=self.base_context,
            )
        )
        self.assertIsNotNone(action)
        self.assertEqual(action.action_type, "move")
        self.assertEqual(action.target, "主厅")
        self.assertEqual(calls, 2)

    def test_invalid_params_exhausted(self):
        """三次参数都不合法 → 返回 None"""
        action, calls = asyncio.run(
            self._run(
                _make_response("", tool_call=True, func_name="speak",
                               func_args='{"target": "王五", "content": "hi"}'),
                _make_response("", tool_call=True, func_name="speak",
                               func_args='{"target": "赵六", "content": "hi"}'),
                _make_response("", tool_call=True, func_name="speak",
                               func_args='{"target": "钱七", "content": "hi"}'),
                validation_context=self.base_context,
            )
        )
        self.assertIsNone(action)
        self.assertEqual(calls, 3)


if __name__ == "__main__":
    unittest.main()
