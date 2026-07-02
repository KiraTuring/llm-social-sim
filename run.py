#!/usr/bin/env python3
"""通用模拟入口：支持多场景选择。"""

from render.console import ConsoleRenderer
from memory.memory import AgentMemory
from llm.client import LLMClient
from core.rules import RuleEngine
from core.gm import GMAgent
from core.agent import Agent
from core.manual_agent import ManualAgent
from core.action import ActionRegistry
import argparse
import asyncio
import os
import sys
from pathlib import Path

from scenarios.utils import load_scene, list_available_scenes, validate_agent_configs
from dotenv import load_dotenv

load_dotenv()


def load_config(config_path: str | None = None) -> dict:
    """加载配置文件"""
    if config_path is None:
        config_path = str(Path(__file__).parent / "config.yaml")

    import yaml

    with open(config_path) as f:
        config = yaml.safe_load(f)

    return _expand_env_vars(config)


def _expand_env_vars(obj: any) -> any:
    """递归展开配置中的环境变量"""
    if isinstance(obj, str):
        if obj.startswith("${") and obj.endswith("}"):
            var_name = obj[2:-1]
            return os.getenv(var_name, "")
        return obj
    elif isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_expand_env_vars(item) for item in obj]
    return obj


async def run_simulation(config: dict, scene_name: str, max_ticks: int | None = None, mode: str | None = None, manual_agents: list[str] | None = None, load_path: str | None = None, save_path: str | None = None):
    """运行模拟"""

    if load_path:
        from core.save_load import load_simulation_state
        world, scene, gm = load_simulation_state(load_path, config)
        scene.setup(registry := ActionRegistry())
        start_tick = world.tick + 1
        remaining = max_ticks or config["simulation"]["max_ticks"]
        print(f"从存档恢复 [{scene.name}]，当前 tick={world.tick}，继续运行 {remaining} 个 tick\n")
    else:
        scene = load_scene(scene_name)
        world = scene.init_world()
        validate_agent_configs(scene.agents)
        scene.setup(registry := ActionRegistry())
        manual_names = set(manual_agents or config["simulation"].get("manual_agents", []))

        for cfg in scene.agents:
            memory = AgentMemory(
                name=cfg["name"],
                short_limit=config["agent"]["memory_short_limit"],
                compress_threshold=config["agent"]["memory_compress_threshold"],
                relation_limit=config["agent"].get("relation_display_limit", 3),
            )
            agent_kwargs = dict(
                name=cfg["name"],
                role=cfg["role"],
                personality=cfg["personality"],
                goal=cfg["goal"],
                location=cfg["location"],
                relationships=cfg["relationships"],
                memory=memory,
                content_max_length=config["agent"].get("content_max_length", 200),
                max_energy=config["agent"].get("max_energy", 100),
                inbox_limit=config["agent"].get("inbox_limit", 5),
            )
            if cfg["name"] in manual_names:
                manual_file = config["simulation"].get("manual_file")
                agent = ManualAgent(**agent_kwargs, file_path=manual_file)
            else:
                agent = Agent(**agent_kwargs)
            world.agents[agent.name] = agent

        world.action_order = list(world.agents.keys())

        gm_cfg = scene.get_gm_config()
        gm = GMAgent(
            events=gm_cfg["events"],
            random_events=gm_cfg["random_events"],
            chance=config["gm"]["random_event_chance"],
        )

        start_tick = 1
        remaining = max_ticks or config["simulation"]["max_ticks"]

    from core.logger import SimLogger

    log_level = getattr(__import__("logging"), config["logging"].get("level", "INFO"))
    logger = SimLogger(
        log_file=config["logging"].get("file", "logs/simulation.log"),
        level=log_level,
    )

    llm = LLMClient(config["llm"], logger)

    rule_engine = RuleEngine()
    scene.setup_rules(rule_engine)

    renderer = ConsoleRenderer(
        render_config=scene.render_config,
        show_full_inbox=config["simulation"].get("show_full_inbox", False),
        show_full_monologue=config["simulation"].get("show_full_monologue", True),
    )

    if not load_path:
        print(f"\n{'='*50}")
        print(f"场景: {scene.name}")
        print(f"角色: {', '.join([a['name'] for a in scene.agents])}")
        print(f"{'='*50}\n")

    actual_mode = mode or config["simulation"]["mode"]

    for tick in range(start_tick, start_tick + remaining):
        world.tick = tick

        logger.log_tick_start(tick)

        await gm.check_and_inject(world)

        agent_actions = {}
        for agent_name in world.action_order:
            agent = world.agents[agent_name]

            context = await agent.perceive(world, llm_client=llm)
            agents_by_location = {}
            for loc in world.locations:
                agents_by_location[loc] = world.get_agents_in_location(loc)
            validation_context = {
                "agent_name": agent_name,
                "agent_location": world.agents[agent_name].location,
                "agent_names": list(world.agents.keys()),
                "locations": world.locations,
                "agents_by_location": agents_by_location,
            }
            action = await agent.think(llm, registry, context, tick, validation_context)
            messages = await agent.act(action, world, registry)

            agent_actions[agent_name] = action

            action_dict = {
                "action_type": action.action_type,
                "target": action.target,
                "content": action.content,
                "internal_monologue": action.internal_monologue,
                "result": action.result,
            } if action else {}
            logger.log_agent_action(agent_name, tick, action_dict)

            for msg in messages:
                logger.log_message({
                    "sender": msg.sender,
                    "recipients": msg.recipients,
                    "target": msg.target,
                    "content": msg.content,
                    "msg_type": msg.msg_type,
                    "tick": msg.tick,
                })
                rule_engine.trigger(msg.msg_type, msg, world)

        world.message_bus.get_all()

        renderer.render_tick(world, agent_actions)

        if config["simulation"]["rotate_order"]:
            world.rotate_order()

        logger.log_tick_end(tick)

        if actual_mode == "interactive":
            input("按回车继续下一个 tick...")
        else:
            await asyncio.sleep(config["simulation"]["auto_delay"])

    renderer.render_summary(world)

    if save_path:
        from core.save_load import save_simulation_state
        scene_module = scene.__class__.__module__.split(".")[-1]
        save_simulation_state(world, gm, scene_module, scene.name, save_path)
        print(f"状态已保存到 {save_path}")

    logger.close()


