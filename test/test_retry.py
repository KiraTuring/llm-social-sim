#!/usr/bin/env python3
"""测试 LLM retry 机制：无 tool call + 不合法工具/参数时是否正确重试。"""

import asyncio
import unittest
from unittest.mock import patch

from conftest import OFFLINE_CONFIG as CONFIG, make_response as _make_response, make_multi_response as _make_multi_response
from llm.client import LLMClient
from app.factory import create_agent, create_gm
from core.action import ActionRegistry
from actions.common import SpeakAction, ObserveAction, MoveAction
from core.gm import GMAgent


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
            "adjacent_locations": [loc for loc in self.locations if loc != "吧台"],
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

    async def _call_multi(self, response):
        call_count = 0

        async def fake_acompletion(**kwargs):
            nonlocal call_count
            call_count += 1
            return response

        with patch("litellm.acompletion", new=fake_acompletion):
            _, actions = await self.client.call_multi(
                system_prompt="测试",
                messages=[{"role": "user", "content": "hello"}],
                action_registry=self.registry,
                agent_name="GM",
                tick=1,
                allow_no_tool=True,
            )
            return actions, call_count

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

    # ── 不合法的工具/参数：重试一次后成功 ──

    def test_invalid_then_success(self):
        """非法工具/参数（不存在、对自己、原地、不存在的目标）→ 重试一次后成功"""
        cases = [
            ("fly", '{"internal_monologue": "测试"}', "observe", '{"internal_monologue": "测试"}', "observe", None),
            ("speak", '{"target": "TestAgent", "content": "hi"}', "speak", '{"target": "张三", "content": "hi"}', "speak", "张三"),
            ("move", '{"target": "吧台", "content": "go"}', "move", '{"target": "主厅", "content": "go"}', "move", "主厅"),
            ("speak", '{"target": "王五", "content": "hi"}', "speak", '{"target": "张三", "content": "hi"}', "speak", "张三"),
            ("move", '{"target": "后院", "content": "go"}', "move", '{"target": "主厅", "content": "go"}', "move", "主厅"),
        ]
        for bad_func, bad_args, good_func, good_args, exp_type, exp_target in cases:
            with self.subTest(bad_func=bad_func, bad_args=bad_args):
                action, calls = asyncio.run(
                    self._run(
                        _make_response("", tool_call=True, func_name=bad_func, func_args=bad_args),
                        _make_response("", tool_call=True, func_name=good_func, func_args=good_args),
                        None,
                        validation_context=self.base_context,
                    )
                )
                self.assertIsNotNone(action)
                self.assertEqual(action.action_type, exp_type)
                self.assertEqual(action.target, exp_target)
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

    def test_agent_multi_tool_discards_extra(self):
        """Agent 一次返回多个工具 → 只保留第一个，多余显式丢弃，不触发重试"""
        action, calls = asyncio.run(
            self._run(
                _make_multi_response([
                    ("observe", '{"internal_monologue": "a"}'),
                    ("speak", '{"target": "张三", "content": "hi"}'),
                ]),
                None, None,
            )
        )
        self.assertIsNotNone(action)
        self.assertEqual(action.action_type, "observe")
        self.assertEqual(calls, 1)
        self.assertEqual(len(action.raw_tool_calls), 1)

    def test_gm_multi_tool_keeps_all(self):
        """call_multi 不设 limit_tools（GM 路径）→ 全部工具都返回"""
        actions, calls = asyncio.run(
            self._call_multi(
                _make_multi_response([
                    ("observe", '{"internal_monologue": "a"}'),
                    ("observe", '{"internal_monologue": "b"}'),
                ]),
            )
        )
        self.assertEqual(len(actions), 2)
        self.assertEqual(calls, 1)

    async def _run_partial_execution(self, response):
        """返回 (actions, call_count, executed, messages)，模拟 GM 工具执行。"""
        executed = []
        messages = [{"role": "user", "content": "hello"}]
        call_count = 0

        async def fake_acompletion(**kwargs):
            nonlocal call_count
            call_count += 1
            return response

        with patch("litellm.acompletion", new=fake_acompletion):
            _, actions = await self.client.call_multi(
                system_prompt="测试",
                messages=messages,
                action_registry=self.registry,
                agent_name="GM",
                tick=1,
                allow_no_tool=True,
                execute_action=lambda action: executed.append(action.action_type) or f"执行了{action.action_type}",
            )
        return actions, call_count, executed, messages

    def test_gm_partial_execution_does_not_retry(self):
        """同一响应中第 2 个工具校验失败：已执行工具不重试，错误工具不执行，消息配对完整"""
        actions, calls, executed, messages = asyncio.run(
            self._run_partial_execution(
                _make_multi_response([
                    ("observe", '{"internal_monologue": "a"}'),
                    ("speak", '{"target": "不存在", "content": "hi"}'),
                ]),
            )
        )

        # 不再整体重试：只调用一次 LLM
        self.assertEqual(calls, 1)
        # 只返回已执行成功的工具
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].action_type, "observe")
        # 已执行工具恰好执行一次，非法工具未执行
        self.assertEqual(executed, ["observe"])
        # 消息配对完整：assistant 声明 2 个 tool_call_id，2 个 tool 消息分别配对
        assistant = [m for m in messages if m.get("role") == "assistant" and m.get("tool_calls")]
        self.assertEqual(len(assistant), 1)
        tool_ids = [tc["id"] for tc in assistant[0]["tool_calls"]]
        self.assertEqual(len(tool_ids), 2)
        tool_messages = [m for m in messages if m.get("role") == "tool"]
        self.assertEqual({m["tool_call_id"] for m in tool_messages}, set(tool_ids))
        self.assertTrue(any("执行了observe" in m["content"] for m in tool_messages))
        self.assertTrue(any("不存在" in m["content"] for m in tool_messages))


