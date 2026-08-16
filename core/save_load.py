"""模拟状态保存与加载"""

import json
import re
from pathlib import Path

from core.action import ActionRegistry
from core.message import MessageBus
from core.agent import Agent
from core.manual_agent import ManualAgent
from core.scene_loader import load_scene

SAVE_VERSION = 2


def _migrate_event_log(events: list) -> list[dict]:
    """v1 字符串事件迁移到 v2 结构化事件。

    v1 格式形如 "[tick 3] 屋外传来马蹄声"；迁移时解析 tick，来源无法
    还原，统一标记为 GM。
    """
    migrated = []
    for item in events:
        if isinstance(item, dict):
            migrated.append(item)
            continue
        match = re.match(r"^\[tick (\d+)\] (.*)$", item, re.DOTALL)
        if match:
            migrated.append({
                "tick": int(match.group(1)),
                "text": match.group(2),
                "source": "GM",
                "source_type": "gm",
            })
        else:
            migrated.append({
                "tick": 0,
                "text": item,
                "source": "GM",
                "source_type": "gm",
            })
    return migrated


def _migrate(data: dict) -> dict:
    """存档版本迁移入口：旧版本在此升级到最新格式，返回迁移后的 dict。"""
    if data.get("version") == 1:
        data = dict(data)
        data["version"] = SAVE_VERSION
        data["event_log"] = _migrate_event_log(data.get("event_log", []))
    return data


def save_simulation_state(world, gm, scene_module: str, scene_display: str, path: str):
    data = {
        "version": SAVE_VERSION,
        "scene": scene_module,
        "scene_display": scene_display,
        "tick": world.tick,
        "locations": world.locations,
        "connections": [[a, b] for a, b in world.connections],
        "action_order": world.action_order,
        "event_log": [e.to_dict() for e in world.event_log],
        "environment": world.environment,
        "message_bus": world.message_bus.to_dict(),
        "gm": gm.to_dict(),
        "agents": {
            name: agent.to_dict()
            for name, agent in world.agents.items()
        },
        "npcs": {
            name: npc.to_dict()
            for name, npc in world.npcs.items()
        },
    }

    path_obj = Path(path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    path_obj.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_simulation_state(path: str, config: dict):
    from core.world import WorldState
    from core.gm import GMAgent

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data = _migrate(data)

    if data.get("version") != SAVE_VERSION:
        raise ValueError(f"不支持的存档版本: {data.get('version')}")

    scene = load_scene(data["scene"])
    display_name = data.get("scene_display", data["scene"])
    print(f"载入存档: {display_name}")

    registry = ActionRegistry()
    scene.setup(registry)

    from core.event import TimelineEvent

    world = WorldState()
    world.tick = data["tick"]
    world.apply_scene_config(scene)
    world.event_log = [TimelineEvent.from_dict(e) for e in data["event_log"]]
    world.action_order = data["action_order"]
    world.connections = [tuple(p) for p in data.get("connections", [])]
    world._adjacency = WorldState.compute_adjacency(world.connections)
    world.environment = data.get("environment", {})

    world.message_bus = MessageBus.from_dict(data["message_bus"])

    agents_by_name = {a["name"]: a for a in scene.agents}

    for name, agent_data in data["agents"].items():
        cfg = agents_by_name[name]
        agent_type = agent_data.get("agent_type", "Agent")
        cls = ManualAgent if agent_type == "ManualAgent" else Agent
        restore_kwargs = {}
        if cls is ManualAgent and agent_data.get("manual_file"):
            restore_kwargs["file_path"] = agent_data["manual_file"]
        agent = cls.from_config(
            scene, cfg, config, registry=registry, saved=agent_data, **restore_kwargs
        )
        world.agents[name] = agent

    # 恢复 NPC（静态 + 运行时动态添加的），并合并进 npc_names
    from core.character import NPC

    for name, npc_data in data.get("npcs", {}).items():
        npc = NPC.from_dict(npc_data)
        world.add_npc(npc)

    # 校正 npc_names：删除的静态 NPC（npc_remove）不能被 scene 基线重新播种，
    # npc_names 必须与实际 npcs 实体完全一致（add_npc/remove_npc 保持该不变量）。
    world.npc_names = set(world.npcs.keys())

    gm_registry = ActionRegistry(include_agent_params=False)
    scene.setup_gm(gm_registry)
    gm = GMAgent.from_dict(scene, config, data["gm"], gm_registry)

    return world, scene, gm, registry
