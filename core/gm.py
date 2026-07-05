"""GM Agent：注入事件，推进剧情。"""

import random
from typing import TYPE_CHECKING

from core.action import ActionRegistry
from core.actions.gm_tools import NarrateAction, ModifyEnvironmentAction, ModifyCharStateAction

if TYPE_CHECKING:
    from core.world import WorldState
    from llm.client import LLMClient


class GMAgent:
    """GM Agent：注入事件，推进剧情"""

    MAX_TURNS = 3

    def __init__(self, events: list, random_events: list, chance: float,
                 use_llm: bool = False, llm_chance: float = 0.0, llm_prompt: str = "",
                 world_description: str = "", logger=None, message_limit: int = 15):
        self.scheduled_events = events
        self.random_events = random_events
        self.random_chance = chance
        self.use_llm = use_llm
        self.llm_chance = llm_chance
        self.llm_prompt = llm_prompt
        self.world_description = world_description
        self.logger = logger
        self.message_limit = message_limit

        self.registry = ActionRegistry(include_state_update=False)
        self.registry.register(NarrateAction())
        self.registry.register(ModifyEnvironmentAction())
        self.registry.register(ModifyCharStateAction())

    @classmethod
    def from_config(cls, scene, config):
        """从 scene 配置和模拟配置构建 GMAgent。"""
        gm_cfg = scene.get_gm_config()
        return cls(
            events=gm_cfg["events"],
            random_events=gm_cfg["random_events"],
            chance=config["gm"]["random_event_chance"],
            use_llm=config["gm"]["use_llm"],
            llm_chance=config["gm"].get("llm_event_chance", 0.3),
            llm_prompt=gm_cfg.get("llm_prompt", ""),
            world_description=scene.world_description,
            message_limit=config["gm"].get("message_limit", 15),
        )

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
            had_interact = any(
                m.msg_type == "interact" and m.tick == world.tick - 1
                for m in world.message_bus.get_recent(50)
            )
            if had_interact or random.random() < self.llm_chance:
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

        msg = Message(sender="GM", recipients=[BROADCAST], content=event, msg_type="system_event", tick=world.tick)
        world.message_bus.send(msg)

    async def _generate_llm_event(self, world: "WorldState", llm_client: "LLMClient") -> None:
        """ReAct 循环：让 LLM 连续调用工具生成事件"""
        system_prompt = self._build_gm_prompt()
        validation_context = {
            "locations": world.locations,
            "agent_names": list(world.agents.keys()),
        }
        messages = [{"role": "user", "content": self._build_world_context(world)}]

        for turn in range(self.MAX_TURNS):
            def _exec(action):
                spec = self.registry.get(action.action_type)
                if not spec:
                    return f"未知工具: {action.action_type}"
                _, result = spec.execute("GM", action.params, world)
                summary = (result or {}).get("summary", f"'{action.action_type}' 执行完成")
                world.add_event(summary)
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

            messages.append({"role": "user", "content": "如需继续使用工具请调用，否则直接回复'完成'。"})

    def _build_gm_prompt(self) -> str:
        """构建 GM 的 system prompt，自动追加可用工具"""
        lines = []
        if self.llm_prompt:
            lines.append(self.llm_prompt)
        if self.world_description:
            lines.append("")
            lines.append(self.world_description)
        lines.append("")
        lines.append("重要规则：")
        lines.append("- 不要生成和近期事件冲突或简单重复的事件，可以是新事件或对近期事件的后续")
        lines.append("- 不要替角色做决定或直接控制角色的行为")
        lines.append("- 不要改变角色的位置")
        lines.append("- 留意角色最近的消息，基于角色与环境的互动产生合理的事件响应或后续影响。注意你要回应的是交互行为(interact)而不是聊天(speak/whisper)")
        lines.append("- 事件要简短自然，一句话")
        lines.append("- 最多同时生成一个新事件。可以多次调用工具，但所有调用都围绕同一个事件")
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
            statuses = []
            for n in names:
                state_str = ", ".join(f"{k}:{v}" for k, v in world.agents[n].states.items())
                statuses.append(f"{n}({state_str})")
            parts.append(f"  {loc}: {', '.join(statuses)}")

        env_lines = []
        for loc in world.locations:
            summary = world.get_environment_summary(loc)
            if summary:
                env_lines.append(f"  {loc}: {summary}")
        if env_lines:
            parts.append("\n环境状态：")
            parts.extend(env_lines)

        if world.message_bus:
            msgs = []
            for m in world.message_bus.get_recent(self.message_limit):
                target_str = f" -> {m.target}" if m.target else ""
                msgs.append(f"  [tick {m.tick}] [{m.sender}] ({m.msg_type}{target_str}): {m.content}")
            if msgs:
                parts.append("\n最近收到的消息：")
                parts.extend(msgs)

        return "\n".join(parts)
