#!/usr/bin/env python3
"""测试贸易机制：TradeAction 转移/校验/消息流/存档往返/私有性（离线）。"""

import os
import tempfile

from core.action import ActionRegistry
from actions.trade import TradeAction
from actions.common import ObserveAction
from core.agent import Agent
from core.character import NPC
from core.gm import GMAgent
from core.message import Message
from core.rules import RuleEngine
from core.save_load import save_simulation_state, load_simulation_state
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

ACTION = TradeAction()


def _build_world(colocated: bool = True):
    """两个 Agent（附金钱/物品状态），默认同在大厅。"""
    world = SCENE.init_world()
    world.agents["测试甲"] = Agent.from_config(
        SCENE, SCENE.agents[0], CONFIG, registry=AGENT_REGISTRY
    )
    world.agents["测试乙"] = Agent.from_config(
        SCENE, SCENE.agents[1], CONFIG, registry=AGENT_REGISTRY
    )
    world.agents["测试甲"].states.update({"金钱": 30, "物品": {"干粮": 2, "药草": 3}})
    world.agents["测试乙"].states.update({"金钱": 50, "物品": {"酒壶": 1, "干粮": 1}})
    if colocated:
        assert world.move_character("测试乙", "大厅") is None
    world.rebuild_location_index()
    return world


def _ctx(world, agent_name: str) -> dict:
    return world.build_validation_context(agent_name)


# ---------- 转移正确性 ----------

def test_money_transfer():
    """纯 give（支付/送礼）：金钱双向增减，对手方收 trade 私信，事件入 log"""
    world = _build_world()
    ctx = _ctx(world, "测试甲")
    params = {"target": "测试乙", "give_money": 10}
    assert ACTION.validate_params(params, ctx) is None
    msgs, result = ACTION.execute("测试甲", params, world)
    assert world.agents["测试甲"].states["金钱"] == 20
    assert world.agents["测试乙"].states["金钱"] == 60
    assert msgs and msgs[0].msg_type == "trade" and msgs[0].recipients == ["测试乙"]
    assert "金钱10" in result["summary"]
    # 纯 give（无 take）：对手方视角只有获得，没有付出
    assert "你获得金钱10" in msgs[0].content
    assert any("交易" in e for e in world.event_log)


def test_items_both_ways():
    """give 物品 + take 物品：双向转移，归零的键被删除；双方视角互为镜像"""
    world = _build_world()
    ctx = _ctx(world, "测试甲")
    params = {"target": "测试乙", "give_items": {"干粮": 1}, "take_items": {"酒壶": 1}}
    assert ACTION.validate_params(params, ctx) is None
    msgs, result = ACTION.execute("测试甲", params, world)
    assert world.agents["测试甲"].states["物品"] == {"干粮": 1, "药草": 3, "酒壶": 1}
    assert world.agents["测试乙"].states["物品"] == {"干粮": 2}
    # 行动者记忆：自己的视角（付出干粮，获得酒壶）
    assert "付出干粮×1" in result["summary"] and "获得酒壶×1" in result["summary"]
    # 对手方私信：镜像视角（你付出酒壶，获得干粮）
    trade_msg = [m for m in msgs if m.msg_type == "trade"][0]
    assert "你付出酒壶×1" in trade_msg.content and "获得干粮×1" in trade_msg.content


def test_trade_with_npc():
    """NPC 对手方：账目照常转移（为商贩 NPC 铺路）"""
    world = _build_world()
    npc = NPC(name="神秘商贩", location="大厅", role="商贩", goal="兜售货物")
    assert world.add_npc(npc) is None
    world.npcs["神秘商贩"].states.update({"金钱": 100, "物品": {"香料": 5}})
    ctx = _ctx(world, "测试甲")
    params = {"target": "神秘商贩", "give_money": 20, "take_items": {"香料": 2}}
    assert ACTION.validate_params(params, ctx) is None
    msgs, result = ACTION.execute("测试甲", params, world)
    assert world.agents["测试甲"].states["金钱"] == 10
    assert world.npcs["神秘商贩"].states["金钱"] == 120
    assert world.agents["测试甲"].states["物品"]["香料"] == 2
    assert world.npcs["神秘商贩"].states["物品"] == {"香料": 3}