def main():
    parser = argparse.ArgumentParser(description="LLM 社会模拟引擎", formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument("--scene", "-s", type=str, help="场景名称（如: tavern）", default=None)
    parser.add_argument("--ticks", "-t", type=int, help="运行 tick 数", default=None)
    parser.add_argument("--mode", "-m", type=str, choices=["interactive", "auto"], help="运行模式", default=None)
    parser.add_argument("--list-scenes", "-l", action="store_true", help="列出所有可用场景")
    parser.add_argument("--config", "-c", type=str, help="配置文件路径", default=None)
    parser.add_argument("--manual", nargs="*", help="手动控制的 Agent 名称，多个用空格分隔", default=None)
    parser.add_argument("--manual-file", type=str, help="手动控制 JSON 文件路径", default=None)
    parser.add_argument("--save", type=str, help="运行结束后保存状态到文件", default=None)
    parser.add_argument("--load", type=str, help="从存档文件继续运行", default=None)

    args = parser.parse_args()

    if args.load and args.scene is not None:
        print("❌ --load 和 --scene 不能同时使用（存档中已包含场景信息）")
        sys.exit(1)

    if args.scene is None:
        args.scene = "tavern"

    config = load_config(args.config)

    if args.manual_file:
        config.setdefault("simulation", {})["manual_file"] = args.manual_file

    if args.list_scenes:
        scenes = list_available_scenes()
        print("可用场景:")
        for scene in scenes:
            print(f"  - {scene}")
        return

    asyncio.run(run_simulation(config, args.scene, args.ticks, args.mode, args.manual, load_path=args.load, save_path=args.save))


if __name__ == "__main__":
    main()
