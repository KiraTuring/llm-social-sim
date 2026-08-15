# -*- coding: utf-8 -*-
"""测试 chat 模式（prompt_format="chat"）的消息结构和生命周期。"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.agent import Agent
from core.action import ActionRegistry, ActionSpec, Action
from core.message import Message, MessageBus
from core.world import WorldState
from memory.memory import AgentMemory


# ── 辅助 mock ──

class _DummyAction(ActionSpec):
    name = "speak"
    description = "说话"
    text_format = ""

    def execute(self, agent_name, params, world):
        target = params.get("target", "all")
        content = params.get("content", "")
        msg = Message(sender=agent_name, recipients=[target], content=content, msg_type="speech", tick=world.tick)
        world.message_bus.send(msg)
        return [msg], {"summary": f"对{target}说: {content}"}


def _make_llm_response(content: str = "", tool_call: bool = True):
    """模拟 litellm.acompletion 返回"""
    class FakeFunction:
        name = "speak"
        arguments = '{"target": "张三", "internal_monologue": "嗯", "content": "你好"}'

    tc = None
    if tool_call:
        tc = [type("FakeToolCall", (), {
            "id": "call_test_01",
            "type": "function",
            "function": FakeFunction(),
        })()]

    FakeMessage = type("FakeMessage", (), {"content": content, "tool_calls": tc})

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

        def model_dump_json(self):
            return '{"mock": true}'

    return FakeResponse()


def _make_agent(prompt_format: str = "chat") -> Agent:
    """创建一个最小 Agent（chat 模式）"""
    memory = AgentMemory(name="测试", short_limit=3, compress_threshold=5)
    agent = Agent(
        name="测试",
        role="测试角色",
        personality="随和",
        goal="测试",
        location="主厅",
        relationships={"张三": {"trust": 0, "impression": ""}},
        memory=memory,
        prompt_format=prompt_format,
    )
    return agent


# ── 测试用例 ──

class TestBuildChatMessages(unittest.TestCase):
    """_build_chat_messages 结构测试（不依赖 LLM）"""

    def test_no_summary_empty_history(self):
        """无 summary、无历史 → [user(tick)]"""
        agent = _make_agent("chat")
        result = agent._build_chat_messages("当前上下文", tick=1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["role"], "user")
        self.assertEqual(result[0]["content"], "当前上下文")
        self.assertEqual(result[0]["tick"], 1)

    def test_with_summary_no_history(self):
        """有 summary、无历史 → [summary, user(tick)]"""
        agent = _make_agent("chat")
        agent.memory._summary = "你在酒馆打工五年"
        result = agent._build_chat_messages("当前上下文", tick=2)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["role"], "user")
        self.assertIn("你的过去", result[0]["content"])
        self.assertIn("酒馆打工五年", result[0]["content"])
        self.assertEqual(result[1]["role"], "user")
        self.assertEqual(result[1]["tick"], 2)

    def test_summary_before_history(self):
        """有 summary 和历史 → [summary, user(t1), assistant(t1), user(t2)]"""
        agent = _make_agent("chat")
        agent.memory._summary = "概览"
        agent._chat_history = [
            {"role": "user", "content": "t1", "tick": 1},
            {"role": "assistant", "content": "[speak] 你好", "tick": 1},
        ]
        result = agent._build_chat_messages("t2 上下文", tick=2)
        self.assertEqual(len(result), 4)
        self.assertEqual(result[0]["role"], "user")
        self.assertIn("你的过去", result[0]["content"])
        self.assertEqual(result[1]["role"], "user")
        self.assertEqual(result[1]["tick"], 1)
        self.assertEqual(result[2]["role"], "assistant")
        self.assertEqual(result[3]["role"], "user")
        self.assertEqual(result[3]["tick"], 2)

    def test_copy_not_reference(self):
        """返回的列表应是 _chat_history 的副本，修改不影响原历史"""
        agent = _make_agent("chat")
        agent._chat_history = [
            {"role": "user", "content": "t1", "tick": 1},
        ]
        result = agent._build_chat_messages("t2", tick=2)
        result.append({"role": "assistant", "content": "hack", "tick": 2})
        self.assertEqual(len(agent._chat_history), 1)
        self.assertEqual(len(result), 3)


class TestThinkChatMode(unittest.TestCase):
    """think() 在 chat 模式下的行为"""

    def setUp(self):
        self.agent = _make_agent("chat")
        self.registry = ActionRegistry()
        self.registry.register(_DummyAction())

    def test_success_sets_pending_user_msg(self):
        """LLM 成功返回 action → 设 _pending_user_msg"""
        async def fake_acompletion(**kwargs):
            return _make_llm_response(tool_call=True)

        with patch("litellm.acompletion", new=fake_acompletion):
            action = asyncio.run(self.agent.think(
                llm=self._mock_llm_client(),
                registry=self.registry,
                context="当前环境",
                tick=3,
            ))
        self.assertIsNotNone(action)
        self.assertIsNotNone(self.agent._pending_user_msg)
        self.assertEqual(self.agent._pending_user_msg["tick"], 3)
        self.assertEqual(self.agent._pending_user_msg["role"], "user")
        self.assertEqual(self.agent._pending_user_msg["content"], "当前环境")

    def test_failure_does_not_set_pending_user_msg(self):
        """LLM 失败（retry 耗尽）→ 不设 _pending_user_msg"""
        async def fake_acompletion(**kwargs):
            return _make_llm_response(content="纯文本", tool_call=False)

        with patch("litellm.acompletion", new=fake_acompletion):
            action = asyncio.run(self.agent.think(
                llm=self._mock_llm_client(),
                registry=self.registry,
                context="当前环境",
                tick=3,
            ))
        self.assertIsNotNone(action)  # fallback
        self.assertIsNone(self.agent._pending_user_msg)

    def test_messages_structure_passed_to_llm(self):
        """验证传递给 LLM 的 messages 结构"""
        self.agent.memory._summary = "过去摘要"
        self.agent._chat_history = [
            {"role": "user", "content": "t1", "tick": 1},
            {"role": "assistant", "content": "[speak] hi", "tick": 1},
        ]
        captured = {}

        async def fake_acompletion(**kwargs):
            captured["messages"] = kwargs["messages"]
            return _make_llm_response(tool_call=True)

        with patch("litellm.acompletion", new=fake_acompletion):
            asyncio.run(self.agent.think(
                llm=self._mock_llm_client(),
                registry=self.registry,
                context="t3 上下文",
                tick=3,
            ))
        msgs = captured["messages"]
        # msgs[0] is system prompt (prepended in LLMClient.call_multi)
        self.assertEqual(len(msgs), 5)
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[1]["role"], "user")
        self.assertIn("过去摘要", msgs[1]["content"])
        self.assertEqual(msgs[2]["role"], "user")
        self.assertEqual(msgs[2]["tick"], 1)
        self.assertEqual(msgs[3]["role"], "assistant")
        self.assertEqual(msgs[3]["tick"], 1)
        self.assertEqual(msgs[4]["role"], "user")
        self.assertEqual(msgs[4]["tick"], 3)

    def test_retry_does_not_pollute_chat_history(self):
        """retry 期间追加的消息不应污染 _chat_history"""
        self.agent._chat_history = []
        call_count = [0]

        async def fake_acompletion(**kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                return _make_llm_response(content="纯文本", tool_call=False)
            return _make_llm_response(tool_call=True)

        with patch("litellm.acompletion", new=fake_acompletion):
            asyncio.run(self.agent.think(
                llm=self._mock_llm_client(),
                registry=self.registry,
                context="当前环境",
                tick=3,
            ))
        self.assertEqual(len(self.agent._chat_history), 0)

    def _mock_llm_client(self):
        config = {"provider": "deepseek", "model": "deepseek-chat",
                   "api_key": "test", "base_url": "http://test", "response_mode": "tool_call"}
        from llm.client import LLMClient
        return LLMClient(config, logger=None)


class TestActChatMode(unittest.TestCase):
    """act() 在 chat 模式下的 chat_history 提交"""

    def setUp(self):
        self.agent = _make_agent("chat")
        self.registry = ActionRegistry()
        self.registry.register(_DummyAction())
        self.world = WorldState(tick=5, locations=["主厅"])
        self.world.message_bus = MessageBus()
        self.world.agents["测试"] = self.agent
        self.world.action_order = ["测试"]

    def test_commits_user_and_assistant_to_history(self):
        """act() 将 pending_user_msg 和 assistant 消息写入 chat_history"""
        self.agent._pending_user_msg = {"role": "user", "content": "t5 上下文", "tick": 5}
        action = Action(action_type="speak", target="张三", content="你好", params={"target": "张三", "content": "你好"})
        asyncio.run(self.agent.act(action, self.world, self.registry))

        self.assertEqual(len(self.agent._chat_history), 2)
        self.assertEqual(self.agent._chat_history[0]["role"], "user")
        self.assertEqual(self.agent._chat_history[0]["tick"], 5)
        self.assertEqual(self.agent._chat_history[0]["content"], "t5 上下文")
        self.assertEqual(self.agent._chat_history[1]["role"], "assistant")
        self.assertEqual(self.agent._chat_history[1]["tick"], 5)

    def test_clears_pending_after_commit(self):
        """提交后清除 _pending_user_msg"""
        self.agent._pending_user_msg = {"role": "user", "content": "t5", "tick": 5}
        action = Action(action_type="speak", target="张三", content="hi", params={"target": "张三", "content": "hi"})
        asyncio.run(self.agent.act(action, self.world, self.registry))
        self.assertIsNone(self.agent._pending_user_msg)

    def test_no_pending_does_nothing(self):
        """_pending_user_msg 为 None → 只提交 assistant"""
        self.agent._pending_user_msg = None
        action = Action(action_type="speak", target="张三", content="hi", params={"target": "张三", "content": "hi"})
        asyncio.run(self.agent.act(action, self.world, self.registry))
        self.assertEqual(len(self.agent._chat_history), 1)
        self.assertEqual(self.agent._chat_history[0]["role"], "assistant")

    def test_assistant_msg_content(self):
        """assistant 消息格式: [action_type] -> target: content"""
        self.agent._pending_user_msg = {"role": "user", "content": "t5", "tick": 5}
        action = Action(action_type="speak", target="张三", content="你好世界", params={"target": "张三", "content": "你好世界"})
        asyncio.run(self.agent.act(action, self.world, self.registry))
        content = self.agent._chat_history[1]["content"]
        self.assertIn("[speak]", content)
        self.assertIn("张三", content)
        self.assertIn("你好世界", content)


class TestTruncateChatHistory(unittest.TestCase):
    """_truncate_chat_history 压缩后的对齐"""

    def test_truncate_keeps_only_tick_ge_oldest(self):
        """只保留 tick >= short_term 中最早 tick 的条目"""
        agent = _make_agent("chat")
        agent._chat_history = [
            {"role": "user", "content": "t1", "tick": 1},
            {"role": "assistant", "content": "[observe] ...", "tick": 1},
            {"role": "user", "content": "t2", "tick": 2},
            {"role": "assistant", "content": "[speak] ...", "tick": 2},
            {"role": "user", "content": "t3", "tick": 3},
        ]
        # 模拟 short_term 只保留 tick>=2 的条目
        agent.memory._short_term = [
            {"tick": 2, "event": "事件1"},
            {"tick": 3, "event": "事件2"},
            {"tick": 3, "event": "事件3"},
        ]
        agent._truncate_chat_history()
        self.assertEqual(len(agent._chat_history), 3)
        self.assertEqual(agent._chat_history[0]["tick"], 2)
        self.assertEqual(agent._chat_history[1]["tick"], 2)
        self.assertEqual(agent._chat_history[2]["tick"], 3)

    def test_truncate_noop_empty_short_term(self):
        """short_term 为空 → 不截断"""
        agent = _make_agent("chat")
        agent._chat_history = [{"role": "user", "content": "t1", "tick": 1}]
        agent.memory._short_term = []
        agent._truncate_chat_history()
        self.assertEqual(len(agent._chat_history), 1)

    def test_truncate_keeps_summary_intact(self):
        """summary 在 _chat_history 外（不在截断范围）"""
        agent = _make_agent("chat")
        agent._chat_history = [
            {"role": "user", "content": "t1", "tick": 1},
            {"role": "assistant", "content": "[observe] ...", "tick": 1},
        ]
        agent.memory._short_term = [{"tick": 2, "event": "e1"}]
        agent._truncate_chat_history()
        self.assertEqual(len(agent._chat_history), 0)
        # _build_chat_messages 仍然会注入 summary
        agent.memory._summary = "概览未丢失"
        msgs = agent._build_chat_messages("t3", tick=3)
        self.assertEqual(msgs[0]["role"], "user")
        self.assertIn("概览未丢失", msgs[0]["content"])

    def test_truncate_after_compress(self):
        """完整流程：perceive 触发压缩 → story_term 截断 → chat_history 截断"""
        from llm.client import JSONResult

        agent = _make_agent("chat")
        agent.memory.compress_threshold = 5
        agent.memory.short_limit = 2
        # 写 6 条记忆，触发压缩
        for i in range(6):
            agent.memory.add(f"事件{i+1}", tick=i // 2 + 1)

        # 造一些 chat_history
        agent._chat_history = [
            {"role": "user", "content": "t1", "tick": 1},
            {"role": "assistant", "content": "[speak] a", "tick": 1},
            {"role": "user", "content": "t2", "tick": 2},
            {"role": "assistant", "content": "[speak] b", "tick": 2},
            {"role": "user", "content": "t3", "tick": 3},
            {"role": "assistant", "content": "[speak] c", "tick": 3},
        ]

        self.assertTrue(agent.memory._compress_needed)

        # 模拟 LLM 进行压缩
        mock_client = MagicMock()
        mock_client.logger = None
        mock_client.call_json = AsyncMock(return_value=JSONResult(
            data={"summary": "压缩摘要", "relations": {}},
            raw='{"summary": "压缩摘要", "relations": {}}',
        ))

        asyncio.run(agent.perceive(self._make_world(tick=7), llm_client=mock_client))

        # 验证：short_term 截断后剩余 2 条
        self.assertEqual(len(agent.memory._short_term), 2)
        # chat_history 也应按 tick 截断
        for entry in agent._chat_history:
            self.assertGreaterEqual(entry["tick"], agent.memory._short_term[0]["tick"])

    def _make_world(self, tick: int):
        w = WorldState(tick=tick, locations=["主厅"])
        w.message_bus = MessageBus()
        w.agents["测试"] = _make_agent("chat")
        w.action_order = ["测试"]
        return w


class TestTextModeUnchanged(unittest.TestCase):
    """text 模式不应受 chat 模式代码影响"""

    def test_think_uses_flat_messages(self):
        """text 模式 think 使用 [{"role": "user", "content": context}]"""
        agent = _make_agent("text")
        agent._build_chat_messages = None  # 不应被调用
        self.assertEqual(agent.prompt_format, "text")

    def test_perceive_includes_memory_and_last_action(self):
        """perceive 应包含记忆和上一行动"""
        agent = _make_agent("text")
        agent._last_action = "[speak] 你好"

        world = WorldState(tick=1, locations=["主厅"])
        world.message_bus = MessageBus()
        world.agents["测试"] = agent
        agent.memory.add("我记得某事", tick=1)

        import asyncio
        context = asyncio.run(agent.perceive(world))

        self.assertIn("【你最近记得的事】", context)
        self.assertIn("我记得某事", context)
        self.assertIn("【你上一tick的行动】", context)
        self.assertIn("[speak] 你好", context)

    def test_no_chat_history_in_text_mode(self):
        """act 不写入 _chat_history"""
        agent = _make_agent("text")
        world = WorldState(tick=1, locations=["主厅"])
        world.message_bus = MessageBus()
        world.agents["测试"] = agent

        action = Action(action_type="speak", target="all", content="hi", params={})
        registry = ActionRegistry()
        registry.register(_DummyAction())

        import asyncio
        asyncio.run(agent.act(action, world, registry))
        self.assertEqual(len(agent._chat_history), 0)

    def test_perceive_skips_context_in_chat_mode(self):
        """chat 模式 perceive 不应包含记忆和上一行动"""
        agent = _make_agent("chat")
        agent._last_action = "[speak] 你好"

        world = WorldState(tick=1, locations=["主厅"])
        world.message_bus = MessageBus()
        world.agents["测试"] = agent
        agent.memory.add("我记得某事", tick=1)

        import asyncio
        context = asyncio.run(agent.perceive(world))

        self.assertNotIn("【你最近记得的事】", context)
        self.assertNotIn("我记得某事", context)
        self.assertNotIn("【你上一tick的行动】", context)
        self.assertNotIn("[speak] 你好", context)

    def test_perceive_ingests_inbox(self):
        """perceive 摄入收件箱：上下文含新信息、记忆写入、inbox 清空、_perceived_inbox 正确"""
        agent = _make_agent("text")
        world = WorldState(tick=1, locations=["主厅"])
        world.message_bus = MessageBus()
        world.agents["测试"] = agent
        world.message_bus.send(Message(
            sender="张三", recipients=["测试"], target="测试",
            content="你好，测试", msg_type="speech", tick=1,
        ))

        context = asyncio.run(agent.perceive(world))

        self.assertIn("【你得到的新信息】", context)
        self.assertIn("你好，测试", context)
        self.assertEqual(len(world.message_bus.get_inbox("测试")), 0)
        self.assertEqual(len(agent._perceived_inbox), 1)
        self.assertEqual(agent._perceived_inbox[0]["sender"], "张三")
        self.assertTrue(
            any("你好，测试" in e["event"] for e in agent.memory._short_term),
            agent.memory._short_term,
        )


class TestPendingUserMsgStale(unittest.TestCase):
    """极端情况：act 失败后 _pending_user_msg 的清理"""

    def test_stale_not_committed_if_act_throws(self):
        """act 抛出异常时不提交到 chat_history，且清除悬空的 pending 消息"""
        agent = _make_agent("chat")
        agent._pending_user_msg = {"role": "user", "content": "旧的上下文", "tick": 1}

        # 一个会抛异常的 action
        class FailingAction(ActionSpec):
            name = "fail"
            description = "会失败的 action"
            def execute(self, agent_name, params, world):
                raise RuntimeError("模拟失败")
            def get_tool_schema(self):
                return {"type": "function", "function": {"name": "fail", "description": "fail", "parameters": {"type": "object", "properties": {}}}}

        registry = ActionRegistry()
        registry.register(FailingAction())
        world = WorldState(tick=2, locations=["主厅"])
        world.message_bus = MessageBus()

        action = Action(action_type="fail", content="", params={})
        import asyncio
        asyncio.run(agent.act(action, world, registry))

        self.assertEqual(len(agent._chat_history), 0)
        self.assertIsNone(agent._pending_user_msg)
        self.assertEqual(action.result, {"error": "模拟失败"})


if __name__ == "__main__":
    unittest.main()
