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
    ):
        self.name = name
        self.role = role
        self.personality = personality
        self.goal = goal
        self.location = location
        self.relationships = relationships
        self.memory = memory

        self.mood = "平静"
        self.energy = 100

    def build_system_prompt(self, registry: "ActionRegistry") -> str:
        """构建 System Prompt"""

        action_names = ", ".join(registry.get_action_names())

        relations_text = "\n".join(
            [f"- {name}: {rel.get('impression', '')}" for name, rel in self.relationships.items()]
        )

        return f"""## 你是谁
你是 {self.name}（{self.role}）。{self.personality}

## 你的目标
{self.goal}

## 你能做的事
每次选择一个行动类型：{action_names}
- speak: 对某人或所有人说话
- whisper: 悄悄话（只有目标听到）
- move: 移动到另一个位置
- observe: 观察 surroundings（不做其他事）
- interact: 与物品/环境互动

## 你和其他人的关系
{relations_text if relations_text else "暂无"}

## 输出要求
调用 act 工具，包含:
- action_type, target, content
- internal_monologue: 你的内心独白（别人看不到）"""

    async def perceive(self, world: "WorldState") -> str:
        """感知：收集消息 + 环境 + 记忆"""

        parts = []

        inbox = world.message_bus.get_inbox(self.name)

        if inbox:
            msgs_text = "\n".join(
                [
                    f"- [{msg.msg_type}] {msg.sender}: {msg.content[:50]}"
                    for msg in inbox[-5:]
                ]
            )
            parts.append(f"【你收到的消息】\n{msgs_text}")

        location_agents = world.get_agents_in_location(self.location)
        if self.name in location_agents:
            location_agents.remove(self.name)

        parts.append(f"【当前环境】\n位置: {self.location} | 这里的人: {', '.join(location_agents) if location_agents else '无'}")

        parts.append(f"【你的状态】\n情绪: {self.mood} | 精力: {self.energy}")

        memory_context = self.memory.get_context()
        if memory_context:
            parts.append(memory_context)

        return "\n\n".join(parts)

    async def think(
        self,
        llm: "LLMClient",
        registry: "ActionRegistry",
        context: str,
    ) -> "Action":
        """思考：调用 LLM 决策"""

        system_prompt = self.build_system_prompt(registry)

        messages = [{"role": "user", "content": context}]

        _, action = await llm.call(
            system_prompt=system_prompt,
            messages=messages,
            action_registry=registry,
            temperature=0.7,
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
                messages = action_spec.execute(self.name, {"target": action.target, "content": action.content, **action.params}, world)
                return messages
            except Exception as e:
                print(f"[{self.name}] 执行 action 失败: {e}")

        return []

    def modify_trust(self, other: str, delta: int):
        """修改对某人的信任度"""
        if other in self.relationships:
            self.relationships[other]["trust"] = max(-5, min(5, self.relationships[other].get("trust", 0) + delta))