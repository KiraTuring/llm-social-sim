"""模拟状态保存与加载"""

import json
from pathlib import Path

from core.message import MessageBus
from core.agent import Agent
from core.manual_agent import ManualAgent
from memory.memory import AgentMemory
from scenarios.utils import load_scene


def serialize_memory(memory: AgentMemory) -> dict:
    return memory.to_dict()


def serialize_agent(agent: Agent) -> dict:
    return {
        "role": agent.role,
        "personality": agent.personality,
        "goal": agent.goal,
        "location": agent.location,
        "relationships": agent.relationships,
        "states": agent.states,
        "writable_states": list(agent._writable_states) if agent._writable_states else [],
        "private_states": list(agent._private_states) if agent._private_states else [],
        "content_max_length": agent.content_max_length,
        "agent_type": "ManualAgent" if isinstance(agent, ManualAgent) else "Agent",
        "last_observed_result": getattr(agent, "_last_observed_result", ""),
        "prompt_format": getattr(agent, "prompt_format", "text"),
        "chat_history": getattr(agent, "_chat_history", []),
        "memory": serialize_memory(agent.memory),
    }


def save_simulation_state(world, gm, scene_module: str, scene_display: str, path: str):
    data = {
        "version": 1,
        "scene": scene_module,
        "scene_display": scene_display,
        "tick": world.tick,
        "locations": world.locations,
        "connections": [[a, b] for a, b in world.connections],
        "action_order": world.action_order,
        "event_log": world.event_log,
        "environment": world.environment,
        "message_bus": world.message_bus.to_dict(),
        "gm": {
            "scheduled_events": [[t, e] for t, e in gm.scheduled_events],
            "random_events": gm.random_events,
            "use_llm": gm.use_llm,
            "history": getattr(gm, "_gm_history", []),
        },
        "agents": {
            name: serialize_agent(agent)
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

    if data.get("version") != 1:
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
        agent = cls.from_config(scene, cfg, config, saved=agent_data)
        world.agents[name] = agent

    gm_data = data["gm"]
    gm = GMAgent.from_config(scene, config)
    gm.scheduled_events = [(t, e) for t, e in gm_data["scheduled_events"]]
    gm.random_events = gm_data["random_events"]
    gm.use_llm = gm_data.get("use_llm", config["gm"]["use_llm"])
    gm._gm_history = gm_data.get("history", [])

    return world, scene, gm
