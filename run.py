#!/usr/bin/env python3
"""通用模拟入口：支持多场景选择。"""

from __future__ import annotations

import argparse
import asyncio
import sys

from dotenv import load_dotenv

from app.config import load_config
from app.factory import prepare_world, setup_services
from core.engine import SimulationEngine
from core.save_load import save_simulation_state
from render.console import ConsoleRenderer
from scenarios import list_available_scenes

load_dotenv()


def _make_renderer(config: dict, scene, registry=None) -> ConsoleRenderer:
    """创建控制台渲染器（仅 CLI 路径使用）"""
    return ConsoleRenderer(
        render_config=scene.render_config,
        show_full_inbox=config["simulation"].get("show_full_inbox", False),
        show_full_monologue=config["simulation"].get("show_full_monologue", True),
        registry=registry,
    )


def _print_scene_header(scene):
    """打印场景信息"""
    print(f"\n{'='*50}")
    print(f"场景: {scene.name}")
    print(f"角色: {', '.join([a['name'] for a in scene.agents])}")
    print(f"{'='*50}\n")


def _save_state(world, gm, scene, save_path: str):
    """保存模拟状态"""
    scene_module = scene.__class__.__module__.split(".")[-1]
    save_simulation_state(world, gm, scene_module, scene.name, save_path)
    print(f"状态已保存到 {save_path}")


async def run_tui_simulation(config: dict, scene_name: str, max_ticks: int | None = None, mode: str | None = None, manual_agents: list[str] | None = None, load_path: str | None = None, save_path: str | None = None):
    """使用 Textual TUI 运行模拟"""
    world, scene, gm, registry, start_tick, remaining = prepare_world(
        config, scene_name, manual_agents, load_path, max_ticks
    )

    logger, llm, rule_engine = setup_services(config, scene, gm, world)

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
    world, scene, gm, registry, start_tick, remaining = prepare_world(
        config, scene_name, manual_agents, load_path, max_ticks
    )
    if not load_path:
        _print_scene_header(scene)

    logger, llm, rule_engine = setup_services(config, scene, gm, world)
    renderer = _make_renderer(config, scene, registry)
    engine = SimulationEngine(world, gm, llm, rule_engine, logger, config)

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
