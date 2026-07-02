"""世界状态管理。"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .agent import Agent
    from .message import Message, MessageBus


@dataclass
class WorldState:
    """世界状态"""

    tick: int = 0
    locations: list[str] = field(default_factory=list)
    visibility: dict[str, list[str]] = field(default_factory=dict)
    reverse_visibility: dict[str, list[str]] = field(default_factory=dict)
    agents: dict[str, "Agent"] = field(default_factory=dict)
    event_log: list[str] = field(default_factory=list)
    action_order: list[str] = field(default_factory=list)
    message_bus: Any = None

    @staticmethod
    def compute_reverse_visibility(visibility: dict[str, list[str]]) -> dict[str, list[str]]:
        """从正向可见性计算反向可见性：哪些位置能看到给定位置"""
        reverse = {}
        for loc, visible in visibility.items():
            for vloc in visible:
                reverse.setdefault(vloc, []).append(loc)
        return reverse

    def advance_tick(self):
        """推进一个 tick"""
        self.tick += 1

    def add_event(self, event: str):
        """记录事件"""
        self.event_log.append(f"[tick {self.tick}] {event}")

    def get_agents_in_location(self, location: str) -> list[str]:
        """获取某个位置的所有 Agent"""
        return [name for name, agent in self.agents.items() if agent.location == location]

    def get_hearable_agents(self, agent_name: str, exclude: str | None = None) -> list[str]:
        """获取能听到该 agent 说话的所有其他 agent（同位置 + 能看到该位置的人）"""
        agent = self.agents[agent_name]
        hearable_locs = [agent.location] + self.reverse_visibility.get(agent.location, [])
        result = []
        for loc in hearable_locs:
            for name in self.get_agents_in_location(loc):
                if name != agent_name and name != exclude:
                    result.append(name)
        return result

    def rotate_order(self):
        """轮换行动顺序"""
        if len(self.action_order) > 1:
            self.action_order = self.action_order[1:] + [self.action_order[0]]