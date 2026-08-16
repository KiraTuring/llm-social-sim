"""GM Agent：注入事件，推进剧情。"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from core.action import ActionRegistry, format_tool_result
from core.message import BROADCAST, MSG_INTERACT, MSG_SYSTEM_EVENT, Message

if TYPE_CHECKING:
    from core.world import WorldState
    from llm.client import LLMClient


class GMAgent:
    """GM Agent：注入事件，推进剧情"""

    MAX_TURNS = 3

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

    @classmethod
    def from_config(cls, scene, config, gm_registry: ActionRegistry):
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
            event_tick_window=config["gm"].get("event_tick_window", 3),
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
    def from_dict(cls, scene, config, data: dict, gm_registry: ActionRegistry) -> "GMAgent":
        """从存档恢复 GM：from_config 构造后应用运行时字段。"""
        gm = cls.from_config(scene, config, gm_registry)
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
                (m.msg_type == MSG_INTERACT or
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
        msg = Message(sender="GM", recipients=[BROADCAST], content=event, msg_type=MSG_SYSTEM_EVENT, tick=world.tick)
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
                _, result = spec.execute("GM", action.params, world)
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
        """构建 GM 的 system prompt，自动追加可用工具"""
        lines = []
        if self.llm_prompt:
            lines.append(self.llm_prompt)
        if self.world_description:
            lines.append("")
            lines.append(self.world_description)

        has_npc = bool({"npc_speak", "npc_move", "npc_remove"} & set(self.registry.get_action_names()))

        response_rule = (
            "- 留意角色最近的消息，基于角色与环境的互动产生合理的事件响应或后续影响。"
            "注意你要回应的是交互行为(interact)和角色对 NPC 的对话(speech)，普通聊天不需要回应"
            if has_npc
            else "- 留意角色最近的消息，基于角色与环境的互动产生合理的事件响应或后续影响。"
            "注意你要回应的是交互行为(interact)，普通聊天不需要回应"
        )
        gm_rule_prompt = f"""
重要规则：
- 不要生成和近期事件冲突或简单重复的事件，可以是新事件或对近期事件的后续
- 禁止创造场景中不存在的位置——所有可用位置已在世界描述中列出
{response_rule}
- 事件要简短自然，一句话
- 最多同时生成一个新事件。可以多次调用工具，但所有调用都围绕同一个事件
"""
        lines.append(self._gm_role_rules())
        lines.append(gm_rule_prompt)
        lines.append("")
        lines.append("注意：你在调用工具之前输出的任何对话文字都不会被其他角色看到，也不会对模拟产生任何影响，相当于内心独白。只有工具调用本身会影响环境和其他角色。")
        lines.append("你可以使用以下工具（可一次调用多个）：")
        lines.append(self.registry.describe(indent="  "))
        return "\n".join(lines)

    def _gm_role_rules(self) -> str:
        """按场景是否有 NPC 生成角色控制权规则（无 NPC 场景不自相矛盾）。"""
        tool_names = set(self.registry.get_action_names())
        if {"npc_speak", "npc_move", "npc_remove"} & tool_names:
            return (
                "角色分两类：NPC 由你控制（说话用 npc_speak、移动用 npc_move、"
                "移除用 npc_remove）；Player（玩家）是自主角色，禁止替其做决定、发言或改变位置"
            )
        return "本场景没有 NPC，所有角色都是自主 Player，禁止替任何角色做决定、发言或改变位置"

    def _build_world_context(self, world: "WorldState") -> str:
        """构建世界状态上下文（中等粒度）"""
        parts = [f"当前是第 {world.tick} 个时间步。"]

        locs = {}
        for name, char in world.characters.items():
            locs.setdefault(char.location, []).append(name)

        has_npc = bool(world.npcs)
        if has_npc:
            parts.append("\n角色位置与状态（Player 自主行动，NPC 由你控制）：")
        else:
            parts.append("\n角色位置与状态（Player 自主行动）：")
        for loc, names in locs.items():
            statuses = []
            for n in names:
                state_str = ", ".join(f"{k}:{v}" for k, v in world.characters[n].states.items())
                tag = " [NPC]" if (has_npc and n in world.npcs) else (" [Player]" if has_npc else "")
                statuses.append(f"{n}{tag}({state_str})")
            parts.append(f"  {loc}: {', '.join(statuses)}")

        env_lines = []
        for loc in world.locations:
            summary = world.get_environment_summary(loc)
            if summary:
                env_lines.append(f"  {loc}: {summary}")
        if env_lines:
            parts.append("\n环境状态：")
            parts.extend(env_lines)

        events = world.event_log_for_last_ticks(self.event_tick_window)
        if events:
            lines = []
            for e in events:
                lines.append(f"  [tick {e.tick}] {e.text}")
            parts.append("\n最近事件：")
            parts.extend(lines)

        return "\n".join(parts)
