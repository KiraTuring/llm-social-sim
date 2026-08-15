"""Agent 类：属性、记忆、perceive/think/act。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.action import Action, ActionRegistry, format_tool_result
from core.character import Character
from memory.memory import AgentMemory

if TYPE_CHECKING:
    from core.world import WorldState
    from llm.client import LLMClient
    from memory.memory import AgentMemory


class Agent(Character):
    """Agent：感知→思考→行动（自主行动者，继承 Character 的位置/状态）"""

    agent_type = "Agent"
    # 关系属性边界：声明 (min, max) 的数值属性采用增量语义并夹取；未声明的属性直接赋值
    relationship_attr_bounds: dict[str, tuple[int, int]] = {"trust": (-5, 5)}

    def __init__(
        self,
        name: str,
        role: str,
        personality: str,
        goal: str,
        location: str,
        relationships: dict,
        memory: "AgentMemory",
        content_max_length: int = 200,
        inbox_limit: int = 5,
        world_description: str = "",
        states: dict | None = None,
        writable_states: set | None = None,
        private_states: set | None = None,
        instruction: str = "",
        prompt_format: str = "text",
    ):
        super().__init__(
            name=name, location=location, role=role,
            personality=personality, goal=goal, states=states,
        )
        self.relationships = relationships
        self.memory = memory
        self.content_max_length = content_max_length
        self.inbox_limit = inbox_limit
        self.world_description = world_description
        self.instruction = instruction
        self.prompt_format = prompt_format

        self._writable_states = set(writable_states) if writable_states else set()
        self._private_states = set(private_states) if private_states else set()
        self._last_action = None
        self._last_observed_result: str = ""
        self._perceived_inbox: list[dict] = []
        self._chat_history: list[dict] = []
        self._pending_user_msg: dict | None = None

    @classmethod
    def from_config(cls, scene, cfg, config, *, saved=None, **extra):
        """创建 Agent。

        saved=None: 全新 agent，从 scene+cfg 构建
        saved=agent_data: 从存档恢复，scene 提供静态字段，saved 提供运行时状态
        """
        prompt_format = config["agent"].get("prompt_format", "text")
        base = dict(
            name=cfg["name"],
            role=cfg["role"],
            personality=cfg["personality"],
            goal=cfg["goal"],
            world_description=scene.world_description,
            instruction=scene.instruction,
            inbox_limit=config["agent"].get("inbox_limit", 5),
            prompt_format=prompt_format,
        )

        if saved is None:
            runtime = dict(
                location=cfg["location"],
                relationships=cfg["relationships"],
                memory=AgentMemory(
                    name=cfg["name"],
                    short_limit=config["agent"]["memory_short_limit"],
                    compress_threshold=config["agent"]["memory_compress_threshold"],
                ),
                content_max_length=config["agent"].get("content_max_length", 200),
                states=({**dict(scene.states or {}), **dict(cfg.get("states") or {})}),
                writable_states=set(cfg.get("writable_states") or scene.writable_states or []),
                private_states=set(cfg.get("private_states") or scene.private_states or []),
            )
        else:
            runtime = dict(
                location=saved.get("location", cfg["location"]),
                relationships=saved.get("relationships", cfg["relationships"]),
                memory=AgentMemory.from_dict(
                    saved["memory"],
                    name=cfg["name"],
                    short_limit=config["agent"]["memory_short_limit"],
                    compress_threshold=config["agent"]["memory_compress_threshold"],
                ),
                content_max_length=saved.get("content_max_length", 200),
                states=saved.get("states", {}),
                writable_states=set(saved.get("writable_states", [])),
                private_states=set(saved.get("private_states", [])),
            )

        agent = cls(**base, **runtime, **extra)

        if saved is not None:
            agent._last_observed_result = saved.get("last_observed_result", "")
            agent._chat_history = saved.get("chat_history", [])

        return agent

    def to_dict(self) -> dict:
        """序列化为可保存的 dict（存档用，格式与 save_load 历史 shape 一致）"""
        return {
            "role": self.role,
            "personality": self.personality,
            "goal": self.goal,
            "location": self.location,
            "relationships": self.relationships,
            "states": self.states,
            "writable_states": list(self._writable_states) if self._writable_states else [],
            "private_states": list(self._private_states) if self._private_states else [],
            "content_max_length": self.content_max_length,
            "agent_type": self.agent_type,
            "last_observed_result": self._last_observed_result,
            "prompt_format": self.prompt_format,
            "chat_history": self._chat_history,
            "memory": self.memory.to_dict(),
        }

    @property
    def perceived_inbox(self) -> list[dict]:
        """本 tick perceive 读取到的收件箱消息（渲染用，返回副本）。"""
        return list(self._perceived_inbox)

    @property
    def writable_states(self) -> set:
        """可写状态名集合（只读，渲染/展示用）。"""
        return set(self._writable_states)

    @property
    def private_states(self) -> set:
        """私有状态名集合（只读，渲染/展示用）。"""
        return set(self._private_states)

    @property
    def last_observed_result(self) -> str:
        """最近一次观察结果（只读，渲染/展示用）。"""
        return self._last_observed_result

    def recent_memories(self, limit: int = 5) -> list[dict]:
        """最近 limit 条记忆（渲染用，返回副本）。"""
        return self.memory.recent(limit)

    def build_system_prompt(self, registry: "ActionRegistry") -> str:
        """构建 System Prompt"""

        action_names = ", ".join(registry.get_action_names())

        desc_lines = registry.describe()

        relations_text = "\n".join(
            f"- {name}: " + "，".join(f"{key}: {value}" for key, value in rel.items())
            for name, rel in self.relationships.items()
        )

        world_part = f"\n\n## 世界\n{self.world_description}" if self.world_description else ""

        prompt = f"""## 模拟规则
