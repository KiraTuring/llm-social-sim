"""测试 Agent 感知和思考流程。"""

import asyncio
import os
import pytest
from dotenv import load_dotenv

from core.agent import Agent
from core.action import ActionSpec, ActionRegistry
from core.message import BROADCAST
from core.world import WorldState
from llm.client import LLMClient
from memory.memory import AgentMemory

load_dotenv()


class TestSpeakAction(ActionSpec):
    name = "speak"
    description = "说话"
    text_format = "[ACTION]speak[/ACTION]\n[CONTENT]{内容}[/CONTENT]\n[THOUGHT]{内心独白}[/THOUGHT]"

    def execute(self, agent_name, params, world):
        from core.message import Message

        target = params.get("target", BROADCAST)
        content = params.get("content", "")
        recipients = [BROADCAST] if target == BROADCAST else [target]

        msg = Message(sender=agent_name, recipients=recipients, content=content, msg_type="speech", tick=world.tick)
        world.message_bus.send(msg)
        return [msg]


@pytest.mark.llm
async def test_agent():
    """测试 Agent perceive → think → act 流程"""

    llm_config = {
        "provider": "deepseek",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": os.getenv("DEEPSEEK_API_KEY"),
        "response_mode": "text_parse",
    }

    world = WorldState(tick=1, locations=["酒馆"])
    world.message_bus = None

    from core.message import MessageBus

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
    )

    world.agents[agent.name] = agent

    llm = LLMClient(llm_config)

    print("测试 Agent 感知和思考")
    print("=" * 50)

    context = await agent.perceive(world)
    print(f"[感知结果]\n{context[:200]}...")

    action = await agent.think(llm, registry, context)
    print("\n[思考结果]")
    print(f"  Action: {action.action_type}")
    print(f"  Content: {action.content[:50]}...")
    print(f"  Internal: {action.internal_monologue[:50]}...")

    print("\n测试完成 ✅")


if __name__ == "__main__":
    asyncio.run(test_agent())
