#!/usr/bin/env python3
"""通用模拟入口：支持多场景选择。"""

from render.console import ConsoleRenderer
from llm.client import LLMClient
from core.rules import RuleEngine
from core.engine import SimulationEngine
from core.gm import GMAgent
from core.agent import Agent
from core.manual_agent import ManualAgent
from core.action import ActionRegistry
from core.logger import SimLogger
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


def _init_world(config: dict, scene_name: str, manual_agents: list[str] | None):
    """新场景初始化：加载场景、创建 Agent 和 GM"""
    scene = load_scene(scene_name)
    world = scene.init_world()
    validate_agent_configs(scene.agents)
    scene.setup(registry := ActionRegistry())
    manual_names = set(manual_agents or config["simulation"].get("manual_agents", []))

    for cfg in scene.agents:
        if cfg["name"] in manual_names:
            manual_file = config["simulation"].get("manual_file")
            try:
                agent = ManualAgent.from_config(scene, cfg, config, file_path=manual_file)
            except (FileNotFoundError, ValueError) as e:
                print(f"❌ 手动控制配置错误: {e}")
                sys.exit(1)
        else:
            agent = Agent.from_config(scene, cfg, config)
        world.agents[agent.name] = agent

    world.action_order = [n for n in world.agents if n not in world.npc_names]

    gm_registry = ActionRegistry(include_agent_params=False)
    scene.setup_gm(gm_registry)
    gm = GMAgent.from_config(scene, config, gm_registry)

    return world, scene, gm, registry


def _load_world(load_path: str, config: dict, max_ticks: int | None):
    """从存档恢复世界状态"""
    from core.save_load import load_simulation_state

    world, scene, gm = load_simulation_state(load_path, config)
    scene.setup(registry := ActionRegistry())
    gm_registry = ActionRegistry(include_agent_params=False)
    scene.setup_gm(gm_registry)
    gm.registry = gm_registry
    start_tick = world.tick + 1
    remaining = max_ticks or config["simulation"]["max_ticks"]
    print(f"从存档恢复 [{scene.name}]，当前 tick={world.tick}，继续运行 {remaining} 个 tick\n")
    return world, scene, gm, registry, start_tick


def _setup_services(config: dict, scene, gm):
    """创建模拟核心服务（logger, llm, rule_engine）"""
    log_level = getattr(__import__("logging"), config["logging"].get("level", "INFO"))
    logger = SimLogger(
        log_file=config["logging"].get("file", "logs/simulation.log"),
        level=log_level,
    )
    llm = LLMClient(config["llm"], logger)
    gm.logger = logger

    rule_engine = RuleEngine()
    scene.setup_rules(rule_engine)

    return logger, llm, rule_engine


def _make_renderer(config: dict, scene) -> ConsoleRenderer:
    """创建控制台渲染器（仅 CLI 路径使用）"""
    return ConsoleRenderer(
        render_config=scene.render_config,
        show_full_inbox=config["simulation"].get("show_full_inbox", False),
        show_full_monologue=config["simulation"].get("show_full_monologue", True),
    )


def _print_scene_header(scene):
    """打印场景信息"""
    print(f"\n{'='*50}")
    print(f"场景: {scene.name}")
    print(f"角色: {', '.join([a['name'] for a in scene.agents])}")
    print(f"{'='*50}\n")


def _save_state(world, gm, scene, save_path: str):
    """保存模拟状态"""
    from core.save_load import save_simulation_state

    scene_module = scene.__class__.__module__.split(".")[-1]
    save_simulation_state(world, gm, scene_module, scene.name, save_path)
    print(f"状态已保存到 {save_path}")


async def run_tui_simulation(config: dict, scene_name: str, max_ticks: int | None = None, mode: str | None = None, manual_agents: list[str] | None = None, load_path: str | None = None, save_path: str | None = None):
    """使用 Textual TUI 运行模拟"""
    if load_path:
        world, scene, gm, registry, start_tick = _load_world(load_path, config, max_ticks)
    else:
        world, scene, gm, registry = _init_world(config, scene_name, manual_agents)
        start_tick = 1

    remaining = max_ticks or config["simulation"]["max_ticks"]

    logger, llm, rule_engine = _setup_services(config, scene, gm)

    from render.tui_app import SimulationTuiApp
    app = SimulationTuiApp(
        world=world, scene=scene, gm=gm, registry=registry,
        config=config, start_tick=start_tick, remaining=remaining,
        mode=mode, save_path=save_path,
        logger=logger, llm=llm, rule_engine=rule_engine,
    )
    try:
        await app.run_async()
    finally:
        logger.close()


async def run_simulation(config: dict, scene_name: str, max_ticks: int | None = None, mode: str | None = None, manual_agents: list[str] | None = None, load_path: str | None = None, save_path: str | None = None):
    """运行模拟"""
    remaining = max_ticks or config["simulation"]["max_ticks"]

    if load_path:
        world, scene, gm, registry, start_tick = _load_world(load_path, config, max_ticks)
    else:
        world, scene, gm, registry = _init_world(config, scene_name, manual_agents)
        start_tick = 1
        _print_scene_header(scene)

    logger, llm, rule_engine = _setup_services(config, scene, gm)
    renderer = _make_renderer(config, scene)
    engine = SimulationEngine(
        world, gm, registry, llm, rule_engine, logger, config
    )

    actual_mode = mode or config["simulation"]["mode"]
    for tick in range(start_tick, start_tick + remaining):
        actions = await engine.run_tick(tick)
        renderer.render_tick(world, actions)
        if actual_mode == "interactive":
            input("按回车继续下一个 tick...")
        else:
            await asyncio.sleep(config["simulation"]["auto_delay"])

    renderer.render_summary(world)
    if save_path:
        _save_state(world, gm, scene, save_path)
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
    parser.add_argument("--tui", action="store_true", help="使用 TUI 界面")

    args = parser.parse_args()

    if args.load and args.scene is not None:
        print("❌ --load 和 --scene 不能同时使用（存档中已包含场景信息）")
        sys.exit(1)

    config = load_config(args.config)

    if args.scene is None:
        args.scene = config.get("scene", "tavern")

    if args.manual_file:
        config.setdefault("simulation", {})["manual_file"] = args.manual_file

    if args.list_scenes:
        scenes = list_available_scenes()
        print("可用场景:")
        for scene in scenes:
            print(f"  - {scene}")
        return

    if args.tui:
        asyncio.run(run_tui_simulation(config, args.scene, args.ticks, args.mode, args.manual, load_path=args.load, save_path=args.save))
        return

    asyncio.run(run_simulation(config, args.scene, args.ticks, args.mode, args.manual, load_path=args.load, save_path=args.save))


if __name__ == "__main__":
    main()
