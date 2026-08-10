#! /usr/bin/env python3
"""打印指定场景中 Agent 或 GM 的 system prompt，方便开发和调试"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.agent import Agent
from core.action import ActionRegistry
from core.gm import GMAgent
from scenarios.utils import load_scene


def main():
    parser = argparse.ArgumentParser(description="打印 Agent/GM system prompt")
    parser.add_argument("scene", help="场景名称")
    parser.add_argument("--agent", "-a", help="只打印指定 Agent")
    parser.add_argument("--gm", action="store_true", help="打印 GM 的 system prompt 与世界上下文")
    args = parser.parse_args()

    import yaml

    config = yaml.safe_load(open(Path(__file__).parent.parent / "config.yaml"))

    scene = load_scene(args.scene)

    if args.gm:
        gm_registry = ActionRegistry(include_agent_params=False)
        scene.setup_gm(gm_registry)
        gm = GMAgent.from_config(scene, config, gm_registry)

        world = scene.init_world()
        for cfg in scene.agents:
            world.agents[cfg["name"]] = Agent.from_config(scene, cfg, config)

        sep = "=" * 60
        print(f"\n{sep}")
        print(f"  GM system prompt ({scene.name})")
        print(f"{sep}")
        print(gm._build_gm_prompt())
        print(f"\n{sep}")
        print("  世界上下文 (tick 0)")
        print(f"{sep}")
        print(gm._build_world_context(world))
        return

    registry = ActionRegistry()
    scene.setup(registry)

    agents = [a for a in scene.agents if not args.agent or a["name"] == args.agent]

    if not agents:
        print(f"未找到 Agent: {args.agent}")
        sys.exit(1)

    for cfg in agents:
        agent = Agent.from_config(scene, cfg, config)

        prompt = agent.build_system_prompt(registry)

        sep = "=" * 60
        print(f"\n{sep}")
        print(f"  {cfg['name']} ({cfg['role']})")
        print(f"{sep}")
        print(prompt)

    print(f"\n{'=' * 60}")
    print(f"  共 {len(agents)} 个 Agent")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
