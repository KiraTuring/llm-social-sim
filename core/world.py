"""世界状态管理。"""

import copy
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
    _protected_env_keys: dict[str, set[str]] = field(default_factory=dict)
    npc_names: set[str] = field(default_factory=set)
    _agents_by_location: dict[str, list[str]] = field(default_factory=dict)

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

    @staticmethod
    def compute_protected_env_keys(initial_environment: dict[str, dict[str, str]]) -> dict[str, set[str]]:
        """从初始环境配置计算受保护的指标 key 集合"""
        return {loc: set(keys.keys()) for loc, keys in initial_environment.items()}

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

    def modify_environment(self, location: str, key: str, value: str) -> tuple[bool, str]:
        """GM 专用单一入口：value='delete' 时删除 key，否则更新。
        返回 (success, message)，message 可直接用作 summary。"""
        if location not in self.locations:
            return False, f"'{location}' 不是有效位置"
        if value != "delete":
            self.environment.setdefault(location, {})[key] = value
            return True, f"环境变更: {location}.{key} → {value}"
        # value == "delete"
        if key not in self.environment.get(location, {}):
            return False, f"'{location}' 中不存在指标 '{key}'"
        if key in self._protected_env_keys.get(location, set()):
            return False, f"'{key}' 是预定义指标，不可删除"
        del self.environment[location][key]
        return True, f"环境指标已删除: {location}.{key}"

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

    _SCENE_DIRECT_FIELDS = ("locations", "connections", "interactable_keys")

    def apply_scene_config(self, scene) -> None:
        """从 scene 复制场景级配置（init 和 load 共用）"""
        for attr in self._SCENE_DIRECT_FIELDS:
            setattr(self, attr, copy.deepcopy(getattr(scene, attr)))
        self._adjacency = self.compute_adjacency(self.connections)
        self.set_visibility(scene.visibility or {})
        self.environment = {k: dict(v) for k, v in scene.initial_environment.items()}
        self._protected_env_keys = self.compute_protected_env_keys(scene.initial_environment)
        self.npc_names = set(scene.npc_names or [])

    def add_event(self, event: str):
        """记录事件。

        TODO(Phase 3): 有界事件存储，裁剪时归档到持久化文件。
        """
        self.event_log.append(f"[tick {self.tick}] {event}")

    def get_agents_in_location(self, location: str) -> list[str]:
        """获取某个位置的所有 Agent（返回副本，调用方可安全修改）"""
        if not self._agents_by_location and self.agents:
            self.rebuild_location_index()
        return list(self._agents_by_location.get(location, ()))

    def rebuild_location_index(self) -> None:
        """按当前 agents 的位置重建索引（引擎启动/存档加载后调用）。"""
        index: dict[str, list[str]] = {}
        for name, agent in self.agents.items():
            index.setdefault(agent.location, []).append(name)
        self._agents_by_location = index

    def move_agent(self, agent_name: str, new_location: str) -> str | None:
        """移动 Agent 并增量维护位置索引。返回错误信息或 None。"""
        if agent_name not in self.agents:
            return f"'{agent_name}' 不存在"
        if new_location not in self.locations:
            return f"'{new_location}' 不是有效位置"
        if not self._agents_by_location:
            self.rebuild_location_index()

        agent = self.agents[agent_name]
        old_location = agent.location
        if old_location != new_location:
            old_bucket = self._agents_by_location.get(old_location)
            if old_bucket and agent_name in old_bucket:
                old_bucket.remove(agent_name)
            self._agents_by_location.setdefault(new_location, []).append(agent_name)
            agent.location = new_location
        return None

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

    def build_validation_context(self, agent_name: str) -> dict:
        """为指定 Agent 或 GM 构建 LLM 参数校验上下文"""
        if agent_name not in self.agents:
            return {
                "agent_name": agent_name,
                "agent_names": list(self.agents.keys()),
                "locations": self.locations,
                "npc_names": list(self.npc_names),
                "interactable_keys": self.interactable_keys,
            }
        agent_location = self.agents[agent_name].location
        agents_by_location = {loc: self.get_agents_in_location(loc) for loc in self.locations}
        return {
            "agent_name": agent_name,
            "agent_location": agent_location,
            "agent_names": list(self.agents.keys()),
            "locations": self.locations,
            "agents_by_location": agents_by_location,
            "hearable_agents": self.get_hearable_agents(agent_name),
            "adjacent_locations": self.get_adjacent_locations(agent_location),
            "interactable_keys": self.interactable_keys,
            "content_max_length": self.agents[agent_name].content_max_length,
        }
