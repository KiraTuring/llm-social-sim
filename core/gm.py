"""GM Agent：注入事件，推进剧情。"""

import random
from typing import TYPE_CHECKING

from core.action import ActionRegistry
from core.actions.gm_actions import GenerateEventAction, ModifyEnvironmentAction

if TYPE_CHECKING:
    from core.world import WorldState
    from llm.client import LLMClient


class GMAgent:
    """GM Agent：注入事件，推进剧情"""

    MAX_TURNS = 3

    def __init__(self, events: list, random_events: list, chance: float,
                 use_llm: bool = False, llm_chance: float = 0.0, llm_prompt: str = "",
                 world_description: str = "", logger=None):
        self.scheduled_events = events
        self.random_events = random_events
        self.random_chance = chance
        self.use_llm = use_llm
        self.llm_chance = llm_chance
        self.llm_prompt = llm_prompt
        self.world_description = world_description
        self.logger = logger

        self.registry = ActionRegistry()
        self.registry.register(GenerateEventAction())
        self.registry.register(ModifyEnvironmentAction())

    async def check_and_inject(self, world: "WorldState", llm_client: "LLMClient | None" = None):
        """每个 tick 检查是否需要注入事件"""

        scheduled = self._check_scheduled(world)
        random_ev = self._check_random()

        # 先将计划/随机事件写入 event_log，让 GM 的 context 能看到
        prior = [e for e in [scheduled, random_ev] if e]
        for e in prior:
            world.add_event(e)

        llm_events: list[str] = []
        if self.use_llm and llm_client and random.random() < self.llm_chance:
            llm_events = await self._generate_llm_event(world, llm_client) or []
            for e in llm_events:
                world.add_event(e)

        events = prior + llm_events
        for event in events:
            if self.logger:
                self.logger.info(f"GM 事件: {event}")
            self._broadcast_event(event, world)

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

        msg = Message(sender="GM", recipients=[BROADCAST], content=event, msg_type="system_event", tick=world.tick)
        world.message_bus.send(msg)

    async def _generate_llm_event(self, world: "WorldState", llm_client: "LLMClient") -> list[str] | None:
        """ReAct 循环：让 LLM 连续调用工具生成事件"""
        system_prompt = self._build_gm_prompt()
        validation_context = {
            "locations": world.locations,
            "agent_names": list(world.agents.keys()),
        }
        messages = [{"role": "user", "content": self._build_world_context(world)}]

        all_events: list[str] = []
        for turn in range(self.MAX_TURNS):
            _, actions = await llm_client.call_multi(
                system_prompt=system_prompt,
                messages=messages,
                action_registry=self.registry,
                agent_name="GM",
                tick=world.tick,
                validation_context=validation_context,
                allow_no_tool=True,
            )
            if not actions:
                break

            turn_results = []
            for action in actions:
                result = self._dispatch(action, world)
                if result:
                    turn_results.append(result)
                    all_events.append(result)

            if not turn_results:
                break

            text = f"执行完成：{' | '.join(turn_results)}"
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": "如需继续使用工具请调用，否则直接回复'完成'。"})

        return all_events if all_events else None

    def _build_gm_prompt(self) -> str:
        """构建 GM 的 system prompt，自动追加可用工具"""
        lines = []
        if self.llm_prompt:
            lines.append(self.llm_prompt)
        if self.world_description:
            lines.append("")
            lines.append(self.world_description)
        lines.append("")
        lines.append("规则：")
        lines.append("- 不要生成和近期事件冲突或简单重复的事件，可以是新事件或对近期事件的后续")
        lines.append("- 不要替角色做决定或直接控制角色的行为")
        lines.append("- 事件要简短自然，一句话")
        lines.append("- 只生成一个事件。可以多次调用工具，但所有调用都围绕同一个事件")
        lines.append("")
        lines.append("你可以使用以下工具（可一次调用多个）：")
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

        env_lines = []
        for loc in world.locations:
            summary = world.get_environment_summary(loc)
            if summary:
                env_lines.append(f"  {loc}: {summary}")
        if env_lines:
            parts.append("\n环境状态：")
            parts.extend(env_lines)

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

    def _dispatch(self, action, world: "WorldState") -> str | None:
        """根据 action_type 分发到对应的 handler"""
        handler = {
            "generate_event": self._handle_event,
            "modify_environment": self._handle_modify_env,
        }.get(action.action_type)
        if handler:
            return handler(action, world)
        return None

    def _handle_event(self, action, world: "WorldState") -> str | None:
        """处理 generate_event action"""
        return action.content if action.content else None

    def _handle_modify_env(self, action, world: "WorldState") -> str | None:
        """处理 modify_environment action"""
        loc = action.params.get("location", "")
        key = action.params.get("key", "")
        value = action.params.get("value", "")
        if not loc or not key or not value:
            return None
        err = world.update_environment(loc, key, value)
        if err:
            return err
        return f"环境变更: {loc}.{key} → {value}"
