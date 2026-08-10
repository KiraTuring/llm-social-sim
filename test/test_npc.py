#!/usr/bin/env python3
"""测试动态 NPC：Character 继承、AddNpcAction、接口兼容（离线 mock）。"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.action import ActionRegistry
from core.actions.common import SpeakAction, ObserveAction, WhisperAction
from core.actions.gm_npc import AddNpcAction, NpcSpeakAction
from core.actions.gm_tools import ModifyCharStateAction
from core.character import Character, NPC
from core.gm import GMAgent
from core.save_load import save_simulation_state, load_simulation_state
from scenarios.murder import MurderScene
from scenarios.tavern import TavernScene

SCENE = TavernScene()
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
        "use_llm": False,
        "random_event_chance": 0.0,
        "llm_event_chance": 0.0,
        "message_limit": 5,
    },
}


def _build_world():
    """构造含动态 NPC 的世界：在吧台添加一个神秘旅人。"""
    from core.agent import Agent

    world = SCENE.init_world()
    world.agents["老巴克"] = Agent.from_config(SCENE, SCENE.agents[0], CONFIG)
    world.agents["老巴克"].location = "主厅"
    world.rebuild_location_index()
    ok = world.add_npc(NPC(name="神秘旅人", location="吧台", role="旅人", goal="打听消息"))
    assert ok is None, ok
    return world


def run_tests():
    print("测试动态 NPC（Character 继承 + AddNpcAction）")
    print("=" * 50)

    # 0. Character 继承：Agent 与 NPC 都是 Character
    from core.agent import Agent

    assert issubclass(Agent, Character)
    assert issubclass(NPC, Character)
    print("[0] Agent/NPC 均为 Character 子类 OK")

    # 1. AddNpcAction 创建成功：npcs + npc_names + 位置索引
    world = SCENE.init_world()
    world.rebuild_location_index()
    action = AddNpcAction()
    spec_ctx = {"agent_names": ["老巴克"], "npc_names": [], "locations": world.locations}
    assert action.validate_params({"npc_name": "神秘旅人", "location": "吧台"}, spec_ctx) is None
    _, result = action.execute("GM", {"npc_name": "神秘旅人", "location": "吧台", "role": "旅人"}, world)
    assert "神秘旅人" in world.npcs
    assert "神秘旅人" in world.npc_names
    assert "神秘旅人" in world.get_characters_in_location("吧台")
    assert "神秘旅人" not in world.get_agents_in_location("吧台")
    assert "已出现在吧台" in result["result"]
    assert any("新 NPC 出现" in e for e in world.event_log)
    print("[1] AddNpcAction 创建成功 OK")

    # 2. 校验：重名拒绝 + 非法位置拒绝
    world = _build_world()
    spec_ctx = {"agent_names": ["老巴克"], "npc_names": ["神秘旅人"], "locations": world.locations}
    err = AddNpcAction().validate_params({"npc_name": "神秘旅人", "location": "吧台"}, spec_ctx)
    assert err is not None and "已存在" in err
    err = AddNpcAction().validate_params({"npc_name": "新来者", "location": "不存在的地方"}, spec_ctx)
    assert err is not None and "不是有效位置" in err
    # 与现有 agent 重名
    err = AddNpcAction().validate_params({"npc_name": "老巴克", "location": "吧台"}, spec_ctx)
    assert err is not None and "已存在" in err
    print("[2] 重名/非法位置校验 OK")

    # 3. get_hearable_agents 对 NPC 不崩溃且返回可见 agent
    world = _build_world()
    hearable = world.get_hearable_agents("神秘旅人")
    assert "老巴克" in hearable  # 吧台可见主厅
    assert "神秘旅人" not in hearable  # 自己排除
    assert isinstance(hearable, list)
    print("[3] get_hearable_agents(npc) OK")

    # 4. ObserveAction 能看到动态 NPC（role/state）
    world = _build_world()
    from core.agent import Agent

    agent = Agent.from_config(SCENE, SCENE.agents[0], CONFIG)
    world.agents[agent.name] = agent
    # 把 agent 挪到吧台与 NPC 同处，便于 observe
    from core.actions.common import MoveAction

    MoveAction().execute(agent.name, {"target": "吧台"}, world)
    _, result = ObserveAction().execute(agent.name, {}, world)
    obs = result["observed"]
    assert "神秘旅人" in obs and "旅人" in obs, obs
    print("[4] ObserveAction 看到动态 NPC OK")

    # 5. SpeakAction / WhisperAction 对动态 NPC 校验通过
    world = _build_world()
    agent = Agent.from_config(SCENE, SCENE.agents[0], CONFIG)
    agent.location = "吧台"
    world.agents[agent.name] = agent
    ctx = world.build_validation_context(agent.name)
    reg = ActionRegistry()
    for cls in (SpeakAction, WhisperAction, ObserveAction):
        reg.register(cls())
    assert reg.get("speak").validate_params({"target": "神秘旅人", "content": "你好"}, ctx) is None
    assert reg.get("whisper").validate_params({"target": "神秘旅人", "content": "小声"}, ctx) is None
    print("[5] speak/whisper 对动态 NPC 校验通过 OK")

    # 6. ModifyCharStateAction 能修改 NPC 状态
    world = _build_world()
    gm_ctx = world.build_validation_context("GM")
    assert ModifyCharStateAction().validate_params({"target": "神秘旅人", "key": "伤势", "value": "轻伤"}, gm_ctx) is None
    ModifyCharStateAction().execute("GM", {"target": "神秘旅人", "key": "伤势", "value": "轻伤"}, world)
    assert world.npcs["神秘旅人"].states["伤势"] == "轻伤"
    print("[6] modify_char_state 修改 NPC 状态 OK")

    # 7. NpcSpeakAction 对动态 NPC 生效（消息流 sender=NPC 名）
    world = _build_world()
    gm_ctx = world.build_validation_context("GM")
    assert NpcSpeakAction().validate_params({"npc_name": "神秘旅人", "content": "要喝酒吗"}, gm_ctx) is None
    msgs, _ = NpcSpeakAction().execute("GM", {"npc_name": "神秘旅人", "content": "要喝酒吗"}, world)
    assert msgs and msgs[0].sender == "神秘旅人"
    print("[7] npc_speak 控制动态 NPC 说话 OK")

    # 8. murder 场景：警长是 NPC 实体，is_npc 成立，speak 放行
    from render.tui_info import is_npc

    murder = MurderScene()
    mw = murder.init_world()
    mw.rebuild_location_index()
    assert isinstance(mw.npcs.get("警长"), NPC)
    assert is_npc("警长", mw)
    assert not is_npc("艾莉娅", mw)
    # 艾莉娅在庄园大厅，可对警卫室的警长说话（可见）
    mw.agents["艾莉娅"] = Agent.from_config(murder, murder.agents[0], CONFIG)
    mw.agents["艾莉娅"].location = "庄园大厅"
    ctx = mw.build_validation_context("艾莉娅")
    assert SpeakAction().validate_params({"target": "警长", "content": "找到线索了吗"}, ctx) is None
    print("[8] murder 静态 NPC 迁移 OK")

    # 9. GM 世界上下文包含动态 NPC
    world = _build_world()
    gm = GMAgent.from_config(SCENE, CONFIG)
    ctx_text = gm._build_world_context(world)
    assert "神秘旅人" in ctx_text and "吧台" in ctx_text, ctx_text
    print("[9] GM 世界上下文含 NPC OK")
    # 10. save → load 往返：npcs 与 npc_names 完整恢复
    world = _build_world()
    world.tick = 2
    gm_registry = ActionRegistry(include_agent_params=False)
    SCENE.setup_gm(gm_registry)
    gm = GMAgent.from_config(SCENE, CONFIG, gm_registry)
    tmp = os.path.join(tempfile.mkdtemp(), "npc.json")
    save_simulation_state(world, gm, "tavern", SCENE.name, tmp)
    world2, scene2, gm2 = load_simulation_state(tmp, CONFIG)
    assert "神秘旅人" in world2.npcs
    assert "神秘旅人" in world2.npc_names
    assert world2.npcs["神秘旅人"].location == "吧台"
    assert world2.npcs["神秘旅人"].role == "旅人"
    assert world2.npcs["神秘旅人"].goal == "打听消息"
    assert "神秘旅人" in world2.get_characters_in_location("吧台")
    print("[10] NPC 存档往返 OK")

    # 11. GM 工具白名单：各场景 setup_gm 全量声明，未注册的工具不可用
    def _gm_tool_names(scene):
        reg = ActionRegistry(include_agent_params=False)
        scene.setup_gm(reg)
        return {s["function"]["name"] for s in reg.get_tool_schemas()}

    tavern_tools = _gm_tool_names(TavernScene())
    assert {"narrate", "modify_environment", "modify_char_state"} <= tavern_tools
    assert "npc_speak" not in tavern_tools and "add_npc" not in tavern_tools

    murder_tools = _gm_tool_names(MurderScene())
    assert {"narrate", "npc_speak", "modify_char_state"} <= murder_tools
    assert "add_npc" not in murder_tools and "modify_environment" not in murder_tools

    # 基类默认只注册 narrate
    from scenarios.base import Scene

    base_reg = ActionRegistry(include_agent_params=False)
    Scene().setup_gm(base_reg)
    base_tools = {s["function"]["name"] for s in base_reg.get_tool_schemas()}
    assert base_tools == {"narrate"}, base_tools
    print("[11] GM 工具全量白名单 OK")

    print("=" * 50)
    print("全部动态 NPC 测试通过")


run_tests()
