#!/usr/bin/env python3
"""测试动态 NPC：Character 继承、AddNpcAction、接口兼容（离线 mock）。"""

import os
import tempfile

import pytest
from core.action import ActionRegistry
from actions.common import SpeakAction, ObserveAction, WhisperAction, MoveAction
from actions.gm_npc import AddNpcAction, NpcMoveAction, NpcSpeakAction, RemoveNpcAction
from actions.gm_tools import ModifyCharStateAction
from core.agent import Agent
from core.character import Character, NPC
from core.gm import GMAgent
from core.scene import Scene
from core.save_load import save_simulation_state, load_simulation_state
from render.tui_info import is_npc
from scenarios._test import _TestScene

SCENE = _TestScene()
AGENT_REGISTRY = ActionRegistry()
SCENE.setup(AGENT_REGISTRY)
CONFIG = {
    "agent": {
        "prompt_format": "text",
        "memory_short_limit": 10,
        "memory_compress_threshold": 30,
        "content_max_length": 200,
    },
    "gm": {
        "prompt_format": "text",
        "chat_history_max_messages": 40,
        "use_llm": False,
        "random_event_chance": 0.0,
        "llm_event_chance": 0.0,
        "message_limit": 5,
    },
}


def _build_world():
    """构造含动态 NPC 的世界：在书房添加一个神秘旅人。"""
    world = SCENE.init_world()
    world.agents["测试甲"] = Agent.from_config(
        SCENE, SCENE.agents[0], CONFIG, registry=AGENT_REGISTRY
    )
    world.agents["测试甲"].location = "大厅"
    world.rebuild_location_index()
    ok = world.add_npc(NPC(name="神秘旅人", location="书房", role="旅人", goal="打听消息"))
    assert ok is None, ok
    return world


def _gm_tool_names(scene) -> set:
    reg = ActionRegistry(include_agent_params=False)
    scene.setup_gm(reg)
    return {s["function"]["name"] for s in reg.get_tool_schemas()}


def test_agent_npc_are_characters():
    """Character 继承：Agent 与 NPC 都是 Character"""
    assert issubclass(Agent, Character)
    assert issubclass(NPC, Character)


def test_add_npc_creates_and_indexes():
    """AddNpcAction 创建成功：npcs + npc_names + 位置索引"""
    world = SCENE.init_world()
    world.rebuild_location_index()
    action = AddNpcAction()
    spec_ctx = {"agent_names": ["测试甲"], "npc_names": [], "locations": world.locations}
    assert action.validate_params({"npc_name": "神秘旅人", "location": "书房"}, spec_ctx) is None
    _, result = action.execute("GM", {"npc_name": "神秘旅人", "location": "书房", "role": "旅人"}, world)
    assert "神秘旅人" in world.npcs
    assert "神秘旅人" in world.npc_names
    assert "神秘旅人" in world.get_characters_in_location("书房")
    assert "神秘旅人" not in world.get_agents_in_location("书房")
    assert "已出现在书房" in result["result"]
    assert any("新 NPC 出现" in e for e in world.event_log)


def test_add_npc_validation_rejects():
    """校验：重名拒绝 + 非法位置拒绝"""
    world = _build_world()
    spec_ctx = {"agent_names": ["测试甲"], "npc_names": ["神秘旅人"], "locations": world.locations}
    err = AddNpcAction().validate_params({"npc_name": "神秘旅人", "location": "书房"}, spec_ctx)
    assert err is not None and "已存在" in err
    err = AddNpcAction().validate_params({"npc_name": "新来者", "location": "不存在的地方"}, spec_ctx)
    assert err is not None and "不是有效位置" in err
    # 与现有 agent 重名
    err = AddNpcAction().validate_params({"npc_name": "测试甲", "location": "书房"}, spec_ctx)
    assert err is not None and "已存在" in err


def test_hearable_agents_for_npc():
    """get_hearable_agents 对 NPC 不崩溃且返回可见 agent"""
    world = _build_world()
    hearable = world.get_hearable_agents("神秘旅人")
    assert "测试甲" in hearable  # 书房可见大厅
    assert "神秘旅人" not in hearable  # 自己排除
    assert isinstance(hearable, list)


def test_observe_sees_dynamic_npc():
    """ObserveAction 能看到动态 NPC（role/state）"""
    world = _build_world()
    agent = Agent.from_config(SCENE, SCENE.agents[0], CONFIG, registry=AGENT_REGISTRY)
    world.agents[agent.name] = agent
    # 把 agent 挪到书房与 NPC 同处，便于 observe
    MoveAction().execute(agent.name, {"target": "书房"}, world)
    _, result = ObserveAction().execute(agent.name, {}, world)
    obs = result["observed"]
    assert "神秘旅人" in obs and "旅人" in obs, obs


