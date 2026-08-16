"""GM 事件注入测试：计划事件确定性注入、随机事件按概率注入（离线，不调 LLM）。"""

from unittest.mock import AsyncMock

from core.action import ActionRegistry
from core.character import NPC
from core.gm import GMAgent
from core.world import WorldState, LocationGraph
from core.message import BROADCAST, Message, MessageBus
from scenarios._test import _TestScene


def _make_gm_registry() -> ActionRegistry:
    """按场景基类默认白名单构建 GM registry（与 Scene.setup_gm 一致）。"""
    registry = ActionRegistry(include_agent_params=False)
    _TestScene().setup_gm(registry)
    return registry


def _build_world() -> WorldState:
    world = WorldState(tick=1, geography=LocationGraph(locations=["酒馆"]))
    world.message_bus = MessageBus()
    return world


async def test_scheduled_events_injected_at_tick():
    """计划事件在指定 tick 确定性注入"""
    world = _build_world()
    gm = GMAgent(
        events=[(2, "一个穿黑甲的士兵推门进来"), (4, "外面传来马蹄声")],
        random_events=[],
        chance=0.0,
        gm_registry=_make_gm_registry(),
    )
    for tick in range(1, 6):
        world.tick = tick
        await gm.check_and_inject(world)
    texts_at_2 = world.event_log_for_tick(2)
    texts_at_4 = world.event_log_for_tick(4)
    assert any("穿黑甲的士兵" in e.text for e in texts_at_2), texts_at_2
    assert any("马蹄声" in e.text for e in texts_at_4), texts_at_4


def test_gm_context_reads_event_log_text_not_meta():
    """GM 上下文读 event_log 的 text，不读 meta。"""
    world = _build_world()
    world.add_event("旁白: 测试事件", source="GM", source_type="gm", meta={"secret": "不应出现"})
    gm = GMAgent(events=[], random_events=[], chance=0.0, gm_registry=_make_gm_registry())
    ctx = gm._build_world_context(world)
    assert "最近事件" in ctx
    assert "旁白: 测试事件" in ctx
    assert "不应出现" not in ctx
    assert "最近收到的消息" not in ctx


async def test_random_event_injected_with_chance_1():
    """random_chance=1.0 时每个 tick 都注入随机事件"""
    world = _build_world()
    gm = GMAgent(
        events=[],
        random_events=["醉汉开始唱歌", "壁炉火噼啪作响"],
        chance=1.0,
        gm_registry=_make_gm_registry(),
    )
    for tick in range(1, 4):
        world.tick = tick
        await gm.check_and_inject(world)
    log = "\n".join(world.event_log_texts())
    assert any(name in log for name in ("醉汉", "壁炉")), log


def _llm_trigger_gm() -> GMAgent:
    """use_llm 开启、随机概率为 0 的 GM，便于精确验证触发条件。"""
    return GMAgent(
        events=[], random_events=[], chance=0.0,
        use_llm=True, llm_chance=0.0,
        gm_registry=_make_gm_registry(),
    )


async def test_llm_triggered_by_trigger_gm_flag():
    """上一 tick 的消息带 trigger_gm=True（如 interact）触发 GM。"""
    world = _build_world()
    gm = _llm_trigger_gm()
    calls: list[int] = []
    gm._generate_llm_event = AsyncMock(side_effect=lambda w, c: calls.append(w.tick))
    world.message_bus.register_agent("测试甲")

    world.tick = 2
    world.message_bus.send(Message(
        sender="测试甲", recipients=[BROADCAST], content="开动引擎",
        tag="interact", tick=1, trigger_gm=True,
    ))
    await gm.check_and_inject(world, llm_client=object())
    assert calls == [2]


async def test_llm_triggered_by_npc_target_fallback():
    """target 是 NPC 的消息仍触发 GM（兜底路径，不依赖 flag）。"""
    world = _build_world()
    gm = _llm_trigger_gm()
    calls: list[int] = []
    gm._generate_llm_event = AsyncMock(side_effect=lambda w, c: calls.append(w.tick))
    world.message_bus.register_agent("测试甲")
    world.add_npc(NPC(name="警长", location="酒馆", role="治安官"))

    world.tick = 2
    world.message_bus.send(Message(
        sender="测试甲", recipients=["警长"], target="警长",
        content="请问谁来过这里", tag="speech", tick=1,
    ))
    await gm.check_and_inject(world, llm_client=object())
    assert calls == [2]


async def test_llm_not_triggered_by_plain_speech():
    """普通聊天（非 interact、target 非 NPC）不触发 GM。"""
    world = _build_world()
    gm = _llm_trigger_gm()
    calls: list[int] = []
    gm._generate_llm_event = AsyncMock(side_effect=lambda w, c: calls.append(w.tick))
    world.message_bus.register_agent("测试甲")
    world.message_bus.register_agent("测试乙")

    world.tick = 2
    world.message_bus.send(Message(
        sender="测试甲", recipients=["测试乙"], target="测试乙",
        content="今天天气不错", tag="speech", tick=1,
    ))
    await gm.check_and_inject(world, llm_client=object())
    assert calls == []