# ---------- 校验 ----------

def test_validate_rejects_basic():
    """校验拒绝：缺目标/自交易/不存在/跨位置"""
    world = _build_world(colocated=False)  # 测试乙在花园
    ctx = _ctx(world, "测试甲")
    assert "交易对象" in ACTION.validate_params({"give_money": 1}, ctx)
    assert "自己" in ACTION.validate_params({"target": "测试甲", "give_money": 1}, ctx)
    err = ACTION.validate_params({"target": "张三", "give_money": 1}, ctx)
    assert err is not None and "不存在" in err
    err = ACTION.validate_params({"target": "测试乙", "give_money": 1}, ctx)
    assert err is not None and "不在你当前的位置" in err


def test_validate_rejects_trade_rules():
    """校验拒绝：空交易/纯取/负值/非整数/数量非法/超支/物品超库存"""
    world = _build_world()
    ctx = _ctx(world, "测试甲")
    err = ACTION.validate_params({"target": "测试乙"}, ctx)
    assert err is not None and "交易内容为空" in err
    err = ACTION.validate_params({"target": "测试乙", "take_money": 5}, ctx)
    assert err is not None and "有来有往" in err
    err = ACTION.validate_params({"target": "测试乙", "take_items": {"酒壶": 1}}, ctx)
    assert err is not None and "有来有往" in err
    err = ACTION.validate_params({"target": "测试乙", "give_money": -1}, ctx)
    assert err is not None and "不能为负" in err
    err = ACTION.validate_params({"target": "测试乙", "give_money": "五"}, ctx)
    assert err is not None and "必须是整数" in err
    err = ACTION.validate_params({"target": "测试乙", "give_money": 1, "give_items": {"干粮": 0}}, ctx)
    assert err is not None and "正整数" in err
    err = ACTION.validate_params({"target": "测试乙", "give_money": 31}, ctx)
    assert err is not None and "金钱不足" in err
    err = ACTION.validate_params({"target": "测试乙", "give_items": {"干粮": 3}}, ctx)
    assert err is not None and "物品不足" in err


def test_execute_rejects_insufficient_target():
    """take 侧对方库存不足：执行期错误 result，无消息、账目不变"""
    world = _build_world()
    params = {"target": "测试乙", "give_items": {"干粮": 1}, "take_money": 60}
    msgs, result = ACTION.execute("测试甲", params, world)
    assert msgs == []
    assert "没有足够的金钱" in result["summary"]
    assert world.agents["测试甲"].states["金钱"] == 30
    assert world.agents["测试乙"].states["金钱"] == 50

    params = {"target": "测试乙", "give_money": 5, "take_items": {"龙蛋": 1}}
    msgs, result = ACTION.execute("测试甲", params, world)
    assert msgs == []
    assert "没有 龙蛋" in result["summary"]
    assert world.agents["测试甲"].states["金钱"] == 30  # 首次失败未扣款


# ---------- 消息流与规则 ----------

def test_bystander_notice_hides_money():
    """对手方私信含金额明细且为对手方视角；旁观者通知只列物品，不含金额"""
    world = _build_world()
    params = {"target": "测试乙", "give_money": 10, "give_items": {"干粮": 1}, "take_items": {"酒壶": 1}}
    msgs, _ = ACTION.execute("测试甲", params, world)
    trade_msg = [m for m in msgs if m.msg_type == "trade"]
    notice = [m for m in msgs if m.msg_type == "action"]
    assert trade_msg and trade_msg[0].recipients == ["测试乙"]
    # 对手方视角（镜像）：付出的是被拿走的酒壶，获得的是对方给的金钱与干粮
    assert "你付出酒壶×1" in trade_msg[0].content
    assert "获得金钱10" in trade_msg[0].content and "获得干粮×1" in trade_msg[0].content
    # 不能出现行动者视角的「付出金钱」——防止对手方误读为自己付钱
    assert "付出金钱" not in trade_msg[0].content
    assert notice and notice[0].recipients
    assert "测试乙" in notice[0].content
    assert "干粮" in notice[0].content and "酒壶" in notice[0].content
    assert "金钱" not in notice[0].content


