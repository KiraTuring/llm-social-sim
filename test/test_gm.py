"""GM 事件注入测试：计划事件确定性注入、随机事件按概率注入（离线，不调 LLM）。"""

from core.action import ActionRegistry
from core.gm import GMAgent
from core.world import WorldState
from core.message import MessageBus
from scenarios._test import _TestScene


def _make_gm_registry() -> ActionRegistry:
    """按场景基类默认白名单构建 GM registry（与 Scene.setup_gm 一致）。"""
    registry = ActionRegistry(include_agent_params=False)
    _TestScene().setup_gm(registry)
    return registry


def _build_world() -> WorldState:
    world = WorldState(tick=1, locations=["酒馆"])
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
