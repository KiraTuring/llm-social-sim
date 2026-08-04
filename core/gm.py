"""GM Agent：注入事件，推进剧情。"""

import random
from typing import TYPE_CHECKING

from core.action import ActionRegistry
from core.actions.gm_tools import NarrateAction, ModifyEnvironmentAction, ModifyCharStateAction
from core.actions.gm_npc import NpcSpeakAction

if TYPE_CHECKING:
    from core.world import WorldState
    from llm.client import LLMClient


class GMAgent:
    """GM Agent：注入事件，推进剧情"""

    MAX_TURNS = 3

    def __init__(self, events: list, random_events: list, chance: float,
                 use_llm: bool = False, llm_chance: float = 0.0, llm_prompt: str = "",
                 world_description: str = "", logger=None, message_limit: int = 15,
                 prompt_format: str = "text", history_max_messages: int = 40,
                 gm_registry: ActionRegistry | None = None):
        self.scheduled_events = events
        self.random_events = random_events
        self.random_chance = chance
        self.use_llm = use_llm
        self.llm_chance = llm_chance
        self.llm_prompt = llm_prompt
        self.world_description = world_description
        self.logger = logger
        self.message_limit = message_limit
        self.prompt_format = prompt_format
        self.history_max_messages = history_max_messages
        self._gm_history: list[dict] = []

        if gm_registry is not None:
            self.registry = gm_registry
        else:
            self.registry = ActionRegistry(include_agent_params=False)
            self.registry.register(NarrateAction())
            self.registry.register(ModifyEnvironmentAction())
            self.registry.register(ModifyCharStateAction())
            self.registry.register(NpcSpeakAction())

    @classmethod
    def from_config(cls, scene, config, gm_registry=None):
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
            prompt_format=config["gm"].get("prompt_format", "text"),
            history_max_messages=config["gm"].get("chat_history_max_messages", 40),
            gm_registry=gm_registry,
        )

    def to_dict(self) -> dict:
        """序列化为可保存的 dict（存档用）。"""
        return {
            "scheduled_events": [list(item) for item in self.scheduled_events],
            "random_events": self.random_events,
            "use_llm": self.use_llm,
            "history": self._gm_history,
        }

    @classmethod
    def from_dict(cls, scene, config, data: dict) -> "GMAgent":
        """从存档恢复 GM：from_config 构造后应用运行时字段。"""
        gm = cls.from_config(scene, config)
        gm.scheduled_events = [tuple(item) for item in data["scheduled_events"]]
        gm.random_events = data["random_events"]
        gm.use_llm = data.get("use_llm", config["gm"]["use_llm"])
        gm._gm_history = data.get("history", [])
        return gm

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
            recent = world.message_bus.get_recent(50)
            trigger = any(
                (m.msg_type == "interact" or
                 m.msg_type == "speech" and m.target in world.npc_names)
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

        msg = Message(sender="GM", recipients=[BROADCAST], content=event, msg_type="system_event", tick=world.tick)
        world.message_bus.send(msg)

    def _truncate_gm_history(self):
        """chat 模式：滑动窗口截断 GM 对话历史"""
        if len(self._gm_history) > self.history_max_messages:
            excess = len(self._gm_history) - self.history_max_messages
            self._gm_history = self._gm_history[excess:]
        while self._gm_history and self._gm_history[0].get("role") != "user":
            self._gm_history.pop(0)

    async def _generate_llm_event(self, world: "WorldState", llm_client: "LLMClient") -> None:
        """ReAct 循环：让 LLM 连续调用工具生成事件"""
        system_prompt = self._build_gm_prompt()
        validation_context = world.build_validation_context("GM")

        if self.prompt_format == "chat":
            messages = list(self._gm_history)
            messages.append({"role": "user", "content": self._build_world_context(world)})
        else:
            messages = [{"role": "user", "content": self._build_world_context(world)}]

        any_actions = False

        for turn in range(self.MAX_TURNS):
            def _exec(action):
                spec = self.registry.get(action.action_type)
                if not spec:
                    return f"未知工具: {action.action_type}"
                _, result = spec.execute("GM", action.params, world)
                from core.action import format_tool_result
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
        """构建 GM 的 system prompt，自动追加可用工具"""
        lines = []
        if self.llm_prompt:
            lines.append(self.llm_prompt)
        if self.world_description:
            lines.append("")
            lines.append(self.world_description)

        gm_rule_prompt = """
重要规则：
- 不要生成和近期事件冲突或简单重复的事件，可以是新事件或对近期事件的后续
- 禁止替玩家做决定或直接控制玩家的行为
- 禁止替玩家发言
- 禁止改变玩家的位置
- 禁止创造场景中不存在的位置——所有可用位置已在世界描述中列出
- 留意角色最近的消息，基于角色与环境的互动产生合理的事件响应或后续影响。注意你要回应的是交互行为(interact)和角色对 NPC 的对话(speech)，普通聊天不需要回应
- 事件要简短自然，一句话
- 最多同时生成一个新事件。可以多次调用工具，但所有调用都围绕同一个事件
"""
        lines.append(gm_rule_prompt)
        lines.append("")
        lines.append("注意：你在调用工具之前输出的任何对话文字都不会被其他角色看到，也不会对模拟产生任何影响，相当于内心独白。只有工具调用本身会影响环境和其他角色。")
        lines.append("你可以使用以下工具（可一次调用多个）：")
        lines.append(self.registry.describe(indent="  "))
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
