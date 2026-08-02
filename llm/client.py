"""LLM 客户端：统一调用 DeepSeek/本地模型，支持双模式解析。"""

import asyncio
import logging
import os
os.environ["LITELLM_LOG"] = "ERROR"
logging.getLogger("litellm").setLevel(logging.ERROR)

from core.action import Action, ActionRegistry


class LLMClient:
    """LLM 调用客户端"""

    def __init__(self, config: dict, logger=None):
        self.provider = config["provider"]
        self.model = config["model"]
        self.base_url = config.get("base_url")
        self.api_key = config["api_key"]
        self.response_mode = config["response_mode"]
        self.logger = logger
        self.extra_params = config.get("extra_params") or {}
        self._model_str = f"{self.provider}/{self.model}" if "/" not in self.model else self.model

    async def call(
        self,
        system_prompt: str,
        messages: list[dict],
        action_registry: ActionRegistry,
        temperature: float = 0.7,
        agent_name: str = "unknown",
        tick: int = 0,
        validation_context: dict | None = None,
    ) -> tuple[str | None, Action | None]:
        """调用 LLM，返回 (文本输出, 解析后的 Action)"""

        if self.response_mode == "tool_call":
            return await self._call_with_tools(
                system_prompt, messages, action_registry, temperature, agent_name, tick, validation_context
            )
        else:
            return await self._call_with_text(
                system_prompt, messages, action_registry, temperature, agent_name, tick, validation_context
            )

    async def _call_with_tools(
        self,
        system_prompt: str,
        messages: list[dict],
        action_registry: ActionRegistry,
        temperature: float,
        agent_name: str,
        tick: int,
        validation_context: dict | None = None,
    ) -> tuple[str | None, Action | None]:
        """Tool calling 模式"""

        import litellm

        if action_registry is None:
            return None, None
        tool_schemas = action_registry.get_tool_schemas()
        validation_context = validation_context or {}

        for retry in range(3):
            full_messages = [{"role": "system", "content": system_prompt}] + messages

            response = None
            for api_attempt in range(3):
                try:
                    call_kwargs = dict(self.extra_params)
                    call_kwargs.update({
                        "model": self._model_str,
                        "messages": full_messages,
                        "tools": tool_schemas,
                        "api_key": self.api_key,
                        "api_base": self.base_url,
                        "drop_params": True,
                    })
                    call_kwargs.setdefault("temperature", temperature)
                    response = await litellm.acompletion(**call_kwargs)
                    break
                except Exception as e:
                    if api_attempt == 2:
                        if self.logger:
                            self.logger.error(f"API 调用失败: {agent_name} | Tick: {tick} | {e}")
                        print(f"[LLM] 调用失败: {e}")
                        return None, None
                    await asyncio.sleep(1)

            choice = response.choices[0]
            raw_response = response.model_dump_json() if hasattr(response, "model_dump_json") else str(response)
            parsed_action = None

            if hasattr(choice.message, "tool_calls") and choice.message.tool_calls:
                tool_call = choice.message.tool_calls[0]
                try:
                    args = tool_call.function.arguments
                    import json

                    params = json.loads(args)

                    # --- 校验 action_type ---
                    action_spec = action_registry.get(tool_call.function.name)
                    error = None
                    if not action_spec:
                        valid_names = ", ".join(action_registry.get_action_names())
                        error = f"工具 '{tool_call.function.name}' 不存在，可用工具: {valid_names}"
                    else:
                        error = action_spec.validate_params(params, validation_context)

                    if error:
                        if retry < 2:
                            if self.logger:
                                self.logger.warning(f"{agent_name} 参数错误，重试中 ({retry + 1}/2): {error}")
                                self.logger.log_llm_call(
                                    agent_name=agent_name,
                                    tick=tick,
                                    mode="tool_call",
                                    system_prompt=system_prompt,
                                    messages=messages,
                                    schema_or_guide=str(tool_schemas),
                                    raw_response=raw_response,
                                    parsed_action=parsed_action,
                                )
                            print(f"[LLM] {agent_name} 参数错误，重试中 ({retry + 1}/2): {error}")
                            messages.append({"role": "assistant", "content": choice.message.content or ""})
                            messages.append({"role": "user", "content": error})
                            continue
                        else:
                            if self.logger:
                                self.logger.log_llm_call(
                                    agent_name=agent_name,
                                    tick=tick,
                                    mode="tool_call",
                                    system_prompt=system_prompt,
                                    messages=messages,
                                    schema_or_guide=str(tool_schemas),
                                    raw_response=raw_response,
                                    parsed_action=parsed_action,
                                )
                            print(f"[LLM] {agent_name} 重试耗尽，使用 fallback")
                            return choice.message.content, None

                    action = Action(
                        action_type=tool_call.function.name,
                        target=params.get("target"),
                        content=params.get("content", ""),
                        params=params,
                        internal_monologue=params.get("internal_monologue", ""),
                        raw_content=choice.message.content or "",
                        raw_tool_calls=[
                            {
                                "id": tc.id,
                                "type": tc.type,
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in choice.message.tool_calls
                        ],
                    )
                    parsed_action = {
                        "action_type": action.action_type,
                        "target": action.target,
                        "content": action.content,
                        "internal_monologue": action.internal_monologue,
                    }
                    if self.logger:
                        self.logger.log_llm_call(
                            agent_name=agent_name,
                            tick=tick,
                            mode="tool_call",
                            system_prompt=system_prompt,
                            messages=messages,
                            schema_or_guide=str(tool_schemas),
                            raw_response=raw_response,
                            parsed_action=parsed_action,
                        )
                    return choice.message.content, action
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"解析 tool call 失败: {agent_name} | Tick: {tick} | {e}")
                    print(f"[LLM] 解析 tool call 失败: {e}")

            if retry < 2:
                if self.logger:
                    self.logger.warning(f"{agent_name} 未调用工具，重试中 ({retry + 1}/2)")
                    self.logger.log_llm_call(
                        agent_name=agent_name,
                        tick=tick,
                        mode="tool_call",
                        system_prompt=system_prompt,
                        messages=messages,
                        schema_or_guide=str(tool_schemas),
                        raw_response=raw_response,
                        parsed_action=parsed_action,
                    )
                print(f"[LLM] {agent_name} 未调用工具，重试中 ({retry + 1}/2)")
                messages.append({"role": "assistant", "content": choice.message.content or ""})
                messages.append({"role": "user", "content": "请选择一个可用的工具来行动，不要只输出文字。"})
            else:
                if self.logger:
                    self.logger.log_llm_call(
                        agent_name=agent_name,
                        tick=tick,
                        mode="tool_call",
                        system_prompt=system_prompt,
                        messages=messages,
                        schema_or_guide=str(tool_schemas),
                        raw_response=raw_response,
                        parsed_action=parsed_action,
                    )

                print(f"[LLM] {agent_name} 重试耗尽，使用 fallback")
                return choice.message.content, None

        return choice.message.content, None

    async def call_multi(
        self,
        system_prompt: str,
        messages: list[dict],
        action_registry: ActionRegistry,
        temperature: float = 0.7,
        agent_name: str = "unknown",
        tick: int = 0,
        validation_context: dict | None = None,
        max_retries: int = 2,
        allow_no_tool: bool = False,
        execute_action=None,
    ) -> tuple[str | None, list[Action]]:
        """调用 LLM 并解析所有 tool calls（支持一次响应多个工具）

        allow_no_tool: True 时 LLM 返回纯文本直接返回 (text, []) 不走 retry
        execute_action: 可选回调，提供时将自动执行 action 并构建 tool 消息追加到 messages
        """
        import json
        import litellm

        if action_registry is None:
            return None, []
        tool_schemas = action_registry.get_tool_schemas()
        validation_context = validation_context or {}

        for retry in range(max_retries + 1):
            full_messages = [{"role": "system", "content": system_prompt}] + messages

            response = None
            for api_attempt in range(3):
                try:
                    call_kwargs = dict(self.extra_params)
                    call_kwargs.update({
                        "model": self._model_str,
                        "messages": full_messages,
                        "tools": tool_schemas,
                        "api_key": self.api_key,
                        "api_base": self.base_url,
                        "drop_params": True,
                    })
                    call_kwargs.setdefault("temperature", temperature)
                    response = await litellm.acompletion(**call_kwargs)
                    break
                except Exception as e:
                    if api_attempt == 2:
                        if self.logger:
                            self.logger.error(f"API 调用失败: {agent_name} | Tick: {tick} | {e}")
                        print(f"[LLM] 调用失败: {e}")
                        return None, []
                    await asyncio.sleep(1)

            choice = response.choices[0]
            raw_response = response.model_dump_json() if hasattr(response, "model_dump_json") else str(response)

            if hasattr(choice.message, "tool_calls") and choice.message.tool_calls:
                actions = []
                all_valid = True
                for tool_call in choice.message.tool_calls:
                    try:
                        params = json.loads(tool_call.function.arguments)

                        action_spec = action_registry.get(tool_call.function.name)
                        error = None
                        if not action_spec:
                            valid_names = ", ".join(action_registry.get_action_names())
                            error = f"工具 '{tool_call.function.name}' 不存在，可用工具: {valid_names}"
                        else:
                            error = action_spec.validate_params(params, validation_context)

                        if error:
                            all_valid = False
                            if retry < max_retries:
                                if self.logger:
                                    self.logger.warning(f"{agent_name} 参数错误，重试中 ({retry + 1}/{max_retries}): {error}")
                                    self.logger.log_llm_call(
                                        agent_name=agent_name, tick=tick, mode="tool_call",
                                        system_prompt=system_prompt, messages=messages,
                                        schema_or_guide=str(tool_schemas), raw_response=raw_response, parsed_action=None,
                                    )
                                print(f"[LLM] {agent_name} 参数错误，重试中 ({retry + 1}/{max_retries}): {error}")
                                messages.append({"role": "assistant", "content": choice.message.content or ""})
                                messages.append({"role": "user", "content": error})
                            else:
                                if self.logger:
                                    self.logger.log_llm_call(
                                        agent_name=agent_name, tick=tick, mode="tool_call",
                                        system_prompt=system_prompt, messages=messages,
                                        schema_or_guide=str(tool_schemas), raw_response=raw_response, parsed_action=None,
                                    )
                                print(f"[LLM] {agent_name} 重试耗尽，使用 fallback")
                                return choice.message.content, []
                            break

                        action = Action(
                            action_type=tool_call.function.name,
                            target=params.get("target"),
                            content=params.get("content", ""),
                            params=params,
                            internal_monologue=params.get("internal_monologue", ""),
                        )
                        actions.append(action)
                    except Exception as e:
                        all_valid = False
                        if retry < max_retries:
                            if self.logger:
                                self.logger.warning(f"解析 tool call 失败: {agent_name} | Tick: {tick} | {e}")
                            print(f"[LLM] 解析 tool call 失败: {e}")
                            messages.append({"role": "assistant", "content": choice.message.content or ""})
                            messages.append({"role": "user", "content": f"解析参数失败: {e}"})
                        else:
                            return choice.message.content, []
                        break

                if all_valid:
                    for action in actions:
                        action.raw_content = choice.message.content or ""
                        if hasattr(choice.message, "tool_calls") and choice.message.tool_calls:
                            action.raw_tool_calls = [
                                {"id": tc.id, "type": tc.type,
                                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                                for tc in choice.message.tool_calls
                            ]

                    parsed = [{"action_type": a.action_type, "target": a.target, "content": a.content} for a in actions]
                    if self.logger:
                        self.logger.log_llm_call(
                            agent_name=agent_name, tick=tick, mode="tool_call",
                            system_prompt=system_prompt, messages=messages,
                            schema_or_guide=str(tool_schemas), raw_response=raw_response, parsed_action=parsed,
                        )

                    if execute_action and actions:
                        tc_list = []
                        for tc in choice.message.tool_calls:
                            tc_list.append({
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            })
                        messages.append({
                            "role": "assistant",
                            "content": choice.message.content or "",
                            "tool_calls": tc_list,
                        })
                        for i, action in enumerate(actions):
                            result = execute_action(action)
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc_list[i]["id"],
                                "content": result or f"'{action.action_type}' 执行完成",
                            })

                    return choice.message.content, actions
            else:
                if allow_no_tool:
                    if self.logger:
                        self.logger.log_llm_call(
                            agent_name=agent_name, tick=tick, mode="tool_call",
                            system_prompt=system_prompt, messages=messages,
                            schema_or_guide=str(tool_schemas), raw_response=raw_response, parsed_action=None,
                        )
                    return choice.message.content, []

                if retry < max_retries:
                    if self.logger:
                        self.logger.warning(f"{agent_name} 未调用工具，重试中 ({retry + 1}/{max_retries})")
                        self.logger.log_llm_call(
                            agent_name=agent_name, tick=tick, mode="tool_call",
                            system_prompt=system_prompt, messages=messages,
                            schema_or_guide=str(tool_schemas), raw_response=raw_response, parsed_action=None,
                        )
                    print(f"[LLM] {agent_name} 未调用工具，重试中 ({retry + 1}/{max_retries})")
                    messages.append({"role": "assistant", "content": choice.message.content or ""})
                    messages.append({"role": "user", "content": "请使用提供的工具来执行操作。"})
                else:
                    if self.logger:
                        self.logger.log_llm_call(
                            agent_name=agent_name, tick=tick, mode="tool_call",
                            system_prompt=system_prompt, messages=messages,
                            schema_or_guide=str(tool_schemas), raw_response=raw_response, parsed_action=None,
                        )
                    print(f"[LLM] {agent_name} 重试耗尽，使用 fallback")
                    return choice.message.content, []

        return None, []

    async def _call_with_text(
        self,
        system_prompt: str,
        messages: list[dict],
        action_registry: ActionRegistry,
        temperature: float,
        agent_name: str,
        tick: int,
        validation_context: dict | None = None,
    ) -> tuple[str | None, Action | None]:
        """文本解析模式"""

        import litellm

        if action_registry is None:
            return None, None
        text_guide = action_registry.get_text_guide()
        system_prompt = f"{system_prompt}\n\n{text_guide}"
        validation_context = validation_context or {}

        for retry in range(3):
            for attempt in range(3):
                try:
                    call_kwargs = dict(self.extra_params)
                    call_kwargs.update({
                        "model": self.model,
                        "messages": [{"role": "system", "content": system_prompt}] + messages,
                        "api_key": self.api_key,
                        "api_base": self.base_url,
                        "drop_params": True,
                    })
                    call_kwargs.setdefault("temperature", temperature)
                    response = await litellm.acompletion(**call_kwargs)
                    break
                except Exception as e:
                    if attempt == 2:
                        if self.logger:
                            self.logger.error(f"API 调用失败: {agent_name} | Tick: {tick} | {e}")
                        print(f"[LLM] 调用失败: {e}")
                        return None, None
                    await asyncio.sleep(1)

            choice = response.choices[0]
            text = choice.message.content or ""
            raw_response = response.model_dump_json() if hasattr(response, "model_dump_json") else str(response)
            action = action_registry.parse_text(text)
            if action:
                action.raw_content = text

            # --- 校验 ---
            error = None
            if not action:
                error = "无法解析你的输出，请严格按格式输出。"
            else:
                action_spec = action_registry.get(action.action_type)
                if not action_spec:
                    valid_names = ", ".join(action_registry.get_action_names())
                    error = f"行动类型 '{action.action_type}' 不存在，可用: {valid_names}"
                else:
                    params = {"target": action.target, "content": action.content, **action.params}
                    error = action_spec.validate_params(params, validation_context)

            if error:
                if retry < 2:
                    if self.logger:
                        self.logger.warning(f"{agent_name} 输出错误，重试中 ({retry + 1}/2): {error}")
                        self.logger.log_llm_call(
                            agent_name=agent_name,
                            tick=tick,
                            mode="text_parse",
                            system_prompt=system_prompt,
                            messages=messages,
                            schema_or_guide=text_guide,
                            raw_response=raw_response,
                            parsed_action=None,
                        )
                    print(f"[LLM] {agent_name} 输出错误，重试中 ({retry + 1}/2): {error}")
                    messages.append({"role": "assistant", "content": text})
                    messages.append({"role": "user", "content": error})
                    continue
                else:
                    if self.logger:
                        self.logger.log_llm_call(
                            agent_name=agent_name,
                            tick=tick,
                            mode="text_parse",
                            system_prompt=system_prompt,
                            messages=messages,
                            schema_or_guide=text_guide,
                            raw_response=raw_response,
                            parsed_action=None,
                        )
                    print(f"[LLM] {agent_name} 重试耗尽，使用 fallback")
                    return text, None

            parsed_action = {
                "action_type": action.action_type,
                "target": action.target,
                "content": action.content,
                "internal_monologue": action.internal_monologue,
            }

            if self.logger:
                self.logger.log_llm_call(
                    agent_name=agent_name,
                    tick=tick,
                    mode="text_parse",
                    system_prompt=system_prompt,
                    messages=messages,
                    schema_or_guide=text_guide,
                    raw_response=raw_response,
                    parsed_action=parsed_action,
                )

            return text, action

        return None, None