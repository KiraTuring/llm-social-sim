"""Agent 类：属性、记忆、perceive/think/act。"""

from typing import TYPE_CHECKING

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
        max_energy: int = 100,
        inbox_limit: int = 5,
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

        self.mood = "平静"
        self.energy = max_energy
        self._last_action = None

    def build_system_prompt(self, registry: "ActionRegistry") -> str:
        """构建 System Prompt"""

        action_names = ", ".join(registry.get_action_names())

        desc_lines = "\n".join(
            f"- {name}: {registry.get(name).description}" for name in registry.get_action_names()
        )

        relations_text = "\n".join(
            [f"- {name}: {rel.get('impression', '')}" for name, rel in self.relationships.items()]
        )

        return f"""## 你是谁
你是 {self.name}（{self.role}）。{self.personality}

## 你的目标
{self.goal}

## 你能做的事
行动类型: {action_names}
{desc_lines}

## 你和其他人的关系
{relations_text if relations_text else "暂无"}

## 输出要求
选择要执行的行动工具: {action_names}
所有工具都包含可选的 internal_monologue 字段（内心独白，别人看不到）"""

    async def perceive(self, world: "WorldState") -> str:
        """感知：收集消息 + 环境 + 记忆"""

        parts = []

        inbox = world.message_bus.get_inbox(self.name)
        max_len = self.content_max_length

        location_agents = world.get_agents_in_location(self.location)
        if self.name in location_agents:
            location_agents.remove(self.name)

        visible_locs = [self.location] + world.visibility.get(self.location, [])
        loc_agents_text = ', '.join(location_agents) if location_agents else '无'
        visible_text = ', '.join(visible_locs)

        parts.append(f"【当前环境】\n位置: {self.location} | 这里的人: {loc_agents_text} | 你能观察到: {visible_text}")

        parts.append(f"【你的状态】\n情绪: {self.mood} | 精力: {self.energy}")

        memory_context = self.memory.get_context()
        if memory_context:
            parts.append(memory_context)

        if inbox:
            msgs_text_lines = []
            recent_inbox = inbox[-self.inbox_limit:]
            for msg in recent_inbox:
                truncated = msg.content[:max_len]
                sender_part = f"{msg.sender}" + (f" -> {msg.target}" if msg.target else "")
                msgs_text_lines.append(f"- [{msg.msg_type}] {sender_part}: {truncated}")
            parts.append(f"【你收到的消息】\n" + "\n".join(msgs_text_lines))

        if inbox:
            for msg in recent_inbox:
                truncated = msg.content[:max_len]
                sender_part = f"{msg.sender}" + (f" -> {msg.target}" if msg.target else "")
                self.memory.add(f"[{msg.msg_type}] {sender_part}: {truncated}")

        self._perceived_inbox = [{"sender": msg.sender, "content": msg.content[:max_len], "target": msg.target}
                                  for msg in inbox]

        world.message_bus.clear_inbox(self.name)

        if self._last_action:
            parts.append(f"【你刚才的行动】\n{self._last_action}")

        return "\n\n".join(parts)

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

                if result:
                    for key, value in result.items():
                        self.memory.add(f"[{key}] {value}")
                else:
                    summary = f"[{action.action_type}] {self.name}: {action.content[:self.content_max_length]}"
                    if action.target:
                        summary += f" (目标: {action.target})"
                    self.memory.add(summary)

                self._build_last_action(action, world)
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
        self._last_action = "你 " + " ".join(parts)

    def modify_trust(self, other: str, delta: int):
        """修改对某人的信任度"""
        if other in self.relationships:
            self.relationships[other]["trust"] = max(-5, min(5, self.relationships[other].get("trust", 0) + delta))
