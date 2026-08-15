#!/usr/bin/env python3
"""测试模型能力：验证 Action 解析是否稳定。"""

import asyncio
import os
from dotenv import load_dotenv
from llm.client import LLMClient
from core.action import ActionSpec, ActionRegistry

load_dotenv()


class TestSpeakAction(ActionSpec):
    name = "speak"
    description = "说话"
    text_format = "[ACTION]speak[/ACTION]\n[CONTENT]{内容}[/CONTENT]\n[THOUGHT]{内心独白}[/THOUGHT]"

    def execute(self, agent_name: str, params: dict, world):
        return []


async def test_model():
    """测试模型的 Action 解析能力"""

    test_config = {
        "provider": "deepseek",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": os.getenv("DEEPSEEK_API_KEY"),
        "response_mode": "text_parse",
    }

    registry = ActionRegistry()
    registry.register(TestSpeakAction())

    from core.logger import SimLogger
    logger = SimLogger(log_file="logs/test.log", level=10)

    client = LLMClient(test_config, logger)

    if not client.api_key:
        print("❌ 请先设置 DEEPSEEK_API_KEY")
        return

    test_messages = [{"role": "user", "content": "你觉得今晚天气怎么样？"}]

    print("测试 1: 基础调用（text_parse 模式）")
    print("-" * 50)

    text, action = await client.call(
        system_prompt="你是一个测试 Agent。",
        messages=test_messages,
        action_registry=registry,
        agent_name="TestAgent",
        tick=1,
    )

    print(f"原始输出: {text[:100] if text else 'N/A'}...")
    if action:
        print(f"✅ 解析成功: action_type={action.action_type}, content={action.content[:30]}...")
        print(f"   内心独白: {action.internal_monologue[:30]}...")
    else:
        print("❌ 解析失败")

    print("\n测试 2: 并发调用（3个 Agent）")
    print("-" * 50)

    start = asyncio.get_event_loop().time()
    tasks = [
        client.call(
            system_prompt=f"你是一个测试 Agent {i}。",
            messages=test_messages,
            action_registry=registry,
            agent_name=f"TestAgent{i}",
            tick=i+2,
        )
        for i in range(3)
    ]

    results = await asyncio.gather(*tasks)
    elapsed = asyncio.get_event_loop().time() - start

    for i, (text, action) in enumerate(results):
        if action:
            print(f"✅ Agent {i}: action_type={action.action_type}, content={action.content[:30]}...")
        else:
            print(f"❌ Agent {i}: 解析失败")
            print(f"   原始输出: {text[:100] if text else 'N/A'}...")

    print(f"\n并发调用耗时: {elapsed:.2f}s")

    logger.close()


if __name__ == "__main__":
    asyncio.run(test_model())
