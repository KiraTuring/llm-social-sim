"""LLM 客户端：统一调用 DeepSeek/本地模型，支持双模式解析。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass

from core.action import Action, ActionRegistry

os.environ["LITELLM_LOG"] = "ERROR"
logging.getLogger("litellm").setLevel(logging.ERROR)


@dataclass
class JSONResult:
    """结构化 LLM 返回：原始文本 + 解析出的 JSON 对象。

    data 为 None 表示内容为空或未解析出 JSON 对象（raw 仍可作兜底）。
    """

    data: dict | None
    raw: str


def _parse_json_object(raw: str) -> dict | None:
    """解析 LLM 返回中的 JSON 对象：直接解析 + 正则提取兜底。

    返回 dict 或 None（内容为空 / 非 JSON / 解析失败）。
    """
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


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
        if self.logger:
            self.logger.debug(f"EXTRA PARAMS: {self.extra_params}")
        self._model_str = f"{self.provider}/{self.model}" if "/" not in self.model else self.model

    def _status(self, level: str, message: str) -> None:
        """输出运行状态：logger 存在时写入日志文件，并始终在控制台显示。"""
        if self.logger is not None:
            getattr(self.logger, level)(message)
        print(f"[LLM] {message}")

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
        """调用 LLM，返回 (文本输出, 解析后的 Action)。

        tool_call 模式基于 call_multi() 实现——单 Action 是多 Action 的特例。
        """
        if self.response_mode == "tool_call":
            text, actions = await self.call_multi(
                system_prompt, messages, action_registry, temperature,
                agent_name, tick, validation_context,
                allow_no_tool=False,
                limit_tools=1,
            )
            return text, actions[0] if actions else None
        else:
            return await self._call_with_text(
                system_prompt, messages, action_registry, temperature,
                agent_name, tick, validation_context,
            )

    async def _acompletion_with_retry(
        self, agent_name: str, tick: int, temperature: float, **overrides
    ):
        """统一 API 调用：extra_params 合并 + 最多 3 次重试。

        overrides 中的键优先于 extra_params；temperature 未显式指定时用参数值。
        返回 litellm 响应，重试全部失败时返回 None。
        """
        import litellm

        call_kwargs = dict(self.extra_params)
        call_kwargs.update(overrides)
        call_kwargs.setdefault("temperature", temperature)

        for api_attempt in range(3):
            try:
                return await litellm.acompletion(**call_kwargs)
            except Exception as e:
                if api_attempt == 2:
                    self._status("error", f"API 调用失败: {agent_name} | Tick: {tick} | {e}")
                    return None
                await asyncio.sleep(1)
        return None

    async def call_json(
        self,
        system_prompt: str,
        user_content: str,
        temperature: float = 0.3,
        agent_name: str = "unknown",
        tick: int = 0,
    ) -> JSONResult | None:
        """调用 LLM 并解析 JSON 对象（记忆压缩等结构化输出场景）。

        走统一请求管线：extra_params 合并 + 最多 3 次重试（_acompletion_with_retry）。
        API 调用彻底失败返回 None；内容为空或解析不出 JSON 时返回
        JSONResult(data=None, raw=原文)，由调用方决定兜底策略。
        """
        response = await self._acompletion_with_retry(
            agent_name, tick, temperature,
            model=self._model_str,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            api_key=self.api_key,
            api_base=self.base_url,
            drop_params=True,
        )
        if response is None:
            return None
        raw = (response.choices[0].message.content or "").strip()
        return JSONResult(data=_parse_json_object(raw), raw=raw)

    async def _retry_loop(
        self,
        messages: list[dict],
        temperature: float,
        agent_name: str,
        tick: int,
        max_retries: int,
        system_prompt: str,
        attempt,
        fallback_payload,
        **request_overrides,
    ):
        """共享请求-重试骨架：请求 → attempt 解析/校验 → 成功/重试/fallback。

        attempt(response, retry) 返回 (status, payload)：
        - ("success", payload)：直接返回 payload
        - ("retry", feedback)：骨架追加 assistant+user 反馈后进入下一轮
        - ("fallback", payload)：重试耗尽，直接返回 payload
        attempt 内部负责日志与打印；骨架只负责请求、反馈消息与循环。
        """
        for retry in range(max_retries + 1):
            response = await self._acompletion_with_retry(
                agent_name, tick, temperature,
                model=self._model_str,
                messages=[{"role": "system", "content": system_prompt}] + messages,
                api_key=self.api_key,
                api_base=self.base_url,
                drop_params=True,
                **request_overrides,
            )
            if response is None:
                return fallback_payload
            status, payload = attempt(response, retry)
            if status == "success":
                return payload
            if status == "fallback":
                return payload
            self._append_feedback(messages, response.choices[0], payload)
        return fallback_payload

    @staticmethod
    def _append_feedback(messages: list[dict], choice, feedback: str) -> None:
        """重试反馈：追加 assistant 原文 + user 纠错消息（不污染持久历史）。"""
        messages.append({"role": "assistant", "content": choice.message.content or ""})
        messages.append({"role": "user", "content": feedback})

    def _process_tool_calls(
        self,
        tool_calls,
        choice,
        raw_response,
        tool_schemas,
        action_registry,
        validation_context,
        execute_action,
        messages,
        system_prompt,
        agent_name,
        tick,
        max_retries,
        retry,
    ):
        """校验并执行一次响应中的 tool calls。

        返回 (status, payload)：
        - ("success", actions)：全部通过；或同批已执行部分工具后遇到非法工具
          （已执行结果与失败工具的错误信息已配对写入 messages，GM 的 ReAct
          循环下一轮会看到这些结果，不会盲目重试导致重复执行）
        - ("retry", feedback)：尚未产生副作用，需要重试的反馈文本（由重试循环追加）
        - ("fallback", None)：重试耗尽，调用方返回 fallback
        """
        actions = []
        executed = []
        for tool_call in tool_calls:
            try:
                params = json.loads(tool_call.function.arguments)
                action_spec = action_registry.get(tool_call.function.name)
                if action_spec is None:
                    valid_names = ", ".join(action_registry.get_action_names())
                    error = f"工具 '{tool_call.function.name}' 不存在，可用工具: {valid_names}"
                else:
                    error = action_spec.validate_params(params, validation_context)

                if error:
                    # 同批前面的工具已经执行过（如 npc_add 已生效），此时整体重试
                    # 会让模型把已执行的工具再执行一遍。改为把已执行结果和当前失败
                    # 信息配对写入 messages 后返回 success，让 ReAct 循环进入下一轮。
                    if execute_action and executed:
                        self._append_executed_tool_messages(
                            tool_calls=tool_calls,
                            choice=choice,
                            executed=executed,
                            messages=messages,
                            failed_tool_call=tool_call,
                            error=error,
                        )
                        self._log_partial_tool_call_error(
                            error=error,
                            raw_response=raw_response,
                            tool_schemas=tool_schemas,
                            messages=messages,
                            system_prompt=system_prompt,
                            agent_name=agent_name,
                            tick=tick,
                        )
                        return "success", actions

                    return self._handle_tool_call_error(
                        error,
                        f"{agent_name} 参数错误，重试中 ({retry + 1}/{max_retries}): {error}",
                        choice, raw_response, tool_schemas, messages,
                        system_prompt, agent_name, tick, max_retries, retry,
                    )

                action = Action(
                    action_type=tool_call.function.name,
                    target=params.get("target"),
                    content=params.get("content", ""),
                    params=params,
                    internal_monologue=params.get("internal_monologue", ""),
                )
                actions.append(action)

                # 校验通过后立即执行（若提供回调），使前序工具的副作用
                # （如 npc_add 修改 world）对后续 tool call 的校验可见。
                # 结果暂存，等全部工具校验通过后统一追加 assistant(tool_calls)+tool
                # 消息——保证 assistant 声明的每个 tool_call_id 都被 tool 消息配对。
                if execute_action:
                    executed.append((tool_call, execute_action(action)))
            except Exception as e:
                if execute_action and executed:
                    self._append_executed_tool_messages(
                        tool_calls=tool_calls,
                        choice=choice,
                        executed=executed,
                        messages=messages,
                        failed_tool_call=tool_call,
                        error=f"解析参数失败: {e}",
                    )
                    self._log_partial_tool_call_error(
                        error=f"解析参数失败: {e}",
                        raw_response=raw_response,
                        tool_schemas=tool_schemas,
                        messages=messages,
                        system_prompt=system_prompt,
                        agent_name=agent_name,
                        tick=tick,
                    )
                    return "success", actions

                return self._handle_tool_call_error(
                    f"解析参数失败: {e}",
                    f"解析 tool call 失败: {agent_name} | Tick: {tick} | {e}",
                    choice, raw_response, tool_schemas, messages,
                    system_prompt, agent_name, tick, max_retries, retry,
                )

        if execute_action and executed:
            self._append_executed_tool_messages(
                tool_calls=tool_calls,
                choice=choice,
                executed=executed,
                messages=messages,
            )

        return "success", actions

    def _append_executed_tool_messages(
        self,
        tool_calls,
        choice,
        executed,
        messages,
        failed_tool_call=None,
        error: str | None = None,
    ) -> None:
        """把一次响应中的工具执行结果按 assistant(tool_calls)+tool 消息对追加到 messages。

        所有 assistant 声明的 tool_call_id 都会被配对：已执行工具用执行结果，
        校验/解析失败的工具用错误信息作为 tool 消息内容。
        """
        tc_list = [{
            "id": tc.id,
            "type": "function",
            "function": {
                "name": tc.function.name,
                "arguments": tc.function.arguments,
            },
        } for tc in tool_calls]
        messages.append({
            "role": "assistant",
            "content": choice.message.content or "",
            "tool_calls": tc_list,
        })
        for tc, result in executed:
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result or f"'{tc.function.name}' 执行完成",
            })
        if failed_tool_call is not None:
            messages.append({
                "role": "tool",
                "tool_call_id": failed_tool_call.id,
                "content": f"工具执行失败: {error}",
            })

    def _log_partial_tool_call_error(
        self,
        error: str,
        raw_response,
        tool_schemas,
        messages,
        system_prompt,
        agent_name,
        tick,
    ) -> None:
        """部分执行后的工具校验/解析失败：记录 warning 与 LLM 调用日志。"""
        self._status(
            "warning",
            f"{agent_name} 同一响应中部分工具已执行后遇到非法工具，"
            f"已把执行结果与错误反馈给模型继续下一轮: {error}",
        )
        if self.logger:
            self.logger.log_llm_call(
                agent_name=agent_name, tick=tick, mode="tool_call",
                system_prompt=system_prompt, messages=messages,
                schema_or_guide=str(tool_schemas), raw_response=raw_response,
            )

    def _handle_tool_call_error(
        self,
        feedback,
        warn_text,
        choice,
        raw_response,
        tool_schemas,
        messages,
        system_prompt,
        agent_name,
        tick,
        max_retries,
        retry,
    ):
        """工具校验/解析失败：可重试则返回反馈文本，重试耗尽则返回 fallback 信号。

        返回 ("retry", feedback) 或 ("fallback", None)。日志/打印在此统一处理。
        """
        if retry < max_retries:
            self._status("warning", warn_text)
            if self.logger:
                self.logger.log_llm_call(
                    agent_name=agent_name, tick=tick, mode="tool_call",
                    system_prompt=system_prompt, messages=messages,
                    schema_or_guide=str(tool_schemas), raw_response=raw_response,
                )
            return "retry", feedback
        if self.logger:
            self.logger.log_llm_call(
                agent_name=agent_name, tick=tick, mode="tool_call",
                system_prompt=system_prompt, messages=messages,
                schema_or_guide=str(tool_schemas), raw_response=raw_response,
            )
        self._status("warning", f"{agent_name} 重试耗尽，本次无行动")
        return "fallback", None

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
        limit_tools: int | None = None,
        execute_action=None,
    ) -> tuple[str | None, list[Action]]:
        """调用 LLM 并解析所有 tool calls（支持一次响应多个工具）。

        allow_no_tool: True 时 LLM 返回纯文本直接返回 (text, []) 不走 retry
        limit_tools: 限制最多解析前 N 个 tool call，多余的显式丢弃（Agent 路径传 1）
        execute_action: 可选回调，提供时将自动执行 action 并构建 tool 消息追加到 messages
        call() 的单 Action 路径也复用本方法（allow_no_tool=False, limit_tools=1）。
        """
        if action_registry is None:
            return None, []
        tool_schemas = action_registry.get_tool_schemas()
        validation_context = validation_context or {}

        def attempt(response, retry):
            choice = response.choices[0]
            raw_response = response.model_dump_json() if hasattr(response, "model_dump_json") else str(response)

            if hasattr(choice.message, "tool_calls") and choice.message.tool_calls:
                tool_calls = list(choice.message.tool_calls)
                if limit_tools is not None and len(tool_calls) > limit_tools:
                    discarded = len(tool_calls) - limit_tools
                    self._status(
                        "warning",
                        f"{agent_name} 一次返回 {len(tool_calls)} 个工具，"
                        f"仅保留前 {limit_tools} 个，显式丢弃 {discarded} 个",
                    )
                    tool_calls = tool_calls[:limit_tools]

                status, payload = self._process_tool_calls(
                    tool_calls, choice, raw_response, tool_schemas,
                    action_registry, validation_context, execute_action,
                    messages, system_prompt, agent_name, tick,
                    max_retries, retry,
                )
                if status == "success":
                    actions = payload
                    for action in actions:
                        action.raw_content = choice.message.content or ""
                        action.raw_tool_calls = [
                            {"id": tc.id, "type": tc.type,
                             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                            for tc in tool_calls
                        ]
                    parsed = [{"action_type": a.action_type, "target": a.target, "content": a.content} for a in actions]
                    if self.logger:
                        self.logger.log_llm_call(
                            agent_name=agent_name, tick=tick, mode="tool_call",
                            system_prompt=system_prompt, messages=messages,
                            schema_or_guide=str(tool_schemas), raw_response=raw_response,
                        )
                    return "success", (choice.message.content, actions)
                if status == "fallback":
                    return "fallback", (choice.message.content, [])
                return "retry", payload

            # 未调用工具
            if allow_no_tool:
                if self.logger:
                    self.logger.log_llm_call(
                        agent_name=agent_name, tick=tick, mode="tool_call",
                        system_prompt=system_prompt, messages=messages,
                        schema_or_guide=str(tool_schemas), raw_response=raw_response,
                    )
                return "success", (choice.message.content, [])
            if retry < max_retries:
                self._status("warning", f"{agent_name} 未调用工具，重试中 ({retry + 1}/{max_retries})")
                if self.logger:
                    self.logger.log_llm_call(
                        agent_name=agent_name, tick=tick, mode="tool_call",
                        system_prompt=system_prompt, messages=messages,
                        schema_or_guide=str(tool_schemas), raw_response=raw_response,
                    )
                return "retry", "请选择一个可用的工具来行动，不要只输出文字。"
            if self.logger:
                self.logger.log_llm_call(
                    agent_name=agent_name, tick=tick, mode="tool_call",
                    system_prompt=system_prompt, messages=messages,
                    schema_or_guide=str(tool_schemas), raw_response=raw_response,
                )
            self._status("warning", f"{agent_name} 重试耗尽，本次无行动")
            return "fallback", (choice.message.content, [])

        return await self._retry_loop(
            messages=messages,
            temperature=temperature,
            agent_name=agent_name,
            tick=tick,
            max_retries=max_retries,
            system_prompt=system_prompt,
            attempt=attempt,
            fallback_payload=(None, []),
            tools=tool_schemas,
        )

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

        if action_registry is None:
            return None, None
        text_guide = action_registry.get_text_guide()
        system_prompt = f"{system_prompt}\n\n{text_guide}"
        validation_context = validation_context or {}
        max_retries = 2

        def attempt(response, retry):
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
                if retry < max_retries:
                    self._status("warning", f"{agent_name} 输出错误，重试中 ({retry + 1}/{max_retries}): {error}")
                    if self.logger:
                        self.logger.log_llm_call(
                            agent_name=agent_name, tick=tick, mode="text_parse",
                            system_prompt=system_prompt, messages=messages,
                            schema_or_guide=text_guide, raw_response=raw_response,
                        )
                    return "retry", error
                if self.logger:
                    self.logger.log_llm_call(
                        agent_name=agent_name, tick=tick, mode="text_parse",
                        system_prompt=system_prompt, messages=messages,
                        schema_or_guide=text_guide, raw_response=raw_response,
                    )
                self._status("warning", f"{agent_name} 重试耗尽，本次无行动")
                return "fallback", (text, None)

            parsed_action = {
                "action_type": action.action_type,
                "target": action.target,
                "content": action.content,
                "internal_monologue": action.internal_monologue,
            }

            if self.logger:
                self.logger.log_llm_call(
                    agent_name=agent_name, tick=tick, mode="text_parse",
                    system_prompt=system_prompt, messages=messages,
                    schema_or_guide=text_guide, raw_response=raw_response,
                )

            return "success", (text, action)

        return await self._retry_loop(
            messages=messages,
            temperature=temperature,
            agent_name=agent_name,
            tick=tick,
            max_retries=max_retries,
            system_prompt=system_prompt,
            attempt=attempt,
            fallback_payload=(None, None),
        )