def test_speak_whisper_validate_for_npc():
    """SpeakAction / WhisperAction 对动态 NPC 校验通过"""
    world = _build_world()
    agent = Agent.from_config(SCENE, SCENE.agents[0], CONFIG, registry=AGENT_REGISTRY)
    agent.location = "书房"
    world.agents[agent.name] = agent
    ctx = world.build_validation_context(agent.name)
    reg = ActionRegistry()
    for cls in (SpeakAction, WhisperAction, ObserveAction):
        reg.register(cls())
    assert reg.get("speak").validate_params({"target": "神秘旅人", "content": "你好"}, ctx) is None
    assert reg.get("whisper").validate_params({"target": "神秘旅人", "content": "小声"}, ctx) is None


def test_modify_char_state_for_npc():
    """ModifyCharStateAction 能修改 NPC 状态"""
    world = _build_world()
    gm_ctx = world.build_validation_context("GM")
    assert ModifyCharStateAction().validate_params({"target": "神秘旅人", "key": "伤势", "value": "轻伤"}, gm_ctx) is None
    ModifyCharStateAction().execute("GM", {"target": "神秘旅人", "key": "伤势", "value": "轻伤"}, world)
    assert world.npcs["神秘旅人"].states["伤势"] == "轻伤"


def test_npc_speak_controls_npc():
    """NpcSpeakAction 对动态 NPC 生效（消息流 sender=NPC 名）"""
    world = _build_world()
    gm_ctx = world.build_validation_context("GM")
    assert NpcSpeakAction().validate_params({"npc_name": "神秘旅人", "content": "要喝酒吗"}, gm_ctx) is None
    msgs, _ = NpcSpeakAction().execute("GM", {"npc_name": "神秘旅人", "content": "要喝酒吗"}, world)
    assert msgs and msgs[0].sender == "神秘旅人"


@pytest.mark.parametrize("source,target", [
    ("dynamic", "大厅"),   # 动态 NPC：完整消息流
    ("static", "花园"),    # 静态 NPC：位置/索引
])
def test_npc_move_success(source, target):
    """NpcMoveAction 移动 NPC：位置更新 + 索引维护（动态含 action 消息流）"""
    if source == "dynamic":
        world = _build_world()
        npc_name = "神秘旅人"
    else:
        world = SCENE.init_world()
        world.rebuild_location_index()
        npc_name = "测试守卫"
    gm_ctx = world.build_validation_context("GM")
    assert NpcMoveAction().validate_params({"npc_name": npc_name, "target": target}, gm_ctx) is None
    msgs, result = NpcMoveAction().execute("GM", {"npc_name": npc_name, "target": target}, world)
    assert world.npcs[npc_name].location == target
    assert npc_name in world.get_characters_in_location(target)
    if source == "dynamic":
        assert npc_name not in world.get_characters_in_location("书房")
        assert msgs and msgs[0].sender == npc_name and msgs[0].msg_type == "action"
        assert target in result["result"]
        assert any(f"移动到了{target}" in e for e in world.event_log)


def test_npc_move_validation_rejects():
    """npc_move 校验拒绝：非 NPC / 非法位置 / 已在目标位置"""
    world = _build_world()
    gm_ctx = world.build_validation_context("GM")
    err = NpcMoveAction().validate_params({"npc_name": "艾莉娅", "target": "大厅"}, gm_ctx)
    assert err is not None and "不是 NPC" in err
    err = NpcMoveAction().validate_params({"npc_name": "神秘旅人", "target": "不存在的地方"}, gm_ctx)
    assert err is not None and "不是有效位置" in err
    err = NpcMoveAction().validate_params({"npc_name": "神秘旅人", "target": "书房"}, gm_ctx)
    assert err is not None and "已经在" in err


@pytest.mark.parametrize("source", ["dynamic", "static"])
def test_npc_remove_success(source):
    """npc_remove 移除 NPC：npcs/npc_names/索引三处清理（动态另验静默无消息）"""
    if source == "dynamic":
        world = _build_world()
        npc_name = "神秘旅人"
        gm_ctx = world.build_validation_context("GM")
        assert RemoveNpcAction().validate_params({"npc_name": npc_name}, gm_ctx) is None
        msgs, result = RemoveNpcAction().execute("GM", {"npc_name": npc_name}, world)
        assert msgs == [], "npc_remove 应静默，不发消息"
        assert "已移除" in result["result"]
        assert any(f"{npc_name} 离开了" in e for e in world.event_log)
    else:
        world = SCENE.init_world()
        world.rebuild_location_index()
        npc_name = "测试守卫"
        RemoveNpcAction().execute("GM", {"npc_name": npc_name}, world)
    assert npc_name not in world.npcs and npc_name not in world.npc_names
    assert npc_name not in world.get_characters_in_location("书房")


