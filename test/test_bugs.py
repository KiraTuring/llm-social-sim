#!/usr/bin/env python3
"""测试 Bug 1-4 修复: model 硬编码 / compress 空返回 / visibility 安全 / message_bus 字段"""

import asyncio
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

from core.world import WorldState
from core.message import MessageBus
from core.action import ActionRegistry
from memory.memory import AgentMemory
from scenarios.base import Scene


class TestBug1ModelConfig(unittest.TestCase):
    """Bug 1: llm/client.py model 应从 config 读取，而非硬编码"""

    def setUp(self):
        config = {
            "provider": "openai",
            "model": "gpt-4o",
            "base_url": "https://api.openai.com/v1",
            "api_key": "test_key",
            "response_mode": "tool_call",
        }
        from llm.client import LLMClient
        self.client = LLMClient(config, logger=None)

    def test_model_from_config(self):
        """llm 构造时使用 config 中的 model"""
        self.assertEqual(self.client.model, "gpt-4o")
        self.assertEqual(self.client.provider, "openai")
        self.assertEqual(self.client._model_str, "openai/gpt-4o")

    def test_model_passed_to_acompletion(self):
        """call() 应把 provider/model 传给 litellm.acompletion"""
        async_mock = AsyncMock(return_value=MagicMock(
            choices=[MagicMock(
                message=MagicMock(content="", tool_calls=[
                    MagicMock(function=MagicMock(
                        name="observe",
                        arguments='{"internal_monologue": "test"}'
                    ))
                ])
            )]
        ))

        registry = ActionRegistry()

        with patch("litellm.acompletion", async_mock):
            import asyncio
            asyncio.run(self.client.call(
                system_prompt="test",
                messages=[{"role": "user", "content": "hi"}],
                action_registry=registry,
                agent_name="Test",
                tick=1,
            ))

            _, kwargs = async_mock.call_args
            self.assertEqual(kwargs["model"], "openai/gpt-4o")


class TestBug2CompressNoop(unittest.TestCase):
    """Bug 2: memory.compress() 暂不调用 LLM"""

    def test_compress_returns_without_calling(self):
        """compress() 不应调用任何外部方法，直接返回"""
        memory = AgentMemory(name="测试", short_limit=10, compress_threshold=15)
        memory.add("事件1")

        import asyncio
        result = asyncio.run(memory.compress(None))
        self.assertIsNone(result)

        self.assertEqual(len(memory._short_term), 1)

    def test_compress_empty_memory(self):
        """compress() 空记忆时也不应报错"""
        memory = AgentMemory(name="测试", short_limit=10, compress_threshold=15)

        import asyncio
        result = asyncio.run(memory.compress(None))
        self.assertIsNone(result)


class TestBug3VisibilitySafe(unittest.TestCase):
    """Bug 3: WorldState 默认 visible_locations 为自身"""

    def test_world_default_visible_locations(self):
        """WorldState 默认 get_visible_locations 应只返回自身"""
        w = WorldState(locations=["a", "b"])
        self.assertEqual(w.get_visible_locations("a"), ["a"])

    def test_scene_none_visibility(self):
        """Scene 设 visibility=None 时 init_world 后只能看见自己"""
        class TestScene(Scene):
            name = "test"
            locations = ["a"]
            agents = []
            gm_events = []
            gm_random_events = []
            visibility = None

        world = TestScene().init_world()
        self.assertEqual(world.get_visible_locations("a"), ["a"])

    def test_scene_empty_visibility(self):
        """Scene 设 visibility={} 时 init_world 后只能看见自己"""
        class TestScene(Scene):
            name = "test"
            locations = ["a"]
            agents = []
            gm_events = []
            gm_random_events = []
            visibility = {}

        world = TestScene().init_world()
        self.assertEqual(world.get_visible_locations("a"), ["a"])

    def test_scene_with_visibility(self):
        """Scene 有 visibility 数据时正确传播给 get_visible_locations"""
        class TestScene(Scene):
            name = "test"
            locations = ["a", "b"]
            agents = []
            gm_events = []
            gm_random_events = []
            visibility = {"a": ["b"]}

        world = TestScene().init_world()
        self.assertIn("b", world.get_visible_locations("a"))
        self.assertNotIn("a", world.get_visible_locations("b"))

    def test_visible_locations_never_crash(self):
        """get_visible_locations 对各种输入不抛异常"""
        w = WorldState(locations=["a", "b"])
        self.assertEqual(w.get_visible_locations("不存在"), ["不存在"])

        w.set_visibility({})
        self.assertEqual(w.get_visible_locations("不存在"), ["不存在"])


class TestBug4MessageBusField(unittest.TestCase):
    """Bug 4: message_bus 是 WorldState 的 dataclass 字段"""

    def test_message_bus_default_none(self):
        """WorldState 默认 message_bus = None"""
        w = WorldState()
        self.assertIsNone(w.message_bus)

    def test_message_bus_can_be_set(self):
        """message_bus 可赋值为 MessageBus 实例"""
        w = WorldState()
        bus = MessageBus()
        w.message_bus = bus
        self.assertIs(w.message_bus, bus)

    def test_message_bus_from_init_world(self):
        """Scene.init_world() 设置 message_bus"""
        class TestScene(Scene):
            name = "test"
            locations = ["a"]
            agents = []
            gm_events = []
            gm_random_events = []

        world = TestScene().init_world()
        self.assertIsNotNone(world.message_bus)
        self.assertIsInstance(world.message_bus, MessageBus)


    def test_model_with_slash_used_as_is(self):
        """model 已包含 / 时不做拼接"""
        config = {
            "provider": "deepseek",
            "model": "deepseek/deepseek-chat",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "test_key",
            "response_mode": "tool_call",
        }
        from llm.client import LLMClient
        client = LLMClient(config, logger=None)
        self.assertEqual(client._model_str, "deepseek/deepseek-chat")


