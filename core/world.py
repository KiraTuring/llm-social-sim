"""世界状态管理与地理信息。"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from core.character import NPC
from core.event import SOURCE_GM, TimelineEvent

if TYPE_CHECKING:
    from .agent import Agent


INVENTORY_KEY = "inventory"
ENV_DELETE_SENTINEL = "delete"


def build_inventory(states: dict) -> dict:
    """抽取角色的钱包视图 states[INVENTORY_KEY]。"""
    wallet = states.get(INVENTORY_KEY)
    if not isinstance(wallet, dict):
        return {}
    return {
        key: (dict(value) if isinstance(value, dict) else value)
        for key, value in wallet.items()
    }


class LocationGraph:
    """地点图与环境状态：与 WorldState 强相关，因此定义在 world.py 内。"""

    def __init__(
        self,
        locations: list[str] | None = None,
        connections: list[tuple[str, str]] | None = None,
        interactable_keys: dict[str, list[str]] | None = None,
        environment: dict[str, dict[str, str]] | None = None,
    ):
        self.locations = list(locations or [])
        self.connections = list(connections or [])
        self.interactable_keys = dict(interactable_keys or {})
        self.environment = {k: dict(v) for k, v in (environment or {}).items()}

        self._adjacency: dict[str, set[str]] = {}
        self._visibility: dict[str, list[str]] = {}
        self._reverse_visibility: dict[str, list[str]] = {}
        self._protected_env_keys: dict[str, set[str]] = {}

    @staticmethod
    def compute_adjacency(connections: list[tuple[str, str]]) -> dict[str, set[str]]:
        adj: dict[str, set[str]] = {}
        for a, b in connections:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
        return adj

    @staticmethod
    def compute_reverse_visibility(visibility: dict[str, list[str]]) -> dict[str, list[str]]:
        reverse: dict[str, list[str]] = {}
        for loc, visible in visibility.items():
            for vloc in visible:
                reverse.setdefault(vloc, []).append(loc)
        return reverse

    @staticmethod
    def compute_protected_env_keys(initial_environment: dict[str, dict[str, str]]) -> dict[str, set[str]]:
        return {loc: set(keys.keys()) for loc, keys in initial_environment.items()}

    def rebuild_adjacency(self) -> None:
        self._adjacency = self.compute_adjacency(self.connections)

    def set_visibility(self, visibility: dict[str, list[str]]) -> None:
        self._visibility = dict(visibility) if visibility else {}
        self._reverse_visibility = self.compute_reverse_visibility(self._visibility)

    def apply_scene_config(self, scene) -> None:
        self.locations = copy.deepcopy(scene.locations)
        self.connections = copy.deepcopy(scene.connections)
        self.interactable_keys = copy.deepcopy(scene.interactable_keys)
        self.rebuild_adjacency()
        self.set_visibility(scene.visibility or {})
        self.environment = {k: dict(v) for k, v in scene.initial_environment.items()}
        self._protected_env_keys = self.compute_protected_env_keys(scene.initial_environment)

    def get_adjacent_locations(self, loc: str) -> list[str]:
        if not self.connections:
            return [loc2 for loc2 in self.locations if loc2 != loc]
        return list(self._adjacency.get(loc, set()))

    def get_visible_locations(self, loc: str) -> list[str]:
        return [loc] + self._visibility.get(loc, [])

    def hearable_locations(self, loc: str) -> list[str]:
        return [loc] + self._reverse_visibility.get(loc, [])

    def update_environment(self, location: str, key: str, value: str) -> str | None:
        if location not in self.locations:
            return f"'{location}' 不是有效位置"
        self.environment.setdefault(location, {})[key] = value
        return None

    def modify_environment(self, location: str, key: str, value: str) -> tuple[bool, str]:
        if location not in self.locations:
            return False, f"'{location}' 不是有效位置"
        if value != ENV_DELETE_SENTINEL:
            self.environment.setdefault(location, {})[key] = value
            return True, f"环境变更: {location}.{key} → {value}"

        if key not in self.environment.get(location, {}):
            return False, f"'{location}' 中不存在指标 '{key}'"
        if key in self._protected_env_keys.get(location, set()):
            return False, f"'{key}' 是预定义指标，不可删除"
        del self.environment[location][key]
        return True, f"环境指标已删除: {location}.{key}"

    def get_environment_summary(self, location: str) -> str:
        env = self.environment.get(location, {})
        if not env:
            return ""
        return ", ".join(f"{k} {v}" for k, v in env.items())


@dataclass
class WorldState:
    """世界状态。"""

    tick: int = 0
    agents: dict[str, "Agent"] = field(default_factory=dict)
    npcs: dict[str, NPC] = field(default_factory=dict)
    event_log: list[TimelineEvent] = field(default_factory=list)
    action_order: list[str] = field(default_factory=list)
    message_bus: Any = None
    npc_names: set[str] = field(default_factory=set)
    _agents_by_location: dict[str, list[str]] = field(default_factory=dict)
    geography: LocationGraph = field(default_factory=LocationGraph)

    # ---- 地理字段薄委托，兼容既有 world.locations / world.environment 写法 ---- #
    @property
    def locations(self) -> list[str]:
        return self.geography.locations

    @locations.setter
    def locations(self, value) -> None:
        self.geography.locations = value

    @property
    def connections(self) -> list[tuple[str, str]]:
        return self.geography.connections

    @connections.setter
    def connections(self, value) -> None:
        self.geography.connections = value

    @property
    def interactable_keys(self) -> dict[str, list[str]]:
        return self.geography.interactable_keys

    @interactable_keys.setter
    def interactable_keys(self, value) -> None:
        self.geography.interactable_keys = value

    @property
    def environment(self) -> dict[str, dict[str, str]]:
        return self.geography.environment

    @environment.setter
    def environment(self, value) -> None:
        self.geography.environment = value

    def apply_scene_config(self, scene) -> None:
        """从 scene 复制场景级配置（init 和 load 共用）。"""
        self.geography.apply_scene_config(scene)
        self.npc_names = set(scene.npc_names or [])

    def get_adjacent_locations(self, loc: str) -> list[str]:
        return self.geography.get_adjacent_locations(loc)

    def update_environment(self, location: str, key: str, value: str) -> str | None:
        return self.geography.update_environment(location, key, value)

    def modify_environment(self, location: str, key: str, value: str) -> tuple[bool, str]:
        return self.geography.modify_environment(location, key, value)

    def get_environment_summary(self, location: str) -> str:
        return self.geography.get_environment_summary(location)

    def get_visible_locations(self, location: str) -> list[str]:
        return self.geography.get_visible_locations(location)

    def set_visibility(self, visibility: dict[str, list[str]]) -> None:
        self.geography.set_visibility(visibility)

    @property
    def characters(self) -> dict[str, Any]:
        merged = dict(self.agents)
        merged.update(self.npcs)
        return merged

    def _resolve_location(self, name: str) -> str:
        agent = self.agents.get(name)
        if agent is not None:
            return agent.location
        npc = self.npcs.get(name)
        if npc is not None:
            return npc.location
        raise KeyError(name)

    def add_npc(self, npc: NPC) -> str | None:
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
        self.event_log.append(TimelineEvent(
            tick=self.tick,
            text=text,
            source=source,
            source_type=source_type,
            meta=meta,
        ))

    def event_log_texts(self) -> list[str]:
        return [e.text for e in self.event_log]

    def event_log_for_tick(self, tick: int) -> list[TimelineEvent]:
        return [e for e in self.event_log if e.tick == tick]

    def event_log_for_last_ticks(self, n_ticks: int) -> list[TimelineEvent]:
        if n_ticks <= 0:
            return []
        min_tick = self.tick - n_ticks + 1
        return [e for e in self.event_log if min_tick <= e.tick <= self.tick]

    def get_characters_in_location(self, location: str) -> list[str]:
        if not self._agents_by_location and (self.agents or self.npcs):
            self.rebuild_location_index()
        return list(self._agents_by_location.get(location, ()))

    def get_agents_in_location(self, location: str) -> list[str]:
        return [n for n in self.get_characters_in_location(location) if n not in self.npcs]

    def rebuild_location_index(self) -> None:
        index: dict[str, list[str]] = {}
        for name, agent in self.agents.items():
            index.setdefault(agent.location, []).append(name)
        for name, npc in self.npcs.items():
            index.setdefault(npc.location, []).append(name)
        self._agents_by_location = index

    def move_character(self, name: str, new_location: str) -> str | None:
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
        base_loc = target if use_location else self._resolve_location(target)
        hearable_locs = self.geography.hearable_locations(base_loc)
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
        if len(self.action_order) > 1:
            self.action_order = self.action_order[1:] + [self.action_order[0]]

    def build_validation_context(self, agent_name: str) -> dict:
        base = {
            "agent_name": agent_name,
            "locations": self.locations,
            "interactable_keys": self.interactable_keys,
        }
        if agent_name not in self.agents:
            return {
                **base,
                "agent_names": list(self.agents.keys()),
                "npc_names": list(self.npc_names),
                "npc_locations": {n: self.npcs[n].location for n in self.npcs},
            }

        agent = self.agents[agent_name]
        agent_location = agent.location
        agent_names = list(self.agents.keys()) + [n for n in self.npcs if n != agent_name]
        return {
            **base,
            "agent_location": agent_location,
            "agent_names": agent_names,
            "agents_by_location": {loc: self.get_characters_in_location(loc) for loc in self.locations},
            "hearable_agents": self.get_hearable_agents(agent_name),
            "adjacent_locations": self.get_adjacent_locations(agent_location),
            "content_max_length": agent.content_max_length,
            "inventory": build_inventory(getattr(agent, "states", {})),
        }
