"""模拟状态序列化与版本迁移。

注意：真正的对象装配（从存档恢复世界）在 app/factory.py::restore_world，
本模块只负责纯数据序列化和旧版本数据迁移，不 import 具体场景/动作。
"""

import json
import re
from pathlib import Path

SAVE_VERSION = 2


def _migrate_event_log(events: list) -> list[dict]:
    """v1 字符串事件迁移到 v2 结构化事件。"""
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


def migrate_save_data(data: dict) -> dict:
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
