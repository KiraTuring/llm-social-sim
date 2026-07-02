"""模拟状态保存与加载"""

import json
from pathlib import Path

from core.message import Message, MessageBus
from core.agent import Agent
from core.manual_agent import ManualAgent
from memory.memory import AgentMemory
from scenarios.utils import load_scene


def serialize_message(msg: Message) -> dict:
    return {
        "sender": msg.sender,
        "recipients": msg.recipients,
        "content": msg.content,
        "msg_type": msg.msg_type,
        "tick": msg.tick,
        "target": msg.target,
    }


def deserialize_message(data: dict) -> Message:
    return Message(
        sender=data["sender"],
        recipients=data["recipients"],
        content=data["content"],
        msg_type=data["msg_type"],
        tick=data["tick"],
        target=data.get("target"),
    )


def serialize_message_bus(bus: MessageBus) -> dict:
    return {
        "known_agents": list(bus._known_agents),
        "messages": [serialize_message(m) for m in bus._messages],
        "inboxes": {
            name: [serialize_message(m) for m in msgs]
            for name, msgs in bus._inboxes.items()
        },
    }


def deserialize_message_bus(data: dict, bus: MessageBus):
    bus._known_agents = set(data["known_agents"])
    bus._messages = [deserialize_message(m) for m in data["messages"]]
    bus._inboxes = {
        name: [deserialize_message(m) for m in msgs]
        for name, msgs in data["inboxes"].items()
    }


def serialize_memory(memory: AgentMemory) -> dict:
    return {
        "short_term": memory._short_term,
        "summary": memory._summary,
        "relations": memory._relations,
    }


def serialize_agent(agent: Agent) -> dict:
    return {
        "role": agent.role,
        "personality": agent.personality,
        "goal": agent.goal,
        "location": agent.location,
        "relationships": agent.relationships,
        "mood": agent.mood,
        "energy": agent.energy,
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
        "action_order": world.action_order,
        "event_log": world.event_log,
        "message_bus": serialize_message_bus(world.message_bus),
        "gm": {
            "scheduled_events": [[t, e] for t, e in gm.scheduled_events],
            "random_events": gm.random_events,
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
    world.event_log = data["event_log"]
    world.action_order = data["action_order"]
    world.visibility = scene.visibility or {}

    world.message_bus = MessageBus()
    deserialize_message_bus(data["message_bus"], world.message_bus)

    for name, agent_data in data["agents"].items():
        memory = AgentMemory(
            name=name,
            short_limit=config["agent"]["memory_short_limit"],
            compress_threshold=config["agent"]["memory_compress_threshold"],
            relation_limit=config["agent"].get("relation_display_limit", 3),
        )
        memory._short_term = agent_data["memory"]["short_term"]
        memory._summary = agent_data["memory"]["summary"]
        memory._relations = agent_data["memory"]["relations"]

        agent_kwargs = dict(
            name=name,
            role=agent_data["role"],
            personality=agent_data["personality"],
            goal=agent_data["goal"],
            location=agent_data["location"],
            relationships=agent_data["relationships"],
            memory=memory,
            content_max_length=agent_data.get("content_max_length", 200),
            max_energy=config["agent"].get("max_energy", 100),
            inbox_limit=config["agent"].get("inbox_limit", 5),
        )

        agent_type = agent_data.get("agent_type", "Agent")
        if agent_type == "ManualAgent":
            agent = ManualAgent(**agent_kwargs)
        else:
            agent = Agent(**agent_kwargs)

        agent.mood = agent_data["mood"]
        agent.energy = agent_data["energy"]

        world.agents[name] = agent

    gm_data = data["gm"]
    gm = GMAgent(
        events=[(t, e) for t, e in gm_data["scheduled_events"]],
        random_events=gm_data["random_events"],
        chance=config["gm"]["random_event_chance"],
    )

    return world, scene, gm
