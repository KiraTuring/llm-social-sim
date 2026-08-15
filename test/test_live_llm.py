#!/usr/bin/env python3
"""真实 LLM 集成测试（需 .env 的 DEEPSEEK_API_KEY）：模型解析、Agent 流程。

默认被 pytest.ini 的 `-m "not llm"` 跳过，需 `pytest -m llm` 显式运行。
"""

import asyncio
import os

import pytest
from dotenv import load_dotenv

from core.action import ActionSpec, ActionRegistry
from core.agent import Agent
from core.logger import SimLogger
from core.message import BROADCAST, Message, MessageBus
from core.world import WorldState
from llm.client import LLMClient
from memory.memory import AgentMemory

load_dotenv()


class TestSpeakAction(ActionSpec):
    """真实调用用的最小 speak Action（text_parse 解析目标）。"""

    name = "speak"
    description = "说话"
    text_format = "[ACTION]speak[/ACTION]\n[CONTENT]{内容}[/CONTENT]\n[THOUGHT]{内心独白}[/THOUGHT]"

    def execute(self, agent_name, params, world):
        target = params.get("target", BROADCAST)
        content = params.get("content", "")
        recipients = [BROADCAST] if target == BROADCAST else [target]
        msg = Message(
            sender=agent_name, recipients=recipients, content=content,
            msg_type="speech", tick=world.tick,
        )
        world.message_bus.send(msg)
        return [msg]


def _llm_config() -> dict:
    return {
        "provider": "deepseek",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": os.getenv("DEEPSEEK_API_KEY"),
        "response_mode": "text_parse",
    }


def _require_api_key() -> None:
    if not os.getenv("DEEPSEEK_API_KEY"):
        pytest.skip("缺少 DEEPSEEK_API_KEY，跳过真实 LLM 测试")


@pytest.mark.llm
async def test_model_text_parse_and_concurrent():
    """text_parse 模式：单次解析 + 3 个并发调用都能解析出 Action"""
    _require_api_key()
    registry = ActionRegistry()
    registry.register(TestSpeakAction())
    logger = SimLogger(log_file="logs/test.log", level=10)
    client = LLMClient(_llm_config(), logger)
    try:
        test_messages = [{"role": "user", "content": "你觉得今晚天气怎么样？"}]
        _, action = await client.call(
            system_prompt="你是一个测试 Agent。",
            messages=test_messages,
            action_registry=registry,
            agent_name="TestAgent",
            tick=1,
        )
        assert action is not None, "基础调用未解析出 Action"

        results = await asyncio.gather(*[
            client.call(
                system_prompt=f"你是一个测试 Agent {i}。",
                messages=test_messages,
                action_registry=registry,
                agent_name=f"TestAgent{i}",
                tick=i + 2,
            )
            for i in range(3)
        ])
        assert all(action is not None for _, action in results), "并发调用存在解析失败"
    finally:
        logger.close()


@pytest.mark.llm
async def test_agent_perceive_think_flow():
    """Agent perceive → think 真实调用：能产出 Action"""
    _require_api_key()
    world = WorldState(tick=1, locations=["酒馆"])
    world.message_bus = MessageBus()

    registry = ActionRegistry()
    registry.register(TestSpeakAction())

    memory = AgentMemory(name="老巴克", short_limit=10, compress_threshold=15)
    agent = Agent(
        name="老巴克",
        role="酒馆老板",
        personality="圆滑世故，消息灵通，只想安稳做生意",
        goal="今晚别出乱子，把酒卖出去",
        location="酒馆",
        relationships={},
        memory=memory,
        registry=registry,
    )
    world.agents[agent.name] = agent

    llm = LLMClient(_llm_config())
    context = await agent.perceive(world)
    action = await agent.think(llm, context)
    assert action is not None, "think 未返回 Action"
    assert action.action_type, "think 返回了空 action_type"
