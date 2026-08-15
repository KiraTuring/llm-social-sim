"""ManualAgent 测试：默认 observe、通配 tick、行动执行、非法行动回退、文件错误。"""

import os
import tempfile

import pytest
from conftest import write_plan
from core.action import ActionRegistry
from core.actions.common import SpeakAction, WhisperAction, MoveAction, ObserveAction, InteractAction
from core.manual_agent import ManualAgent
from scenarios.tavern import TavernScene
from scenarios.utils import validate_agent_configs

SCENE = TavernScene()
REGISTRY = ActionRegistry()

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


async def test_no_config_falls_back_to_observe():
    """无配置 → observe"""
    world, agents = await build_world()
    action = await think(agents["老巴克"], world, 1)
    assert action.action_type == "observe", action


async def test_wildcard_tick():
    """通配 *：未单独配置的 tick 重复执行"""
    world, agents = await build_world({
        "老巴克": {"*": {"action_type": "speak", "content": "欢迎光临"}},
    })
    agent = agents["老巴克"]
    for tick in (1, 7):
        action = await think(agent, world, tick)
        assert action.action_type == "speak", action


async def test_specific_tick_overrides_wildcard():
    """具体 tick 优先于通配"""
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


async def test_speak_executes_and_produces_message():
    """speak 执行并产生消息"""
    world, agents = await build_world({
        "老巴克": {"1": {"action_type": "speak", "target": "艾莉娅", "content": "你好"}},
    })
    agent = agents["老巴克"]
    action = await think(agent, world, 1)
    messages = await agent.act(action, world, REGISTRY)
    assert action.action_type == "speak", action
    assert any(m.msg_type == "speech" for m in messages), messages


async def test_move_executes_and_changes_location():
    """move 执行并改变位置"""
    world, agents = await build_world({
        "雷恩": {"1": {"action_type": "move", "target": "主厅"}},
    })
    agent = agents["雷恩"]
    action = await think(agent, world, 1)
    await agent.act(action, world, REGISTRY)
    assert agent.location == "主厅", agent.location


async def test_unknown_action_type_falls_back():
    """未知 action_type → observe"""
    world, agents = await build_world({
        "老巴克": {"1": {"action_type": "fly"}},
    })
    agent = agents["老巴克"]
    action = await think(agent, world, 1)
    assert action.action_type == "observe", action


async def test_unreachable_target_falls_back():
    """目标不可达 → observe"""
    world, agents = await build_world({
        "雷恩": {"1": {"action_type": "move", "target": "后厨"}},
    })
    agent = agents["雷恩"]
    action = await think(agent, world, 1)
    assert action.action_type == "observe", action


async def test_whisper_cross_location_falls_back():
    """whisper 非同位置 → observe"""
    world, agents = await build_world({
        "雷恩": {"1": {"action_type": "whisper", "target": "老巴克", "content": "嘘"}},
    })
    agent = agents["雷恩"]
    action = await think(agent, world, 1)
    assert action.action_type == "observe", action


def test_missing_file_raises():
    """文件缺失 → FileNotFoundError"""
    with pytest.raises(FileNotFoundError):
        ManualAgent.from_config(SCENE, CFG["老巴克"], CONFIG, file_path="/nonexistent/manual.json")


def test_invalid_json_raises():
    """JSON 格式错误 → ValueError"""
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        f.write("{not valid json")
    with pytest.raises(ValueError):
        ManualAgent.from_config(SCENE, CFG["老巴克"], CONFIG, file_path=path)


async def test_content_with_tags_not_parsed():
    """内容含标签文本 → 直接构造 Action，不被 parse_text 截断，params 原样保留"""
    world, agents = await build_world({
        "老巴克": {"1": {
            "action_type": "speak",
            "content": "甲说[/CONTENT]乙说[/ACTION]",
            "params": {"tone": "轻声"},
        }},
    })
    agent = agents["老巴克"]
    action = await think(agent, world, 1)
    assert action.action_type == "speak", action
    assert action.content == "甲说[/CONTENT]乙说[/ACTION]", action.content
    assert action.params == {"tone": "轻声"}, action.params
