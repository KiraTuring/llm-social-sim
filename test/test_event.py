"""Phase 2 结构化事件测试：来源标注、辅助方法、v1 存档迁移。"""

from __future__ import annotations

from core.event import SOURCE_AGENT, SOURCE_GM, SOURCE_NPC, TimelineEvent
from core.save_load import SAVE_VERSION, _migrate
from core.world import WorldState


def test_event_log_structured_sources():
    """add_event 记录结构化事件，可按来源区分。"""
    world = WorldState(tick=1)
    world.add_event("旁白", source="GM", source_type=SOURCE_GM)
    world.add_event("NPC 说话", source="警长", source_type=SOURCE_NPC)
    world.add_event("交易", source="艾莉娅", source_type=SOURCE_AGENT)

    assert [e.source_type for e in world.event_log] == [SOURCE_GM, SOURCE_NPC, SOURCE_AGENT]
    assert world.event_log_texts() == ["旁白", "NPC 说话", "交易"]


def test_event_log_for_tick_filters():
    """event_log_for_tick 只返回指定 tick 的事件。"""
    world = WorldState(tick=1)
    world.add_event("tick1 事件")
    world.tick = 2
    world.add_event("tick2 事件")

    events = world.event_log_for_tick(2)
    assert len(events) == 1
    assert events[0].text == "tick2 事件"
    assert events[0].tick == 2


def test_timeline_event_roundtrip():
    """TimelineEvent to_dict/from_dict 保持字段。"""
    event = TimelineEvent(tick=3, text="交易完成", source="艾莉娅", source_type=SOURCE_AGENT, meta={"k": "v"})
    restored = TimelineEvent.from_dict(event.to_dict())
    assert restored == event


def test_event_log_for_last_ticks_window():
    """event_log_for_last_ticks 按 tick 窗口过滤事件。"""
    world = WorldState(tick=1)
    world.add_event("tick1 事件")
    world.tick = 2
    world.add_event("tick2 事件")
    world.tick = 3
    world.add_event("tick3 事件")

    assert [e.text for e in world.event_log_for_last_ticks(2)] == ["tick2 事件", "tick3 事件"]
    assert [e.text for e in world.event_log_for_last_ticks(1)] == ["tick3 事件"]
    assert [e.text for e in world.event_log_for_last_ticks(0)] == []


def test_save_load_v1_event_log_migration():
    """v1 字符串事件迁移为 v2 结构化事件，tick 被解析，来源默认 GM。"""
    old_save = {
        "version": 1,
        "event_log": [
            "[tick 3] 屋外传来马蹄声",
            "[tick 5] 壁炉暗了下来",
        ],
    }
    migrated = _migrate(old_save)
    assert migrated["version"] == SAVE_VERSION == 2
    assert migrated["event_log"][0] == {
        "tick": 3,
        "text": "屋外传来马蹄声",
        "source": "GM",
        "source_type": SOURCE_GM,
    }
    assert migrated["event_log"][1]["tick"] == 5


def test_save_load_v2_event_log_passthrough():
    """v2 dict 事件原样保留，不重复迁移。"""
    data = {
        "version": 2,
        "event_log": [
            {"tick": 2, "text": "已有结构化事件", "source": "警长", "source_type": SOURCE_NPC},
        ],
    }
    migrated = _migrate(data)
    assert migrated["event_log"] == data["event_log"]