你在扮演 {self.name}（{self.role}），在一个持续运行的社交模拟世界中进行角色扮演。
模拟以 tick 为单位推进，每个 tick 你可以执行一次行动。{world_part}

注意：你在调用工具之前输出的任何对话文字都不会被其他角色看到，也不会对模拟产生任何影响。只有工具调用本身会改变环境和其他角色。

记忆：你过去做的事、说的话和观察到的情况会被记住，在「你最近记得的事」中显示。

其他角色和你一样自主行动——你有自己的目标和性格，他们也有。

行动顺序：所有角色在同一 tick 内按固定顺序依次行动。排在后面的角色可以看到前面角色的行动（说话、移动等），但排在前面的角色要等到下一 tick 才能知道后面的人做了什么。

## 你是谁
你是 {self.name}（{self.role}）。{self.personality}

## 你的目标
{self.goal}

## 你能做的事
行动类型: {action_names}
{desc_lines}

## 你和其他人的关系
{relations_text if relations_text else "暂无"}

## 输出要求
优先选择一个工具来行动。如果你在思考、等人回复、或没有明确可做的事，用 think 工具代替 observe。
所有工具都包含可选的 internal_monologue 字段（内心独白，别人看不到）。"""

        if self.instruction:
            prompt += f"\n\n{self.instruction}"

        return prompt

    async def perceive(self, world: "WorldState", llm_client: "LLMClient | None" = None) -> str:
        """感知：收集消息 + 环境 + 记忆"""
        inbox_lines = self._ingest_inbox(world)
        result = self._build_context(world, inbox_lines)
        if self.memory._compress_needed and llm_client:
            await self._maybe_compress(llm_client)
        return result

    def _ingest_inbox(self, world: "WorldState") -> list[str]:
        """读取收件箱：截断写入记忆、记录 _perceived_inbox、清空，返回提示用行。"""
        inbox = world.message_bus.get_inbox(self.name)
        max_len = self.content_max_length

        lines = []
        for msg in inbox[-self.inbox_limit:]:
            truncated = msg.content[:max_len]
            sender_part = f"{msg.sender}" + (f" -> {msg.target}" if msg.target else "")
            sender_part = sender_part.replace(self.name, "你")
            msgs_text = f"[{msg.msg_type}] {sender_part}: {truncated}"
            lines.append(f"- {msgs_text}")
            self.memory.add(msgs_text, tick=world.tick)

        self._perceived_inbox = [{"sender": msg.sender, "content": msg.content[:max_len], "target": msg.target}
                                 for msg in inbox]
        world.message_bus.clear_inbox(self.name)
        return lines

    def _build_context(self, world: "WorldState", inbox_lines: list[str]) -> str:
        """组装感知上下文 prompt：环境 → 状态 → 记忆 → 新信息 → 上一行动。"""
        parts = []

        location_agents = world.get_characters_in_location(self.location)
        if self.name in location_agents:
            location_agents.remove(self.name)

        visible_locs = world.get_visible_locations(self.location)
        loc_agents_text = ', '.join(location_agents) if location_agents else '无'
        visible_text = ', '.join(visible_locs)
        adjacent = world.get_adjacent_locations(self.location)
        adjacent_text = ', '.join(adjacent) if adjacent else '无'

        parts.append(f"【当前环境】\n你的位置: {self.location}, 这里的人: {loc_agents_text} | 你能观察到: {visible_text} | 可前往: {adjacent_text}")

        state_str = " | ".join(f"{k}: {v}" for k, v in self.states.items())
        parts.append(f"【你的状态】\n{state_str}")

        if self.prompt_format == "text":
            memory_context = self.memory.get_context()
            if memory_context:
                parts.append(memory_context)

        if inbox_lines:
            parts.append("【你得到的新信息】\n" + "\n".join(inbox_lines))

        if self.prompt_format == "text" and self._last_action:
            parts.append(f"【你上一tick的行动】\n{self._last_action} \n不要重复刚才的行动。")

        return "\n\n".join(parts)

    async def _maybe_compress(self, llm_client: "LLMClient") -> None:
        """记忆压缩 + 关系更新 + chat 历史截断（LLM 失败静默跳过）。"""
        if llm_client.logger:
            llm_client.logger.debug(
                f"{self.name} 触发记忆压缩 ({len(self.memory._short_term)} 条)"
            )
        try:
            rel_updates = await self.memory.compress(llm_client, relationships=self.relationships)
            if rel_updates and isinstance(rel_updates, dict):
                for name, changes in rel_updates.items():
                    if name not in self.relationships:
                        continue
                    # 兼容旧版压缩输出的 trust_change 键
                    if "trust_change" in changes:
                        changes = dict(changes)
                        changes["trust"] = changes.pop("trust_change")
                    self.update_relationship(name, changes)
                    if llm_client.logger:
                        llm_client.logger.debug(
                            f"关系变化: {self.name}→{name} {changes}"
                        )
        except Exception:
            pass

        if self.prompt_format == "chat":
            self._truncate_chat_history()

    def _build_chat_messages(self, current_context: str, tick: int) -> list[dict]:
        """chat 模式：组装多轮消息。summary 插在最前（system 之后），chat_history 居中。"""
        messages = list(self._chat_history)
        messages.append({"role": "user", "content": current_context, "tick": tick})

        if self.memory._summary:
            messages.insert(0, {"role": "user", "content": f"【你的过去】\n{self.memory._summary}"})

        return messages

    def _truncate_chat_history(self):
        """压缩后截断 chat_history：只保留 _short_term 中最早 tick 之后的条目。"""
        if not self.memory._short_term:
            return
        oldest_tick = min(e["tick"] for e in self.memory._short_term)
        self._chat_history = [e for e in self._chat_history if e.get("tick", 0) >= oldest_tick]

    async def think(
        self,
        llm: "LLMClient",
        registry: "ActionRegistry",
        context: str,
        tick: int = 0,
        validation_context: dict | None = None,
    ) -> "Action":
        """思考：调用 LLM 决策"""

        system_prompt = self.build_system_prompt(registry)

        if self.prompt_format == "chat":
            messages = self._build_chat_messages(context, tick)
        else:
            messages = [{"role": "user", "content": context}]

        _, action = await llm.call(
            system_prompt=system_prompt,
            messages=messages,
            action_registry=registry,
            temperature=0.7,
            agent_name=self.name,
            tick=tick,
            validation_context=validation_context,
        )

        if action and self.prompt_format == "chat":
            self._pending_user_msg = {"role": "user", "content": context, "tick": tick}

        if not action:
            print(f"[{self.name}] LLM 未返回 Action，使用默认")

            action = Action(action_type="observe", content="观察四周", internal_monologue="...")

        return action

    async def act(self, action: "Action", world: "WorldState", registry: "ActionRegistry") -> list:
        """执行 Action 并记录结果。返回产生的消息；执行失败返回空列表。"""
        action_spec = registry.get(action.action_type)
        if action_spec is None:
            print(f"[{self.name}] 未知行动类型: {action.action_type}")
            return []

        try:
            messages = self._execute_action(action_spec, action, world)
        except Exception as e:
            self._record_failure(action, e)
            return []

        self._record_action(action, world)
        return messages

    def _execute_action(
        self, action_spec, action: "Action", world: "WorldState"
    ) -> list:
        """调用 ActionSpec 执行行动，返回产生的消息。

        失败时抛异常，由 act() 统一记录与兜底。
        """
        messages, result = action_spec.execute(
            self.name,
            {"target": action.target, "content": action.content, **action.params},
            world,
        )
        action.result = result
        return messages

    def _record_action(self, action: "Action", world: "WorldState") -> None:
        """记录行动副作用：状态更新、记忆、上一行动、chat 历史。"""
        # Apply state_update from LLM (only writable states)
        su = action.params.get("state_update", action.state_update or {})
        if isinstance(su, dict):
            for key, val in su.items():
                if key in self._writable_states:
                    self.states[key] = val

        if action.result:
            for key, value in action.result.items():
                self.memory.add(f"[{key}] {value}", tick=world.tick)
        else:
            summary = f"[{action.action_type}] 你: {action.content[:self.content_max_length]}"
            if action.target:
                summary += f" (目标: {action.target})"
            self.memory.add(summary, tick=world.tick)

        self._build_last_action(action, world)

        if self.prompt_format == "chat":
            if self._pending_user_msg:
                self._chat_history.append(self._pending_user_msg)
                self._pending_user_msg = None
            self._chat_history.extend(self._build_chat_entries(action, world.tick))

    def _record_failure(self, action: "Action", error: Exception) -> None:
        """行动失败：错误写入 action.result（日志/UI 可见），并清理悬空消息。"""
        action.result = {"error": str(error)}
        self._pending_user_msg = None
        print(f"[{self.name}] 执行 action 失败: {error}")

    def _build_last_action(self, action: "Action", world: "WorldState"):
        """构建上一步行动的简单描述"""
        parts = [f"[{action.action_type}]"]
        if action.target:
            parts.append(f"-> {action.target}")
        c = action.content[:self.content_max_length]
        if c:
            parts.append(f": {c}")
        self._last_action = " ".join(parts)

    def _build_chat_entries(self, action: "Action", tick: int) -> list[dict]:
        """chat 模式：构建聊天历史条目。

        tool_call 模式 → assistant(含 tool_calls) + tool(result) 消息对
        text_parse 模式 → assistant(LLM 原始文本)
        fallback       → assistant(文本标签格式)
        """
        entries = []
        if action.raw_tool_calls:
            entries.append({
                "role": "assistant",
                "content": action.raw_content,
                "tool_calls": action.raw_tool_calls,
                "tick": tick,
            })
            for tc in action.raw_tool_calls:
                entries.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": self._tool_result_summary(action),
                    "tick": tick,
                })
        elif action.raw_content:
            entries.append({
                "role": "assistant",
                "content": action.raw_content,
                "tick": tick,
            })
        else:
            parts = [f"[{action.action_type}]"]
            if action.target:
                parts.append(f"-> {action.target}")
            c = action.content[:self.content_max_length]
            if c:
                parts.append(f": {c}")
            entries.append({
                "role": "assistant",
                "content": " ".join(parts),
                "tick": tick,
            })
        return entries

    def _tool_result_summary(self, action: "Action") -> str:
        """从 action.result 构建工具返回摘要"""
        return format_tool_result(action.action_type, action.result, self.content_max_length)

    def update_relationship(self, other: str, changes: dict) -> None:
        """通用关系属性更新：有界数值属性按增量累加并夹取，其余属性直接赋值。

        边界由类级 relationship_attr_bounds 声明（如 trust: (-5, 5)）；
        未声明的属性（如 impression）直接赋值。
        """
        if other not in self.relationships:
            return
        for key, value in changes.items():
            bounds = self.relationship_attr_bounds.get(key)
            if bounds is not None and isinstance(value, (int, float)):
                lo, hi = bounds
                current = self.relationships[other].get(key, 0)
                self.relationships[other][key] = max(lo, min(hi, current + value))
            else:
                self.relationships[other][key] = value