class TestMemoryCompression(unittest.TestCase):
    """验证记忆压缩机制的 LLM 调用和状态更新"""

    def setUp(self):
        self.memory = AgentMemory(name="测试", short_limit=3, compress_threshold=5)

    def add_events(self, n: int):
        for i in range(n):
            self.memory.add(f"事件{i+1}")

    def test_compress_not_triggered_below_threshold(self):
        """short_term 长度低于 threshold 时不触发压缩"""
        self.add_events(4)
        self.assertFalse(self.memory._compress_needed)

    def test_compress_flag_set_at_threshold(self):
        """达到 threshold 时设置压缩标志"""
        self.add_events(5)
        self.assertTrue(self.memory._compress_needed)

    def test_compress_noop_without_llm_client(self):
        """llm_client=None 时 compress 不报错"""
        self.add_events(6)
        import asyncio
        result = asyncio.run(self.memory.compress(None))
        self.assertIsNone(result)

    def test_compress_truncates_and_updates_summary(self):
        """compress 成功后 short_term 截断、summary 更新、标志清除"""
        from llm.client import JSONResult

        self.add_events(7)

        mock_client = MagicMock()
        mock_client.logger = None
        mock_client.call_json = AsyncMock(return_value=JSONResult(
            data={"summary": "测试摘要文本"},
            raw='{"summary": "测试摘要文本"}',
        ))

        asyncio.run(self.memory.compress(mock_client))

        self.assertEqual(len(self.memory._short_term), 3)
        self.assertEqual(self.memory._summary, "测试摘要文本")
        self.assertFalse(self.memory._compress_needed)

    def test_compress_merge_with_existing_summary(self):
        """多次压缩时，已有摘要和新经历一起发给 LLM"""
        from llm.client import JSONResult

        self.memory._summary = "旧摘要"
        self.add_events(6)

        mock_client = MagicMock()
        mock_client.logger = None
        mock_client.call_json = AsyncMock(return_value=JSONResult(
            data={"summary": "新摘要"},
            raw='{"summary": "新摘要"}',
        ))

        asyncio.run(self.memory.compress(mock_client))

        user_msg = mock_client.call_json.call_args.kwargs["user_content"]
        self.assertIn("旧摘要", user_msg)

    def test_compress_failure_preserves_state(self):
        """LLM 调用失败时原有状态不变"""
        self.add_events(6)
        original_summary = self.memory._summary
        original_len = len(self.memory._short_term)

        mock_client = MagicMock()
        mock_client.logger = None
        mock_client.call_json = AsyncMock(side_effect=Exception("API error"))

        asyncio.run(self.memory.compress(mock_client))

        self.assertEqual(self.memory._summary, original_summary)
        self.assertEqual(len(self.memory._short_term), original_len)

    def test_compress_raw_fallback_when_no_json(self):
        """LLM 未返回 JSON 时，用原文作为摘要兜底"""
        from llm.client import JSONResult

        self.add_events(6)

        mock_client = MagicMock()
        mock_client.logger = None
        mock_client.call_json = AsyncMock(return_value=JSONResult(
            data=None, raw="测试摘要文本"
        ))

        asyncio.run(self.memory.compress(mock_client))

        self.assertEqual(self.memory._summary, "测试摘要文本")
        self.assertEqual(len(self.memory._short_term), 3)

    def test_compress_api_none_preserves_state(self):
        """call_json 返回 None（API 失败）时状态不变"""
        self.add_events(6)
        original_summary = self.memory._summary
        original_len = len(self.memory._short_term)

        mock_client = MagicMock()
        mock_client.logger = None
        mock_client.call_json = AsyncMock(return_value=None)

        asyncio.run(self.memory.compress(mock_client))

        self.assertEqual(self.memory._summary, original_summary)
        self.assertEqual(len(self.memory._short_term), original_len)


class TestActionRegistryNoneGuard(unittest.TestCase):
    """llm/client.py 中 action_registry=None 的防护"""

    def test_call_with_none_registry_returns_none(self):
        """action_registry=None 时 call 应返回 (None, None)"""
        config = {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "test_key",
            "response_mode": "tool_call",
        }
        from llm.client import LLMClient
        client = LLMClient(config, logger=None)

        import asyncio
        text, action = asyncio.run(client.call(
            system_prompt="test",
            messages=[{"role": "user", "content": "hi"}],
            action_registry=None,
            agent_name="Test",
            tick=1,
        ))
        self.assertIsNone(text)
        self.assertIsNone(action)

    def test_call_with_none_in_text_mode(self):
        """text_parse 模式下 action_registry=None 也应防护"""
        config = {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "test_key",
            "response_mode": "text_parse",
        }
        from llm.client import LLMClient
        client = LLMClient(config, logger=None)

        import asyncio
        text, action = asyncio.run(client.call(
            system_prompt="test",
            messages=[{"role": "user", "content": "hi"}],
            action_registry=None,
            agent_name="Test",
            tick=1,
        ))
        self.assertIsNone(text)
        self.assertIsNone(action)


if __name__ == "__main__":
    unittest.main()
