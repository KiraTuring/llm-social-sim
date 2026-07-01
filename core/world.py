"""世界状态管理。"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent import Agent
    from .message import Message


@dataclass
class WorldState:
    """世界状态"""

    tick: int = 0
    locations: list[str] = field(default_factory=list)
    visibility: dict[str, list[str]] = field(default_factory=dict)
    agents: dict[str, "Agent"] = field(default_factory=dict)
    event_log: list[str] = field(default_factory=list)
    action_order: list[str] = field(default_factory=list)

    def advance_tick(self):
        """推进一个 tick"""
        self.tick += 1

    def add_event(self, event: str):
        """记录事件"""
        self.event_log.append(f"[tick {self.tick}] {event}")

    def get_agents_in_location(self, location: str) -> list[str]:
        """获取某个位置的所有 Agent"""
        return [name for name, agent in self.agents.items() if agent.location == location]

    def rotate_order(self):
        """轮换行动顺序"""
        if len(self.action_order) > 1:
            self.action_order = self.action_order[1:] + [self.action_order[0]]