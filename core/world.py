"""世界状态管理。"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from core.character import NPC
from core.event import SOURCE_GM, TimelineEvent

if TYPE_CHECKING:
    from .agent import Agent


# 钱包状态键：角色的经济/物品统一放在 states[INVENTORY_KEY] 下（单一固定键）。
# 场景在钱包内部自定义资源名（如 金钱/物品/信用点），core 只认识这一个键，
# 校验支付能力时只暴露行动者自己的钱包（见 build_inventory）。
INVENTORY_KEY = "inventory"


def build_inventory(states: dict) -> dict:
    """抽取角色的钱包视图 states[INVENTORY_KEY]（行动者自己的支付能力，供参数校验期使用）。

    返回副本（内层 dict 也取副本），避免调用方误改真实状态。
    """
    wallet = states.get(INVENTORY_KEY)
    if not isinstance(wallet, dict):
        return {}
    return {
        key: (dict(value) if isinstance(value, dict) else value)
        for key, value in wallet.items()
    }


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
    npcs: dict[str, NPC] = field(default_factory=dict)
    event_log: list[TimelineEvent] = field(default_factory=list)
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
            return [loc2 for loc2 in self.locations if loc2 != loc]
        return list(self._adjacency.get(loc, set()))

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
        # 静态 NPC 名作为基线；运行时动态添加的 NPC 名（add_npc）需要保留，
        # 因此这里只追加不覆盖。
        self.npc_names = set(scene.npc_names or [])

    @property
    def characters(self) -> dict[str, Any]:
        """统一视图：所有角色（自主 Agent + GM 控制的 NPC）。

        返回只读视图；增删角色请分别操作 self.agents / self.npcs。
        """
        merged = dict(self.agents)
        merged.update(self.npcs)
        return merged

    def _resolve_location(self, name: str) -> str:
        """解析任意角色（Agent 或 NPC）的位置。不存在时抛 KeyError。"""
        agent = self.agents.get(name)
        if agent is not None:
            return agent.location
        npc = self.npcs.get(name)
        if npc is not None:
            return npc.location
        raise KeyError(name)

    def add_npc(self, npc: NPC) -> str | None:
        """添加一个 NPC（动态或初始化）。返回错误信息或 None。

        自动：登记到 npcs 与 npc_names、增量更新位置索引。
        """
        if npc.name in self.agents or npc.name in self.npcs:
            return f"角色 '{npc.name}' 已存在"
        if npc.location not in self.locations:
            return f"'{npc.location}' 不是有效位置，可选: {', '.join(self.locations)}"
        self.npcs[npc.name] = npc
        self.npc_names.add(npc.name)
        if self._agents_by_location:
            self._agents_by_location.setdefault(npc.location, []).append(npc.name)
        return None

    def remove_npc(self, name: str) -> str | None:
        """移除一个 NPC（add_npc 的镜像）。返回错误信息或 None。

        从 npcs、npc_names 与位置索引中同步删除——所有读取点
        （characters/可见性/校验上下文/存档/TUI）都会自动停止看到它。
        """
        npc = self.npcs.get(name)
        if npc is None:
            return f"'{name}' 不是 NPC"
        old_location = npc.location
        del self.npcs[name]
        self.npc_names.discard(name)
        if self._agents_by_location:
            old_bucket = self._agents_by_location.get(old_location)
            if old_bucket and name in old_bucket:
                old_bucket.remove(name)
        return None

    def add_event(
        self,
        text: str,
        source: str = "GM",
        source_type: str = SOURCE_GM,
        meta: dict | None = None,
    ) -> None:
        """记录结构化时间线事件。

        默认来源是 GM；NPC / Agent 动作可通过 source / source_type 标注。
        事件流上限与归档仍由 Phase 3 处理。
        """
        self.event_log.append(TimelineEvent(
            tick=self.tick,
            text=text,
            source=source,
            source_type=source_type,
            meta=meta,
        ))

    def event_log_texts(self) -> list[str]:
        """返回事件文本列表（测试/调试用）。"""
        return [e.text for e in self.event_log]

    def event_log_for_tick(self, tick: int) -> list[TimelineEvent]:
        """返回指定 tick 的事件列表。"""
        return [e for e in self.event_log if e.tick == tick]

    def event_log_for_last_ticks(self, n_ticks: int) -> list[TimelineEvent]:
        """返回最近 n_ticks 个 tick 内的事件（含当前 tick）。"""
        if n_ticks <= 0:
            return []
        min_tick = self.tick - n_ticks + 1
        return [e for e in self.event_log if min_tick <= e.tick <= self.tick]

    def get_characters_in_location(self, location: str) -> list[str]:
        """获取某个位置的所有角色（Agent + NPC，返回副本，调用方可安全修改）"""
        if not self._agents_by_location and (self.agents or self.npcs):
            self.rebuild_location_index()
        return list(self._agents_by_location.get(location, ()))

    def get_agents_in_location(self, location: str) -> list[str]:
        """获取某个位置的所有 Agent（不含 NPC，返回副本）。"""
        return [n for n in self.get_characters_in_location(location) if n not in self.npcs]

    def rebuild_location_index(self) -> None:
        """按当前角色的位置重建索引（引擎启动/存档加载后调用）。"""
        index: dict[str, list[str]] = {}
        for name, agent in self.agents.items():
            index.setdefault(agent.location, []).append(name)
        for name, npc in self.npcs.items():
            index.setdefault(npc.location, []).append(name)
        self._agents_by_location = index

    def move_character(self, name: str, new_location: str) -> str | None:
        """移动任意角色（Agent 或 NPC）并增量维护位置索引。返回错误信息或 None。

        只校验名字与位置合法性，不校验可达性——「Move 受可达性限制」是
        MoveAction 的规则（validate_params），世界层保持无策略，
        传送门/传送魔法/GM 强制移动等能力可直接调用本方法。
        """
        if name not in self.characters:
            return f"'{name}' 不存在"
        if new_location not in self.locations:
            return f"'{new_location}' 不是有效位置"
        if not self._agents_by_location:
            self.rebuild_location_index()
        char = self.characters[name]
        old_location = char.location
        if old_location != new_location:
            old_bucket = self._agents_by_location.get(old_location)
            if old_bucket and name in old_bucket:
                old_bucket.remove(name)
            self._agents_by_location.setdefault(new_location, []).append(name)
            char.location = new_location
        return None

    def get_hearable_agents(self, target: str, *, exclude: str | None = None, use_location: bool = False) -> list[str]:
        """获取能听到某个位置事件的所有角色（Agent + NPC，同位置 + 可见位置）

        use_location=False (默认): target 是角色名（Agent 或 NPC），自动定位其位置
        use_location=True:       target 是位置名
        """
        base_loc = target if use_location else self._resolve_location(target)
        hearable_locs = [base_loc] + self._reverse_visibility.get(base_loc, [])
        result = []
        for loc in hearable_locs:
            for name in self.get_characters_in_location(loc):
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
                "npc_locations": {n: self.npcs[n].location for n in self.npcs},
                "interactable_keys": self.interactable_keys,
            }
        agent_location = self.agents[agent_name].location
        agent_names = list(self.agents.keys()) + [n for n in self.npcs if n != agent_name]
        agents_by_location = {loc: self.get_characters_in_location(loc) for loc in self.locations}
        return {
            "agent_name": agent_name,
            "agent_location": agent_location,
            "agent_names": agent_names,
            "locations": self.locations,
            "agents_by_location": agents_by_location,
            "hearable_agents": self.get_hearable_agents(agent_name),
            "adjacent_locations": self.get_adjacent_locations(agent_location),
            "interactable_keys": self.interactable_keys,
            "content_max_length": self.agents[agent_name].content_max_length,
            # 行动者自己的经济状态视图（trade 等动作校验支付能力用，不含他人信息）
            # getattr 兜底：测试里的 stub agent 可能没有 states
            "inventory": build_inventory(getattr(self.agents[agent_name], "states", {})),
        }