def test_tavern_trade_rule():
    """tavern 的 trade 规则：交易完成后对手方对发起方 trust +1"""
    from scenarios.tavern import TavernScene

    scene = TavernScene()
    engine = RuleEngine()
    scene.setup_rules(engine)
    world = SCENE.init_world()
    艾莉娅 = Agent.from_config(scene, scene.agents[2], CONFIG, registry=AGENT_REGISTRY)
    老巴克 = Agent.from_config(scene, scene.agents[0], CONFIG, registry=AGENT_REGISTRY)
    world.agents = {"艾莉娅": 艾莉娅, "老巴克": 老巴克}
    before = 老巴克.relationships["艾莉娅"]["trust"]
    msg = Message(sender="艾莉娅", recipients=["老巴克"], content="付出金钱5",
                  msg_type="trade", tick=1)
    engine.trigger("trade", msg, world)
    assert 老巴克.relationships["艾莉娅"]["trust"] == before + 1


def test_tavern_registers_trade():
    """tavern 场景注册了 trade 动作"""
    from scenarios.tavern import TavernScene

    reg = ActionRegistry()
    TavernScene().setup(reg)
    assert "trade" in reg.get_action_names()


# ---------- 校验上下文与存档 ----------

def test_validation_context_inventory():
    """校验上下文含行动者自己的经济视图（副本），GM 分支不含"""
    world = _build_world()
    ctx = _ctx(world, "测试甲")
    assert ctx["inventory"]["金钱"] == 30
    assert ctx["inventory"]["物品"] == {"干粮": 2, "药草": 3}
    ctx["inventory"]["金钱"] = 999
    assert world.agents["测试甲"].states["金钱"] == 30  # 副本，不影响真实状态
    gm_ctx = world.build_validation_context("GM")
    assert "inventory" not in gm_ctx


def test_observe_hides_private_economy():
    """observe 不含金钱/物品（private_states 隐藏）；未标记时可见"""
    world = _build_world()
    _, result = ObserveAction().execute("测试甲", {}, world)
    assert "金钱" in result["observed"]
    world.agents["测试乙"]._private_states = {"金钱", "物品"}
    _, result = ObserveAction().execute("测试甲", {}, world)
    assert "金钱" not in result["observed"]
    assert "物品" not in result["observed"]


def test_trade_save_load_roundtrip():
    """交易后 save → load：金钱/物品保留"""
    world = _build_world()
    world.tick = 2
    params = {"target": "测试乙", "give_money": 10, "take_items": {"酒壶": 1}}
    ACTION.execute("测试甲", params, world)
    gm_registry = ActionRegistry(include_agent_params=False)
    SCENE.setup_gm(gm_registry)
    gm = GMAgent.from_config(SCENE, CONFIG, gm_registry)
    tmp = os.path.join(tempfile.mkdtemp(), "trade.json")
    save_simulation_state(world, gm, "_test", SCENE.name, tmp)
    world2, _, _, _ = load_simulation_state(tmp, CONFIG)
    assert world2.agents["测试甲"].states["金钱"] == 20
    assert world2.agents["测试甲"].states["物品"] == {"干粮": 2, "药草": 3, "酒壶": 1}
    assert world2.agents["测试乙"].states["金钱"] == 60
    assert world2.agents["测试乙"].states["物品"] == {"干粮": 1}
