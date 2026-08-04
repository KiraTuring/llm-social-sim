"""存档往返测试：Agent/GMAgent to_dict/from_dict 边界、格式稳定、版本迁移入口。"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv

from core.action import ActionRegistry
from core.agent import Agent
from core.gm import GMAgent
from core.manual_agent import ManualAgent
from core.message import Message, BROADCAST
from core.save_load import SAVE_VERSION, save_simulation_state, load_simulation_state
from scenarios.tavern import TavernScene
from scenarios.utils import validate_agent_configs

load_dotenv()

SCENE = TavernScene()
CFG = {c["name"]: c for c in SCENE.agents}

CONFIG = {
    "agent": {
        "prompt_format": "text",
        "memory_short_limit": 10,
        "memory_compress_threshold": 30,
        "content_max_length": 200,
        "inbox_limit": 5,
    },
    "gm": {
        "prompt_format": "text",
        "chat_history_max_messages": 40,
        "use_llm": True,
        "random_event_chance": 0.2,
        "llm_event_chance": 0.2,
        "message_limit": 5,
    },
}

AGENT_KEYS = {
    "role", "personality", "goal", "location", "relationships", "states",
    "writable_states", "private_states", "content_max_length", "agent_type",
    "last_observed_result", "prompt_format", "chat_history", "memory",
}
TOP_LEVEL_KEYS = {
    "version", "scene", "scene_display", "tick", "locations", "connections",
    "action_order", "event_log", "environment", "message_bus", "gm", "agents",
}
GM_KEYS = {"scheduled_events", "random_events", "use_llm", "history"}


def write_plan(plan: dict) -> str:
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False)
    return path


def build_world(plan_path: str):
    """构建含一个 ManualAgent 的 tavern 世界 + 带运行时状态的 GM。"""
    validate_agent_configs(SCENE.agents)
    world = SCENE.init_world()
    for name, cfg in CFG.items():
        if name == "老巴克":
            world.agents[name] = ManualAgent.from_config(
                SCENE, cfg, CONFIG, file_path=plan_path
            )
        else:
            world.agents[name] = Agent.from_config(SCENE, cfg, CONFIG)
    world.action_order = list(world.agents)
    world.tick = 3
    world.add_event("屋外传来马蹄声")
    world.update_environment("壁炉旁", "火焰大小", "微弱")
    world.message_bus.send(Message(
        sender="GM", recipients=[BROADCAST], content="窗外下起了雨",
        msg_type="system_event", tick=3,
    ))
    world.message_bus.send(Message(
        sender="老巴克", recipients=["艾莉娅"], target="艾莉娅",
        content="欢迎光临", msg_type="speech", tick=3,
    ))

    gm_registry = ActionRegistry(include_agent_params=False)
    SCENE.setup_gm(gm_registry)
    gm = GMAgent.from_config(SCENE, CONFIG, gm_registry)
    gm._gm_history = [
        {"role": "user", "content": "t3 上下文", "tick": 3},
        {"role": "assistant", "content": "窗外下起了雨", "tick": 3},
    ]
    return world, gm


def run_tests():
    print("测试存档往返（save_load）")
    print("=" * 50)

    plan_path = write_plan({"老巴克": {"1": {"action_type": "observe"}}})
    world, gm = build_world(plan_path)
    save_path = os.path.join(tempfile.mkdtemp(), "roundtrip.json")
    save_simulation_state(world, gm, "tavern", SCENE.name, save_path)

    # 1. 往返一致
    world2, scene2, gm2 = load_simulation_state(save_path, CONFIG)
    assert scene2.name == SCENE.name
    assert world2.tick == world.tick
    assert world2.locations == world.locations
    assert world2.connections == [tuple(p) for p in world.connections]
    assert world2.action_order == world.action_order
    assert world2.event_log == world.event_log
    assert world2.environment == world.environment
    assert world2.message_bus.to_dict() == world.message_bus.to_dict()

    for name, a1 in world.agents.items():
        a2 = world2.agents[name]
        assert a2.location == a1.location
        assert a2.states == a1.states
        assert a2.relationships == a1.relationships
        assert set(a2._writable_states) == set(a1._writable_states)
        assert set(a2._private_states) == set(a1._private_states)
        assert a2.content_max_length == a1.content_max_length
        assert a2._last_observed_result == a1._last_observed_result
        assert a2.prompt_format == a1.prompt_format
        assert a2._chat_history == a1._chat_history
        assert a2.memory.to_dict() == a1.memory.to_dict()
    assert isinstance(world2.agents["老巴克"], ManualAgent)
    assert isinstance(world2.agents["雷恩"], Agent)
    assert not isinstance(world2.agents["雷恩"], ManualAgent)

    assert gm2.scheduled_events == gm.scheduled_events
    assert gm2.random_events == gm.random_events
    assert gm2.use_llm == gm.use_llm
    assert gm2._gm_history == gm._gm_history
    print("[1] 往返一致（world/agent/gm/ManualAgent）OK")

    # 2. 格式稳定：key 逐项匹配现有 schema
    raw = json.loads(Path(save_path).read_text(encoding="utf-8"))
    assert raw["version"] == SAVE_VERSION == 1
    assert set(raw.keys()) == TOP_LEVEL_KEYS, set(raw.keys())
    assert set(raw["gm"].keys()) == GM_KEYS
    assert set(raw["agents"]["雷恩"].keys()) == AGENT_KEYS
    assert set(raw["agents"]["老巴克"].keys()) == AGENT_KEYS | {"manual_file"}
    assert raw["agents"]["老巴克"]["manual_file"] == plan_path
    print("[2] 存档格式稳定（version 1 schema）OK")

    # 3. 未知版本号 → ValueError
    bad_path = os.path.join(tempfile.mkdtemp(), "bad.json")
    Path(bad_path).write_text(json.dumps({"version": 99}), encoding="utf-8")
    try:
        load_simulation_state(bad_path, CONFIG)
        raise AssertionError("应当抛出 ValueError")
    except ValueError:
        pass
    print("[3] 未知版本号报错 OK")

    print("=" * 50)
    print("全部存档往返测试通过")


run_tests()
