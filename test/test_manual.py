"""ManualAgent 测试：默认 observe、通配 tick、行动执行、非法行动回退、文件错误。"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv

from core.action import ActionRegistry
from core.manual_agent import ManualAgent
from scenarios.tavern import TavernScene
from scenarios.utils import validate_agent_configs

load_dotenv()

SCENE = TavernScene()
REGISTRY = ActionRegistry()
from core.actions.common import SpeakAction, WhisperAction, MoveAction, ObserveAction, InteractAction

for action_cls in (SpeakAction, WhisperAction, MoveAction, ObserveAction, InteractAction):
    REGISTRY.register(action_cls())

CONFIG = {
    "agent": {
        "prompt_format": "text",
        "memory_short_limit": 10,
        "memory_compress_threshold": 30,
        "content_max_length": 200,
        "inbox_limit": 5,
    }
}

CFG = {c["name"]: c for c in SCENE.agents}


def write_plan(plan: dict) -> str:
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False)
    return path


async def build_world(plans: dict | None = None) -> tuple:
    """构建完整场景：所有角色都创建为 ManualAgent（plans 指定各自的计划）。"""
    validate_agent_configs(SCENE.agents)
    world = SCENE.init_world()
    agents = {}
    for name, cfg in CFG.items():
        plan = (plans or {}).get(name, {})
        agent = ManualAgent.from_config(
            SCENE, cfg, CONFIG, file_path=write_plan({name: plan})
        )
        world.agents[name] = agent
        agents[name] = agent
    return world, agents


async def think(agent, world, tick):
    return await agent.think(
        None, REGISTRY, "", tick, world.build_validation_context(agent.name)
    )


async def run_tests():
    print("测试 ManualAgent")
    print("=" * 50)

    # 1. 无配置 → observe
    world, agents = await build_world()
    action = await think(agents["老巴克"], world, 1)
    assert action.action_type == "observe", action
    print("[1] 无配置 tick → observe OK")

    # 2. 通配 *
    world, agents = await build_world({
        "老巴克": {"*": {"action_type": "speak", "content": "欢迎光临"}},
    })
    agent = agents["老巴克"]
    for tick in (1, 7):
        action = await think(agent, world, tick)
        assert action.action_type == "speak", action
    print("[2] 通配 * OK")

    # 3. 具体 tick 优先于通配
    world, agents = await build_world({
        "老巴克": {
            "1": {"action_type": "move", "target": "主厅"},
            "*": {"action_type": "observe"},
        },
    })
    agent = agents["老巴克"]
    a1 = await think(agent, world, 1)
    assert a1.action_type == "move", a1
    a2 = await think(agent, world, 2)
    assert a2.action_type == "observe", a2
    print("[3] 具体 tick 优先于通配 OK")

    # 4. speak 执行并产生消息
    world, agents = await build_world({
        "老巴克": {"1": {"action_type": "speak", "target": "艾莉娅", "content": "你好"}},
    })
    agent = agents["老巴克"]
    action = await think(agent, world, 1)
    messages = await agent.act(action, world, REGISTRY)
    assert action.action_type == "speak", action
    assert any(m.msg_type == "speech" for m in messages), messages
    print("[4] speak 执行 OK")

    # 5. move 执行并改变位置
    world, agents = await build_world({
        "雷恩": {"1": {"action_type": "move", "target": "主厅"}},
    })
    agent = agents["雷恩"]
    action = await think(agent, world, 1)
    await agent.act(action, world, REGISTRY)
    assert agent.location == "主厅", agent.location
    print("[5] move 执行 OK")

    # 6. 未知 action_type → observe
    world, agents = await build_world({
        "老巴克": {"1": {"action_type": "fly"}},
    })
    agent = agents["老巴克"]
    action = await think(agent, world, 1)
    assert action.action_type == "observe", action
    print("[6] 未知 action_type 回退 OK")

    # 7. 目标不可达 → observe
    world, agents = await build_world({
        "雷恩": {"1": {"action_type": "move", "target": "后厨"}},
    })
    agent = agents["雷恩"]
    action = await think(agent, world, 1)
    assert action.action_type == "observe", action
    print("[7] 不可达目标回退 OK")

    # 8. whisper 非同位置 → observe
    world, agents = await build_world({
        "雷恩": {"1": {"action_type": "whisper", "target": "老巴克", "content": "嘘"}},
    })
    agent = agents["雷恩"]
    action = await think(agent, world, 1)
    assert action.action_type == "observe", action
    print("[8] whisper 非同位置回退 OK")

    # 9. 文件缺失 → FileNotFoundError
    try:
        ManualAgent.from_config(SCENE, CFG["老巴克"], CONFIG, file_path="/nonexistent/manual.json")
        raise AssertionError("应当抛出 FileNotFoundError")
    except FileNotFoundError:
        pass
    print("[9] 文件缺失报错 OK")

    # 10. JSON 格式错误 → ValueError
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        f.write("{not valid json")
    try:
        ManualAgent.from_config(SCENE, CFG["老巴克"], CONFIG, file_path=path)
        raise AssertionError("应当抛出 ValueError")
    except ValueError:
        pass
    print("[10] JSON 格式错误报错 OK")

    print("=" * 50)
    print("全部 ManualAgent 测试通过")


asyncio.run(run_tests())
