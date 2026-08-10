"""角色基类：Agent 与 NPC 的公共抽象。"""

from __future__ import annotations

from typing import Any


class Character:
    """角色基类：拥有位置和状态。

    Agent（自主行动者，有记忆/LLM 决策）与 NPC（GM 控制的轻量角色）
    都继承自本类，使位置/状态访问、可见性、参数校验等接口统一。
    """

    def __init__(
        self,
        name: str,
        location: str,
        role: str = "",
        personality: str = "",
        goal: str = "",
        states: dict[str, Any] | None = None,
    ):
        self.name = name
        self.location = location
        self.role = role
        self.personality = personality
        self.goal = goal
        self.states = dict(states) if states else {}

    def to_dict(self) -> dict:
        """序列化为可保存的 dict"""
        return {
            "name": self.name,
            "location": self.location,
            "role": self.role,
            "personality": self.personality,
            "goal": self.goal,
            "states": self.states,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Character":
        """从 dict 恢复（子类可用 super().from_dict 后追加字段）"""
        return cls(
            name=data["name"],
            location=data["location"],
            role=data.get("role", ""),
            personality=data.get("personality", ""),
            goal=data.get("goal", ""),
            states=data.get("states"),
        )


class NPC(Character):
    """轻量 NPC 实体：由 GM 控制，不自主行动、无记忆。

    与 Agent 的差异：
    - 不进入 action_order（引擎不会为它调用 think/act）
    - 无 memory / relationships / chat_history
    - 通过 GM 的 npc_speak 等工具发声，对 Agent 消息流透明
    """

    pass
