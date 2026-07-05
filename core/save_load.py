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
        "content_max_length": agent.content_max_length,
        "agent_type": "ManualAgent" if isinstance(agent, ManualAgent) else "Agent",
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

    world = WorldState()
    world.tick = data["tick"]
    world.locations = data["locations"]
    world.connections = [tuple(p) for p in data.get("connections", [])]
    world._adjacency = WorldState.compute_adjacency(world.connections)
    world.event_log = data["event_log"]
    world.action_order = data["action_order"]
    world.visibility = scene.visibility or {}
    world.reverse_visibility = WorldState.compute_reverse_visibility(world.visibility)
    world.environment = data.get("environment", {})
    world.interactable_keys = scene.interactable_keys or {}

    world.message_bus = MessageBus.from_dict(data["message_bus"])

    for name, agent_data in data["agents"].items():
        memory = AgentMemory.from_dict(
            agent_data["memory"],
            name=name,
            short_limit=config["agent"]["memory_short_limit"],
            compress_threshold=config["agent"]["memory_compress_threshold"],
            relation_limit=config["agent"].get("relation_display_limit", 3),
        )

        agent_kwargs = dict(
            name=name,
            role=agent_data["role"],
            personality=agent_data["personality"],
            goal=agent_data["goal"],
            location=agent_data["location"],
            relationships=agent_data["relationships"],
            memory=memory,
            content_max_length=agent_data.get("content_max_length", 200),
            inbox_limit=config["agent"].get("inbox_limit", 5),
            world_description=scene.world_description,
            states=agent_data.get("states"),
            writable_states=set(agent_data.get("writable_states", [])),
        )

        agent_type = agent_data.get("agent_type", "Agent")
        if agent_type == "ManualAgent":
            agent = ManualAgent(**agent_kwargs)
        else:
            agent = Agent(**agent_kwargs)

        agent.states = agent_data.get("states", {})

        world.agents[name] = agent

    gm_data = data["gm"]
    gm = GMAgent(
        events=[(t, e) for t, e in gm_data["scheduled_events"]],
        random_events=gm_data["random_events"],
        chance=config["gm"]["random_event_chance"],
        use_llm=gm_data.get("use_llm", config["gm"]["use_llm"]),
        llm_chance=config["gm"].get("llm_event_chance", 0.3),
        llm_prompt=scene.get_gm_config().get("llm_prompt", ""),
        world_description=scene.world_description,
        message_limit=config["gm"].get("message_limit", 15),
    )

    return world, scene, gm
