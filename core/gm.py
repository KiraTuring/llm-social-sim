"""GM Agent：注入事件，推进剧情。"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from core.action import ActionRegistry, format_tool_result
if TYPE_CHECKING:
    from core.ports import LLMClient
    from core.world import WorldState


class GMAgent:
    """GM Agent：注入事件，推进剧情"""

    MAX_TURNS = 3
    RECENT_MESSAGE_WINDOW = 50

    def __init__(self, events: list, random_events: list, chance: float,
                 gm_registry: ActionRegistry, use_llm: bool = False,
                 llm_chance: float = 0.0, llm_prompt: str = "",
                 world_description: str = "", logger=None, event_tick_window: int = 3,
                 prompt_format: str = "text", history_max_messages: int = 40):
        self.scheduled_events = events
        self.random_events = random_events
        self.random_chance = chance
        self.use_llm = use_llm
        self.llm_chance = llm_chance
        self.llm_prompt = llm_prompt
        self.world_description = world_description
        self.logger = logger
        self.event_tick_window = event_tick_window
        self.prompt_format = prompt_format
        self.history_max_messages = history_max_messages
        self._gm_history: list[dict] = []
        self.registry = gm_registry

    def to_dict(self) -> dict:
        """序列化为可保存的 dict（存档用）。"""
        return {
            "scheduled_events": [list(item) for item in self.scheduled_events],
            "random_events": self.random_events,
            "use_llm": self.use_llm,
            "history": self._gm_history,
        }

    async def check_and_inject(self, world: "WorldState", llm_client: "LLMClient | None" = None):
        """每个 tick 检查是否需要注入事件"""

        scheduled = self._check_scheduled(world)
        random_ev = self._check_random()

        prior = [e for e in [scheduled, random_ev] if e]
        for e in prior:
            world.add_event(e)
            if self.logger:
                self.logger.info(f"GM 事件: {e}")
            self._broadcast_event(e, world)

        if self.use_llm and llm_client:
            recent = world.message_bus.get_recent(self.RECENT_MESSAGE_WINDOW)
            trigger = any(
                (m.trigger_gm or
                 m.target in world.npc_names)
                and m.tick == world.tick - 1
                for m in recent
            )
            if trigger or random.random() < self.llm_chance:
                await self._generate_llm_event(world, llm_client)

    def _check_scheduled(self, world: "WorldState") -> str | None:
        """检查计划事件，支持 3-tuple (tick, event, changes) 格式"""
        for item in self.scheduled_events[:]:
            tick = item[0]
            event = item[1]
            changes = item[2] if len(item) > 2 else None
            if tick == world.tick:
                self.scheduled_events.remove(item)
                if changes:
                    for loc, env_changes in changes.items():
                        for key, val in env_changes.items():
                            world.update_environment(loc, key, val)
                return event
        return None

    def _check_random(self) -> str | None:
        """检查随机事件"""
        if self.random_events and random.random() < self.random_chance:
            return random.choice(self.random_events)
        return None

    def _broadcast_event(self, event: str, world: "WorldState"):
        """广播事件给所有 Agent"""
        from core.message import Message, BROADCAST

        msg = Message(sender="GM", recipients=[BROADCAST], content=event, tag="system_event", tick=world.tick)
        world.message_bus.send(msg)

    def _truncate_gm_history(self):
        """chat 模式：滑动窗口截断 GM 对话历史。

        截断只从头部丢弃；若边界正好落在 assistant(tool_calls) 与其 tool 消息之间，
        会残留孤立 tool 消息（其 assistant 已被切掉），发送给 LLM 会触发
        "tool_calls 必须被 tool 消息配对"的 BadRequestError。
        因此截断后跳过开头的孤立 tool 消息，保证历史始终从完整消息组开始。
        """
        if len(self._gm_history) > self.history_max_messages:
            excess = len(self._gm_history) - self.history_max_messages
            self._gm_history = self._gm_history[excess:]
        while self._gm_history and self._gm_history[0].get("role") == "tool":
            self._gm_history.pop(0)

    async def _generate_llm_event(self, world: "WorldState", llm_client: "LLMClient") -> None:
        """ReAct 循环：让 LLM 连续调用工具生成事件"""
        system_prompt = self._build_gm_prompt()

        if self.prompt_format == "chat":
            messages = list(self._gm_history)
            messages.append({"role": "user", "content": self._build_world_context(world)})
        else:
            messages = [{"role": "user", "content": self._build_world_context(world)}]

        any_actions = False

        for turn in range(self.MAX_TURNS):
            # 每 turn 重建：前序 turn 的工具副作用（如 npc_add）必须对后续校验可见
            validation_context = world.build_validation_context("GM")

            def _exec(action):
                spec = self.registry.get(action.action_type)
                if not spec:
                    return f"未知工具: {action.action_type}"
                messages, result = spec.execute("GM", action.params, world)
                for msg in messages:
                    world.message_bus.send(msg)
                # 执行后原地刷新校验上下文，使同批后续 tool call 能看到本次副作用
                validation_context.update(world.build_validation_context("GM"))
                summary = format_tool_result(action.action_type, result)
                if self.logger:
                    self.logger.info(f"GM 工具: {action.action_type} → {summary}")
                return summary

            _, actions = await llm_client.call_multi(
                system_prompt=system_prompt,
                messages=messages,
                action_registry=self.registry,
                agent_name="GM",
                tick=world.tick,
                validation_context=validation_context,
                allow_no_tool=True,
                execute_action=_exec,
            )
            if not actions:
                break
            any_actions = True

            messages.append({"role": "user",
                             "content": "如需继续使用工具请调用，否则直接回复'完成'。"})

        if self.prompt_format == "chat" and any_actions:
            self._gm_history = [
                m for m in messages
                if not (m.get("role") == "user" and "如需继续使用工具" in m.get("content", ""))
            ]
            self._truncate_gm_history()

    def _build_gm_prompt(self) -> str:
        """构建 GM 的 system prompt。"""
        from core.prompts import build_gm_prompt

        return build_gm_prompt(self.registry, self.llm_prompt, self.world_description)

    def _gm_role_rules(self) -> str:
        from core.prompts import _gm_role_rules

        return _gm_role_rules(self.registry)

    def _build_world_context(self, world: "WorldState") -> str:
        """构建世界状态上下文（中等粒度）。"""
        from core.prompts import build_gm_world_context

        return build_gm_world_context(world, self.event_tick_window)
