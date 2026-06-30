"""LLM 客户端：统一调用 DeepSeek/本地模型，支持双模式解析。"""

import asyncio
import logging
import os
from typing import Any

os.environ["LITELLM_LOG"] = "ERROR"
logging.getLogger("litellm").setLevel(logging.ERROR)

from core.action import Action, ActionRegistry


class LLMClient:
    """LLM 调用客户端"""

    def __init__(self, config: dict):
        self.provider = config["provider"]
        self.model = config["model"]
        self.base_url = config.get("base_url")
        self.api_key = config["api_key"]
        self.response_mode = config["response_mode"]

    async def call(
        self,
        system_prompt: str,
        messages: list[dict],
        action_registry: ActionRegistry,
        temperature: float = 0.7,
    ) -> tuple[str | None, Action | None]:
        """调用 LLM，返回 (文本输出, 解析后的 Action)"""

        if self.response_mode == "tool_call":
            return await self._call_with_tools(
                system_prompt, messages, action_registry, temperature
            )
        else:
            return await self._call_with_text(
                system_prompt, messages, action_registry, temperature
            )

    async def _call_with_tools(
        self,
        system_prompt: str,
        messages: list[dict],
        action_registry: ActionRegistry,
        temperature: float,
    ) -> tuple[str | None, Action | None]:
        """Tool calling 模式"""

        import litellm

        tool_schema = action_registry.get_tool_schema()

        for attempt in range(3):
            try:
                response = await litellm.acompletion(
                    model="deepseek/deepseek-chat",
                    messages=[{"role": "system", "content": system_prompt}] + messages,
                    tools=[tool_schema],
                    temperature=temperature,
                    api_key=self.api_key,
                    api_base=self.base_url,
                    drop_params=True,
                )
                break
            except Exception as e:
                if attempt == 2:
                    print(f"[LLM] 调用失败: {e}")
                    return None, None
                await asyncio.sleep(1)

        choice = response.choices[0]

        if hasattr(choice.message, "tool_calls") and choice.message.tool_calls:
            tool_call = choice.message.tool_calls[0]
            try:
                args = tool_call.function.arguments
                import json

                params = json.loads(args)

                action = Action(
                    action_type=params.get("action_type", "speak"),
                    target=params.get("target"),
                    content=params.get("content", ""),
                    params=params.get("params", {}),
                    internal_monologue=params.get("internal_monologue", ""),
                )
                return choice.message.content, action
            except Exception as e:
                print(f"[LLM] 解析 tool call 失败: {e}")

        return choice.message.content, None

    async def _call_with_text(
        self,
        system_prompt: str,
        messages: list[dict],
        action_registry: ActionRegistry,
        temperature: float,
    ) -> tuple[str | None, Action | None]:
        """文本解析模式"""

        import litellm

        text_guide = action_registry.get_text_guide()
        system_prompt = f"{system_prompt}\n\n{text_guide}"

        for attempt in range(3):
            try:
                response = await litellm.acompletion(
                    model="deepseek/deepseek-chat",
                    messages=[{"role": "system", "content": system_prompt}] + messages,
                    temperature=temperature,
                    api_key=self.api_key,
                    api_base=self.base_url,
                    drop_params=True,
                )
                break
            except Exception as e:
                if attempt == 2:
                    print(f"[LLM] 调用失败: {e}")
                    return None, None
                await asyncio.sleep(1)

        choice = response.choices[0]
        text = choice.message.content or ""

        action = action_registry.parse_text(text)
        return text, action