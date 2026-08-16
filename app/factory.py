"""应用装配层：创建世界、Agent、GM 与模拟服务。

这是唯一允许同时 import core / memory / llm / scenarios / actions 具体实现的层。
"""

from __future__ import annotations

import json
from pathlib import Path

from core.action import ActionRegistry
from core.agent import Agent
from core.character import NPC
from core.event import TimelineEvent
from core.gm import GMAgent
from core.logger import SimLogger
from core.manual_agent import ManualAgent
from core.message import MessageBus
from core.rules import RuleEngine
from core.save_load import SAVE_VERSION, migrate_save_data
from core.scene import validate_agent_configs
from core.world import WorldState
from llm.client import LLMClient
from memory.memory import AgentMemory


def create_agent(scene, cfg, config, *, registry, saved=None, manual_file=None):
    """创建 Agent 或 ManualAgent。

    saved=None 时按 scene+cfg 新建；saved 提供存档运行时状态。
    """
    agent_type = saved.get("agent_type", "Agent") if saved else ("ManualAgent" if manual_file else "Agent")
    cls = ManualAgent if agent_type == "ManualAgent" else Agent
    prompt_format = config["agent"].get("prompt_format", "text")

    base = dict(
        name=cfg["name"],
        role=cfg["role"],
        personality=cfg["personality"],
        goal=cfg["goal"],
        registry=registry,
        world_description=scene.world_description,
        instruction=scene.instruction,
        prompt_format=prompt_format,
    )

    if saved is None:
        runtime = dict(
            location=cfg["location"],
            relationships=cfg["relationships"],
            memory=AgentMemory(
                name=cfg["name"],
                short_limit=config["agent"]["memory_short_limit"],
                compress_threshold=config["agent"]["memory_compress_threshold"],
            ),
            content_max_length=config["agent"].get("content_max_length", 200),
            states=({**dict(scene.states or {}), **dict(cfg.get("states") or {})}),
            writable_states=set(cfg.get("writable_states") or scene.writable_states or []),
            private_states=set(cfg.get("private_states") or scene.private_states or []),
        )
    else:
        runtime = dict(
            location=saved.get("location", cfg["location"]),
            relationships=saved.get("relationships", cfg["relationships"]),
            memory=AgentMemory.from_dict(
                saved["memory"],
                name=cfg["name"],
                short_limit=config["agent"]["memory_short_limit"],
                compress_threshold=config["agent"]["memory_compress_threshold"],
            ),
            content_max_length=saved.get("content_max_length", 200),
            states=saved.get("states", {}),
            writable_states=set(saved.get("writable_states", [])),
            private_states=set(saved.get("private_states", [])),
        )

    kwargs = {}
    if cls is ManualAgent:
        kwargs["file_path"] = saved.get("manual_file") if saved else manual_file

    agent = cls(**base, **runtime, **kwargs)

    if saved is not None:
        agent._last_observed_result = saved.get("last_observed_result", "")
        agent._chat_history = saved.get("chat_history", [])

    return agent


def create_gm(scene, config, gm_registry, saved=None):
    """创建 GMAgent，saved 提供存档运行时状态。"""
    gm_cfg = scene.get_gm_config()
    gm = GMAgent(
        events=gm_cfg["events"],
        random_events=gm_cfg["random_events"],
        chance=config["gm"]["random_event_chance"],
        use_llm=config["gm"]["use_llm"],
        llm_chance=config["gm"].get("llm_event_chance", 0.3),
        llm_prompt=gm_cfg.get("llm_prompt", ""),
        world_description=scene.world_description,
        event_tick_window=config["gm"].get("event_tick_window", 3),
        prompt_format=config["gm"].get("prompt_format", "text"),
        history_max_messages=config["gm"].get("chat_history_max_messages", 40),
        gm_registry=gm_registry,
    )

    if saved is not None:
        gm.scheduled_events = [tuple(item) for item in saved["scheduled_events"]]
        gm.random_events = saved["random_events"]
        gm.use_llm = saved.get("use_llm", config["gm"]["use_llm"])
        gm._gm_history = saved.get("history", [])

    return gm


