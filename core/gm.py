"""GM Agent：注入事件，推进剧情。"""

import random
from typing import TYPE_CHECKING

from core.action import ActionRegistry
from core.actions.gm_actions import GenerateEventAction

if TYPE_CHECKING:
    from core.world import WorldState
    from llm.client import LLMClient


class GMAgent:
    """GM Agent：注入事件，推进剧情"""

    def __init__(self, events: list, random_events: list, chance: float,
                 use_llm: bool = False, llm_chance: float = 0.0, llm_prompt: str = "",
                 logger=None):
        self.scheduled_events = events
        self.random_events = random_events
        self.random_chance = chance
        self.use_llm = use_llm
        self.llm_chance = llm_chance
        self.llm_prompt = llm_prompt
        self.logger = logger

        self.registry = ActionRegistry()
        self.registry.register(GenerateEventAction())

    async def check_and_inject(self, world: "WorldState", llm_client: "LLMClient | None" = None):
        """每个 tick 检查是否需要注入事件"""

        scheduled = self._check_scheduled(world)
        random_ev = self._check_random()

        llm_ev = None
        if self.use_llm and llm_client and random.random() < self.llm_chance:
            llm_ev = await self._generate_llm_event(world, llm_client)

        events = [e for e in [scheduled, random_ev, llm_ev] if e]

        for event in events:
            if self.logger:
                self.logger.info(f"GM 事件: {event}")
            world.add_event(event)
            self._broadcast_event(event, world)

    def _check_scheduled(self, world: "WorldState") -> str | None:
        """检查计划事件"""
        for tick, event in self.scheduled_events[:]:
            if tick == world.tick:
                self.scheduled_events.remove((tick, event))
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

        msg = Message(sender="GM", recipients=[BROADCAST], content=event, msg_type="system_event", tick=world.tick)
        world.message_bus.send(msg)

    async def _generate_llm_event(self, world: "WorldState", llm_client: "LLMClient") -> str | None:
        """根据当前世界状态，让 LLM 生成一个随机事件"""
        context = self._build_world_context(world)
        system_prompt = self._build_gm_prompt()

        validation_context = {
            "locations": world.locations,
            "agent_names": list(world.agents.keys()),
        }

        _, action = await llm_client.call(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": context}],
            action_registry=self.registry,
            agent_name="GM",
            tick=world.tick,
            validation_context=validation_context,
        )

        if action:
            return self._dispatch(action)
        return None

    def _build_gm_prompt(self) -> str:
        """构建 GM 的 system prompt，自动追加可用工具"""
        lines = []
        if self.llm_prompt:
            lines.append(self.llm_prompt)
        lines.append("你可以使用的工具：")
        for s in self.registry.get_tool_schemas():
            name = s["function"]["name"]
            desc = s["function"]["description"]
            lines.append(f"  - {name}: {desc}")
        return "\n".join(lines)

    def _build_world_context(self, world: "WorldState") -> str:
        """构建世界状态上下文（中等粒度）"""
        parts = [f"当前是第 {world.tick} 个时间步。"]

        locs = {}
        for name, agent in world.agents.items():
            locs.setdefault(agent.location, []).append(name)

        parts.append("\n角色位置与状态：")
        for loc, names in locs.items():
            statuses = [f"{n}(情绪:{world.agents[n].mood}, 精力:{world.agents[n].energy})" for n in names]
            parts.append(f"  {loc}: {', '.join(statuses)}")

        if world.event_log:
            parts.append("\n最近发生的事件：")
            for e in world.event_log[-5:]:
                parts.append(f"  {e}")

        if world.message_bus:
            speech = []
            for m in world.message_bus.get_recent(10):
                if m.msg_type == "speech":
                    target = f" -> {m.target}" if m.target else ""
                    speech.append(f"[{m.sender}]{target}: {m.content}")
                elif m.msg_type == "whisper":
                    speech.append(f"[{m.sender}] (悄悄对 {m.target}): {m.content}")
            if speech:
                parts.append("\n最近的对话：")
                for s in speech[-8:]:
                    parts.append(f"  {s}")

        return "\n".join(parts)

    def _dispatch(self, action) -> str | None:
        """根据 action_type 分发到对应的 handler"""
        handler = {
            "generate_event": self._handle_event,
        }.get(action.action_type)
        if handler:
            return handler(action)
        return None

    def _handle_event(self, action) -> str | None:
        """处理 generate_event action"""
        return action.content if action.content else None
