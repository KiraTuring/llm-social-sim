"""模拟状态保存与加载"""

import json
from pathlib import Path

from core.message import MessageBus
from core.agent import Agent
from core.manual_agent import ManualAgent
from scenarios.utils import load_scene

SAVE_VERSION = 1


def _migrate(data: dict) -> dict:
    """存档版本迁移入口：旧版本在此升级到最新格式，返回迁移后的 dict。

    目前只有 version 1，直接原样返回；未来格式演进只改这一处。
    """
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
        "event_log": world.event_log,
        "environment": world.environment,
        "message_bus": world.message_bus.to_dict(),
        "gm": gm.to_dict(),
        "agents": {
            name: agent.to_dict()
            for name, agent in world.agents.items()
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

    world = WorldState()
    world.tick = data["tick"]
    world.apply_scene_config(scene)
    world.event_log = data["event_log"]
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
        agent = cls.from_config(scene, cfg, config, saved=agent_data, **restore_kwargs)
        world.agents[name] = agent

    gm = GMAgent.from_dict(scene, config, data["gm"])

    return world, scene, gm
