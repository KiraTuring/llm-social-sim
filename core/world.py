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
    connections: list[tuple[str, str]] = field(default_factory=list)
    _adjacency: dict[str, set[str]] = field(default_factory=dict)
    _visibility: dict[str, list[str]] = field(default_factory=dict)
    _reverse_visibility: dict[str, list[str]] = field(default_factory=dict)
    agents: dict[str, "Agent"] = field(default_factory=dict)
    event_log: list[str] = field(default_factory=list)
    action_order: list[str] = field(default_factory=list)
    message_bus: Any = None
    environment: dict[str, dict[str, str]] = field(default_factory=dict)
    interactable_keys: dict[str, list[str]] = field(default_factory=dict)

    @staticmethod
    def compute_adjacency(connections: list[tuple[str, str]]) -> dict[str, set[str]]:
        """从边列表计算双向邻接表"""
        adj = {}
        for a, b in connections:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
        return adj

    @staticmethod
    def compute_reverse_visibility(visibility: dict[str, list[str]]) -> dict[str, list[str]]:
        """从正向可见性计算反向可见性：哪些位置能看到给定位置"""
        reverse = {}
        for loc, visible in visibility.items():
            for vloc in visible:
                reverse.setdefault(vloc, []).append(loc)
        return reverse

    def get_adjacent_locations(self, loc: str) -> list[str]:
        """获取从某个位置可达的相邻位置"""
        if not self.connections:
            return [l for l in self.locations if l != loc]
        return list(self._adjacency.get(loc, set()))

    def advance_tick(self):
        """推进一个 tick"""
        self.tick += 1

    def update_environment(self, location: str, key: str, value: str) -> str | None:
        """更新环境状态，如果 location 不合法返回错误信息"""
        if location not in self.locations:
            return f"'{location}' 不是有效位置"
        self.environment.setdefault(location, {})[key] = value
        return None

    def get_environment_summary(self, location: str) -> str:
        """获取某个位置的格式化环境摘要"""
        env = self.environment.get(location, {})
        if not env:
            return ""
        return ", ".join(f"{k} {v}" for k, v in env.items())

    def get_visible_locations(self, location: str) -> list[str]:
        """获取从某个位置能观察到哪些位置（含自身）。可扩展支持动态可见性变化"""
        return [location] + self._visibility.get(location, [])

    def set_visibility(self, visibility: dict[str, list[str]]) -> None:
        """设置可见性并自动计算逆可见性"""
        self._visibility = dict(visibility) if visibility else {}
        self._reverse_visibility = self.compute_reverse_visibility(self._visibility)

    def add_event(self, event: str):
        """记录事件"""
        self.event_log.append(f"[tick {self.tick}] {event}")

    def get_agents_in_location(self, location: str) -> list[str]:
        """获取某个位置的所有 Agent"""
        return [name for name, agent in self.agents.items() if agent.location == location]

    def get_hearable_agents(self, target: str, *, exclude: str | None = None, use_location: bool = False) -> list[str]:
        """获取能听到某个位置事件的所有 agent（同位置 + 可见位置）
        
        use_location=False (默认): target 是 agent 名，自动定位其位置
        use_location=True:       target 是位置名
        """
        base_loc = target if use_location else self.agents[target].location
        hearable_locs = [base_loc] + self._reverse_visibility.get(base_loc, [])
        result = []
        for loc in hearable_locs:
            for name in self.get_agents_in_location(loc):
                if not use_location and name == target:
                    continue
                if name == exclude:
                    continue
                result.append(name)
        return result

    def rotate_order(self):
        """轮换行动顺序"""
        if len(self.action_order) > 1:
            self.action_order = self.action_order[1:] + [self.action_order[0]]