def test_npc_remove_then_rebuild():
    """npc_remove 后 npc_speak/npc_move 校验拒绝；移除后可同名重建"""
    world = _build_world()
    RemoveNpcAction().execute("GM", {"npc_name": "神秘旅人"}, world)
    ctx_after = world.build_validation_context("GM")
    assert NpcSpeakAction().validate_params({"npc_name": "神秘旅人", "content": "hi"}, ctx_after) is not None
    assert NpcMoveAction().validate_params({"npc_name": "神秘旅人", "target": "大厅"}, ctx_after) is not None
    err = RemoveNpcAction().validate_params({"npc_name": "神秘旅人"}, ctx_after)
    assert err is not None and "不是 NPC" in err
    _, add_result = AddNpcAction().execute("GM", {"npc_name": "神秘旅人", "location": "大厅"}, world)
    assert "已出现" in add_result["result"]
    assert "神秘旅人" in world.npcs


def test_static_npc_scene():
    """场景静态 NPC：测试守卫是 NPC 实体，is_npc 成立，speak 放行"""
    world = SCENE.init_world()
    world.rebuild_location_index()
    assert isinstance(world.npcs.get("测试守卫"), NPC)
    assert is_npc("测试守卫", world)
    assert not is_npc("测试甲", world)
    # 测试甲在大厅，可对书房的测试守卫说话（可见）
    world.agents["测试甲"] = Agent.from_config(
        SCENE, SCENE.agents[0], CONFIG, registry=AGENT_REGISTRY
    )
    world.agents["测试甲"].location = "大厅"
    ctx = world.build_validation_context("测试甲")
    assert SpeakAction().validate_params({"target": "测试守卫", "content": "查到什么了吗"}, ctx) is None


def test_gm_context_includes_dynamic_npc():
    """GM 世界上下文包含动态 NPC"""
    world = _build_world()
    gm_registry = ActionRegistry(include_agent_params=False)
    SCENE.setup_gm(gm_registry)
    gm = GMAgent.from_config(SCENE, CONFIG, gm_registry)
    ctx_text = gm._build_world_context(world)
    assert "神秘旅人" in ctx_text and "书房" in ctx_text, ctx_text


def test_npc_save_load_roundtrip():
    """save → load 往返：npcs 与 npc_names 完整恢复"""
    world = _build_world()
    world.tick = 2
    gm_registry = ActionRegistry(include_agent_params=False)
    SCENE.setup_gm(gm_registry)
    gm = GMAgent.from_config(SCENE, CONFIG, gm_registry)
    tmp = os.path.join(tempfile.mkdtemp(), "npc.json")
    save_simulation_state(world, gm, "_test", SCENE.name, tmp)
    world2, scene2, gm2, _ = load_simulation_state(tmp, CONFIG)
    assert "神秘旅人" in world2.npcs
    assert "神秘旅人" in world2.npc_names
    assert world2.npcs["神秘旅人"].location == "书房"
    assert world2.npcs["神秘旅人"].role == "旅人"
    assert world2.npcs["神秘旅人"].goal == "打听消息"
    assert "神秘旅人" in world2.get_characters_in_location("书房")
    assert "测试守卫" in world2.npcs and "测试守卫" in world2.npc_names
    assert "测试守卫" in world2.get_characters_in_location("书房")


def test_removed_npc_not_restored_after_load():
    """移除动态 NPC 后存档往返不回来；移除静态 NPC 后名字不残留幻影"""
    world = _build_world()
    world.tick = 3
    RemoveNpcAction().execute("GM", {"npc_name": "神秘旅人"}, world)
    RemoveNpcAction().execute("GM", {"npc_name": "测试守卫"}, world)
    gm_registry = ActionRegistry(include_agent_params=False)
    SCENE.setup_gm(gm_registry)
    gm = GMAgent.from_config(SCENE, CONFIG, gm_registry)
    tmp = os.path.join(tempfile.mkdtemp(), "npc-removed.json")
    save_simulation_state(world, gm, "_test", SCENE.name, tmp)
    world3, scene3, gm3, _ = load_simulation_state(tmp, CONFIG)
    assert "神秘旅人" not in world3.npcs and "神秘旅人" not in world3.npc_names
    assert "测试守卫" not in world3.npcs and "测试守卫" not in world3.npc_names
    assert world3.npc_names == set(world3.npcs.keys()), world3.npc_names


def test_gm_tool_whitelist():
    """GM 工具白名单：测试场景注册全部 7 个工具，基类默认只注册 narrate"""
    test_tools = _gm_tool_names(SCENE)
    assert {"narrate", "modify_environment", "modify_char_state", "npc_speak", "npc_add", "npc_move", "npc_remove"} <= test_tools

    base_reg = ActionRegistry(include_agent_params=False)
    Scene().setup_gm(base_reg)
    base_tools = {s["function"]["name"] for s in base_reg.get_tool_schemas()}
    assert base_tools == {"narrate"}, base_tools