def init_world(scene_name, config, manual_agents=None):
    """新场景初始化：加载场景、创建 Agent 和 GM。"""
    from scenarios import load_scene

    scene = load_scene(scene_name)
    world = scene.init_world()
    validate_agent_configs(scene.agents)

    registry = ActionRegistry()
    scene.setup(registry)

    manual_names = set(manual_agents or config["simulation"].get("manual_agents", []))
    for cfg in scene.agents:
        manual_file = config["simulation"].get("manual_file") if cfg["name"] in manual_names else None
        try:
            agent = create_agent(scene, cfg, config, registry=registry, manual_file=manual_file)
        except (FileNotFoundError, ValueError):
            raise
        world.agents[agent.name] = agent

    world.action_order = [n for n in world.agents if n not in world.npc_names]

    gm_registry = ActionRegistry(include_agent_params=False)
    scene.setup_gm(gm_registry)
    gm = create_gm(scene, config, gm_registry)

    return world, scene, gm, registry


def restore_world(load_path, config, *, scene_loader):
    """从存档恢复世界状态。返回 (world, scene, gm, registry, start_tick)。"""
    data = json.loads(Path(load_path).read_text(encoding="utf-8"))
    data = migrate_save_data(data)

    if data.get("version") != SAVE_VERSION:
        raise ValueError(f"不支持的存档版本: {data.get('version')}")

    scene = scene_loader(data["scene"])

    registry = ActionRegistry()
    scene.setup(registry)

    world = WorldState()
    world.tick = data["tick"]
    world.apply_scene_config(scene)
    world.event_log = [TimelineEvent.from_dict(e) for e in data["event_log"]]
    world.action_order = data["action_order"]
    world.connections = [tuple(p) for p in data.get("connections", [])]
    world.geography.rebuild_adjacency()
    world.environment = data.get("environment", {})

    world.message_bus = MessageBus.from_dict(data["message_bus"])

    agents_by_name = {a["name"]: a for a in scene.agents}
    for name, agent_data in data["agents"].items():
        cfg = agents_by_name[name]
        agent = create_agent(scene, cfg, config, registry=registry, saved=agent_data)
        world.agents[name] = agent

    for name, npc_data in data.get("npcs", {}).items():
        npc = NPC.from_dict(npc_data)
        world.add_npc(npc)

    world.npc_names = set(world.npcs.keys())

    gm_registry = ActionRegistry(include_agent_params=False)
    scene.setup_gm(gm_registry)
    gm = create_gm(scene, config, gm_registry, saved=data["gm"])

    return world, scene, gm, registry


def setup_services(config, scene, gm, world=None):
    """创建模拟核心服务（logger, llm, rule_engine）。"""
    import logging

    log_level = getattr(logging, config["logging"].get("level", "INFO"))
    logger = SimLogger(
        log_file=config["logging"].get("file", "logs/simulation.log"),
        level=log_level,
    )
    llm = LLMClient(config["llm"], logger)
    gm.logger = logger

    if world is not None:
        mb_cfg = config.get("message_bus", {})
        world.message_bus.set_limits(
            max_messages=mb_cfg.get("max_messages"),
            max_inbox_per_agent=mb_cfg.get("max_inbox_per_agent"),
        )
        for agent in world.agents.values():
            agent.logger = logger

    rule_engine = RuleEngine(logger=logger)
    scene.setup_rules(rule_engine)

    return logger, llm, rule_engine


def prepare_world(config, scene_name, manual_agents=None, load_path=None, max_ticks=None):
    """统一装配世界与场景，返回运行所需的全部上下文。"""
    if load_path:
        from scenarios import load_scene

        world, scene, gm, registry = restore_world(
            load_path, config, scene_loader=load_scene
        )
        start_tick = world.tick + 1
    else:
        world, scene, gm, registry = init_world(config=config, scene_name=scene_name, manual_agents=manual_agents)
        start_tick = 1

    remaining = max_ticks or config["simulation"]["max_ticks"]
    return world, scene, gm, registry, start_tick, remaining
