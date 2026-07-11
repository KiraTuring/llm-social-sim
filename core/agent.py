"""Agent 类：属性、记忆、perceive/think/act。"""

from typing import TYPE_CHECKING

from memory.memory import AgentMemory

if TYPE_CHECKING:
    from core.action import ActionRegistry
    from core.world import WorldState
    from llm.client import LLMClient
    from memory.memory import AgentMemory


class Agent:
    """Agent：感知→思考→行动"""

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
        self.name = name
        self.role = role
        self.personality = personality
        self.goal = goal
        self.location = location
        self.relationships = relationships
        self.memory = memory
        self.content_max_length = content_max_length
        self.inbox_limit = inbox_limit
        self.world_description = world_description
        self.instruction = instruction
        self.prompt_format = prompt_format

        self.states = dict(states) if states else {}
        self._writable_states = set(writable_states) if writable_states else set()
        self._private_states = set(private_states) if private_states else set()
        self._last_action = None
        self._last_observed_result: str = ""
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

    def build_system_prompt(self, registry: "ActionRegistry") -> str:
        """构建 System Prompt"""

        action_names = ", ".join(registry.get_action_names())

        desc_lines = "\n".join(
            f"- {name}: {registry.get(name).description}" for name in registry.get_action_names()
        )

        relations_text = "\n".join(
            [f"- {name}: 信任度 {rel.get('trust', 0)}，印象「{rel.get('impression', '')}」"
             for name, rel in self.relationships.items()]
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

        parts = []

        inbox = world.message_bus.get_inbox(self.name)
        max_len = self.content_max_length

        location_agents = world.get_agents_in_location(self.location)
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

        if inbox:
            msgs_text_lines = []
            recent_inbox = inbox[-self.inbox_limit:]
            for msg in recent_inbox:
                truncated = msg.content[:max_len]
                sender_part = f"{msg.sender}" + (f" -> {msg.target}" if msg.target else "")
                sender_part = sender_part.replace(self.name, "你")
                msgs_text = f"[{msg.msg_type}] {sender_part}: {truncated}"
                msgs_text_lines.append(f"- {msgs_text}")

                self.memory.add(msgs_text, tick=world.tick)

            parts.append(f"【你得到的新信息】\n" + "\n".join(msgs_text_lines))

        self._perceived_inbox = [{"sender": msg.sender, "content": msg.content[:max_len], "target": msg.target}
                                 for msg in inbox]

        world.message_bus.clear_inbox(self.name)

        if self.prompt_format == "text" and self._last_action:
            parts.append(f"【你上一tick的行动】\n{self._last_action} \n不要重复刚才的行动。")

        result = "\n\n".join(parts)

        if self.memory._compress_needed and llm_client:
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
                        old_trust = self.relationships[name].get("trust", 0)
                        self.relationships[name]["trust"] += changes.get("trust_change", 0)
                        self.relationships[name]["trust"] = max(-5, min(5, self.relationships[name]["trust"]))
                        if "impression" in changes:
                            self.relationships[name]["impression"] = changes["impression"]
                        if llm_client.logger:
                            llm_client.logger.debug(
                                f"关系变化: {self.name}→{name} "
                                f"信任 {old_trust}→{self.relationships[name]['trust']} "
                                f"{', 印象更新' if 'impression' in changes else ''}"
                            )
            except Exception:
                pass

            if self.prompt_format == "chat":
                self._truncate_chat_history()

        return result

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
            from core.action import Action

            action = Action(action_type="observe", content="观察四周", internal_monologue="...")

        return action

    async def act(self, action: "Action", world: "WorldState", registry: "ActionRegistry") -> list:
        """行动：执行 Action"""

        action_spec = registry.get(action.action_type)

        if action_spec:
            try:
                messages, result = action_spec.execute(self.name, {"target": action.target, "content": action.content, **action.params}, world)

                action.result = result

                # Apply state_update from LLM (only writable states)
                su = action.params.get("state_update", action.state_update or {})
                if isinstance(su, dict):
                    for key, val in su.items():
                        if key in self._writable_states:
                            self.states[key] = val

                if result:
                    for key, value in result.items():
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
                    self._chat_history.append(self._build_assistant_msg(action, world.tick))

                return messages
            except Exception as e:
                print(f"[{self.name}] 执行 action 失败: {e}")

        return []

    def _build_last_action(self, action: "Action", world: "WorldState"):
        """构建上一步行动的简单描述"""
        parts = [f"[{action.action_type}]"]
        if action.target:
            parts.append(f"-> {action.target}")
        c = action.content[:self.content_max_length]
        if c:
            parts.append(f": {c}")
        self._last_action = " ".join(parts)

    def _build_assistant_msg(self, action: "Action", tick: int) -> dict:
        """chat 模式：构建 assistant 角色消息，表示 agent 的执行记录"""
        parts = [f"[{action.action_type}]"]
        if action.target:
            parts.append(f"-> {action.target}")
        c = action.content[:self.content_max_length]
        if c:
            parts.append(f": {c}")
        return {"role": "assistant", "content": " ".join(parts), "tick": tick}

    def modify_trust(self, other: str, delta: int):
        """修改对某人的信任度"""
        if other in self.relationships:
            self.relationships[other]["trust"] = max(-5, min(5, self.relationships[other].get("trust", 0) + delta))