class TestGMChainTools(unittest.TestCase):
    """GM 同 tick 链式工具：npc_add 后 npc_speak 必须能看到新 NPC（校验上下文实时刷新）。"""

    def setUp(self):
        from scenarios._test import _TestScene
        from core.agent import Agent

        self.scene = _TestScene()
        self.world = self.scene.init_world()
        self.registry = ActionRegistry()
        self.scene.setup(self.registry)
        for cfg in self.scene.agents:
            self.world.agents[cfg["name"]] = create_agent(
                self.scene, cfg, CONFIG, registry=self.registry
            )

        self.gm_registry = ActionRegistry(include_agent_params=False)
        self.scene.setup_gm(self.gm_registry)
        self.gm = create_gm(self.scene, CONFIG, self.gm_registry)
        self.client = LLMClient(CONFIG["llm"], logger=None)

    async def _run_chain(self, response):
        async def fake_acompletion(**kwargs):
            return response

        with patch("litellm.acompletion", new=fake_acompletion):
            await self.gm._generate_llm_event(self.world, self.client)

    def test_npc_add_then_speak_same_response(self):
        """同一响应内 [npc_add, npc_speak]：speak 校验必须看到刚添加的 NPC"""
        asyncio.run(self._run_chain(
            _make_multi_response([
                ("npc_add", '{"npc_name": "流浪汉", "location": "大厅"}'),
                ("npc_speak", '{"npc_name": "流浪汉", "content": "求口吃的"}'),
            ]),
        ))
        self.assertIn("流浪汉", self.world.npcs)
        self.assertIn("流浪汉", self.world.npc_names)
        self.assertNotIn("不是 NPC", "\n".join(self.world.event_log_texts()))

    def test_npc_add_then_speak_across_turns(self):
        """跨 turn：turn0 npc_add，turn1 npc_speak（ReAct 循环内校验上下文逐 turn 重建）"""
        responses = [
            _make_multi_response([("npc_add", '{"npc_name": "吟游诗人", "location": "花园"}')]),
            _make_multi_response([("npc_speak", '{"npc_name": "吟游诗人", "content": "唱首歌"}')]),
            _make_multi_response([("npc_move", '{"npc_name": "吟游诗人", "target": "书房"}')]),
        ]
        call_count = 0

        async def fake_acompletion(**kwargs):
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return resp

        async def run():
            with patch("litellm.acompletion", new=fake_acompletion):
                await self.gm._generate_llm_event(self.world, self.client)

        asyncio.run(run())
        self.assertIn("吟游诗人", self.world.npcs)
        self.assertEqual(self.world.npcs["吟游诗人"].location, "书房")
        self.assertNotIn("不是 NPC", "\n".join(self.world.event_log_texts()))

    def test_chat_second_trigger_tool_pairing(self):
        """chat 模式：一次响应多个工具后，_gm_history 必须保持 assistant(tool_calls)/tool 配对，
        第二次触发不抛 BadRequestError（回归：assistant 声明 id1+id2 却只有 tool(id1) 跟随）。"""
        chat_config = dict(CONFIG)
        chat_config["gm"] = dict(CONFIG["gm"])
        chat_config["gm"]["prompt_format"] = "chat"
        from scenarios._test import _TestScene
        from core.agent import Agent

        scene = _TestScene()
        world = scene.init_world()
        agent_registry = ActionRegistry()
        scene.setup(agent_registry)
        for cfg in scene.agents:
            world.agents[cfg["name"]] = create_agent(
                scene, cfg, chat_config, registry=agent_registry
            )
        reg = ActionRegistry(include_agent_params=False)
        scene.setup_gm(reg)
        gm = create_gm(scene, chat_config, reg)
        client = LLMClient(chat_config["llm"], logger=None)

        responses = [
            _make_multi_response([
                ("npc_add", '{"npc_name": "货郎", "location": "大厅"}'),
                ("npc_speak", '{"npc_name": "货郎", "content": "卖货了"}'),
            ]),
            _make_multi_response([
                ("npc_move", '{"npc_name": "货郎", "target": "花园"}'),
            ]),
        ]
        call_count = 0

        def assert_pairing(messages):
            """校验 assistant(tool_calls) 声明的每个 id 都被紧随的 tool 消息配对。"""
            pending = []
            for m in messages:
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    pending = [tc["id"] for tc in m["tool_calls"]]
                elif m.get("role") == "tool":
                    if m.get("tool_call_id") not in pending:
                        raise AssertionError(f"tool_call_id {m.get('tool_call_id')} 未配对")
                    pending.remove(m.get("tool_call_id"))
            self.assertEqual(pending, [], f"未配对的 tool_call_id: {pending}")

        async def fake_acompletion(**kwargs):
            nonlocal call_count
            assert_pairing(kwargs["messages"])
            resp = responses[call_count]
            call_count += 1
            return resp

        async def run():
            with patch("litellm.acompletion", new=fake_acompletion):
                await gm._generate_llm_event(world, client)
                await gm._generate_llm_event(world, client)

        asyncio.run(run())
        self.assertIn("货郎", world.npcs)
        self.assertEqual(world.npcs["货郎"].location, "花园")
        assert_pairing(gm._gm_history)

    def test_truncate_history_keeps_pairing(self):
        """_truncate_gm_history：截断落在 assistant(tool_calls)/tool 之间时，跳过孤立 tool 消息，
        不残留未配对序列。"""
        from scenarios._test import _TestScene

        scene = _TestScene()
        cfg = dict(CONFIG)
        cfg["gm"] = dict(CONFIG["gm"])
        cfg["gm"]["prompt_format"] = "chat"
        cfg["gm"]["chat_history_max_messages"] = 3
        reg = ActionRegistry(include_agent_params=False)
        scene.setup_gm(reg)
        gm = create_gm(scene, cfg, reg)

        def tc_msg(call_id, tool_id):
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": call_id, "type": "function",
                                "function": {"name": "npc_speak", "arguments": "{}"}}],
            }, {"role": "tool", "tool_call_id": tool_id, "content": "ok"}

        history = [{"role": "user", "content": "u1"}]
        # 构造：u1 + 一组 tool_calls（assistant+tool+tool），共 3 条 + 一条新 user 触发截断
        a, t1 = tc_msg("c1", "c1")
        history += [a, t1, {"role": "tool", "tool_call_id": "c1b", "content": "ok2"},
                    {"role": "user", "content": "u2"}]
        gm._gm_history = history
        gm._truncate_gm_history()
        # 截断后头部不能是孤立 tool 消息（其 assistant 已被切掉）
        self.assertNotEqual(gm._gm_history[0].get("role"), "tool")
        # 若存在 assistant(tool_calls)，其后必须完整配对
        pending = []
        for m in gm._gm_history:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                pending = [tc["id"] for tc in m["tool_calls"]]
            elif m.get("role") == "tool":
                if m.get("tool_call_id") in pending:
                    pending.remove(m.get("tool_call_id"))
        self.assertEqual(pending, [])


class TestParseText(unittest.TestCase):
    """parse_text：缺 [ACTION] 标签返回 None，不再静默回退为 speak。"""

    def setUp(self):
        self.registry = ActionRegistry()
        self.registry.register(ObserveAction())

    def test_missing_action_returns_none(self):
        """完全没有 [ACTION] 标签 → None（调用方走重试/兜底）"""
        self.assertIsNone(self.registry.parse_text("随便说点什么"))

    def test_with_action_tag_parses(self):
        """含 [ACTION] 标签 → 正常解析"""
        action = self.registry.parse_text(
            "[ACTION]observe[/ACTION][THOUGHT]看看[/THOUGHT]"
        )
        self.assertIsNotNone(action)
        self.assertEqual(action.action_type, "observe")
        self.assertEqual(action.internal_monologue, "看看")


if __name__ == "__main__":
    unittest.main()
