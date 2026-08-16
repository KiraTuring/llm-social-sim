"""MessageBus Phase 1 边界测试：有界存储、投递过滤、set_limits、Agent 消费全量。"""

from __future__ import annotations

import asyncio

from core.action import ActionRegistry
from core.agent import Agent
from core.message import BROADCAST, Message, MessageBus
from core.world import WorldState, LocationGraph
from memory.memory import AgentMemory


def _msg(sender: str, content: str, recipients: list[str] | None = None, tick: int = 1) -> Message:
    return Message(
        sender=sender,
        recipients=recipients if recipients is not None else [BROADCAST],
        content=content,
        tag="speech",
        tick=tick,
    )


def test_send_direct_to_unregistered_does_not_create_inbox():
    """单播只投递给已注册 Agent，避免为 NPC/未知角色创建永远不清理的 inbox。"""
    bus = MessageBus()
    bus.register_agent("甲")

    bus.send(_msg("甲", "你好", recipients=["乙"]))
    bus.send(_msg("甲", "给 NPC", recipients=["警长"]))

    assert "乙" not in bus._inboxes
    assert "警长" not in bus._inboxes
    assert bus.get_inbox("甲") == []


def test_broadcast_still_delivers_to_registered_agents():
    """广播仍只投递给已注册 Agent，并排除发送者自己。"""
    bus = MessageBus()
    bus.register_agent("甲")
    bus.register_agent("乙")

    bus.send(_msg("甲", "全体注意"))

    assert len(bus.get_inbox("乙")) == 1
    assert bus.get_inbox("甲") == []


def test_max_inbox_per_agent_trims_on_send():
    """单个收件箱只保留最近 max_inbox_per_agent 条。"""
    bus = MessageBus(max_inbox_per_agent=3)
    bus.register_agent("甲")

    for i in range(1, 8):
        bus.send(_msg("乙", f"msg{i}", recipients=["甲"]))

    inbox = bus.get_inbox("甲")
    assert len(inbox) == 3
    assert [m.content for m in inbox] == ["msg5", "msg6", "msg7"]


def test_max_messages_trims_global_history():
    """全局消息流只保留最近 max_messages 条。"""
    bus = MessageBus(max_messages=5)
    bus.register_agent("甲")

    for i in range(1, 12):
        bus.send(_msg("乙", f"msg{i}", recipients=["甲"]))

    all_messages = bus.get_all()
    assert len(all_messages) == 5
    assert all_messages[-1].content == "msg11"


def test_set_limits_trims_existing_inbox():
    """运行时 set_limits 立即裁剪已存在的 inbox。"""
    bus = MessageBus(max_inbox_per_agent=10)
    bus.register_agent("甲")

    for i in range(1, 11):
        bus.send(_msg("乙", f"msg{i}", recipients=["甲"]))

    assert len(bus.get_inbox("甲")) == 10

    bus.set_limits(max_inbox_per_agent=3)
    inbox = bus.get_inbox("甲")
    assert len(inbox) == 3
    assert [m.content for m in inbox] == ["msg8", "msg9", "msg10"]


def test_agent_ingests_all_inbox_messages():
    """MessageBus 已裁剪到上限后，perceive 消费 inbox 中的全部消息。"""
    bus = MessageBus(max_inbox_per_agent=3)
    bus.register_agent("测试")

    world = WorldState(tick=1, geography=LocationGraph(locations=["主厅"]))
    world.message_bus = bus
    world.agents["测试"] = Agent(
        name="测试",
        role="测试角色",
        personality="随和",
        goal="测试",
        location="主厅",
        relationships={},
        memory=AgentMemory("测试", short_limit=10, compress_threshold=30),
        registry=ActionRegistry(),
        prompt_format="text",
    )
    world.rebuild_location_index()

    for i in range(1, 6):
        bus.send(_msg("张三", f"你好{i}", recipients=["测试"], tick=1))

    context = asyncio.run(world.agents["测试"].perceive(world))

    assert bus.get_inbox("测试") == []
    assert len(world.agents["测试"].memory._short_term) == 3
    assert len(world.agents["测试"]._perceived_inbox) == 3
    assert context.count("你好3") == 1


def test_trigger_gm_default_false_and_roundtrip():
    """trigger_gm 默认 False；to_dict/from_dict 往返一致。"""
    assert _msg("甲", "你好").trigger_gm is False

    flagged = Message(
        sender="甲", recipients=["乙"], content="开动引擎",
        tag="interact", tick=1, trigger_gm=True,
    )
    data = flagged.to_dict()
    assert data["trigger_gm"] is True
    assert Message.from_dict(data).trigger_gm is True

    # 未置位时序列化也是 False，往返不变
    plain = Message.from_dict(_msg("甲", "你好").to_dict())
    assert plain.trigger_gm is False


def test_from_dict_tolerates_missing_trigger_gm():
    """旧存档消息 dict 缺 trigger_gm 键时回退 False，向后兼容。"""
    old = {
        "sender": "甲", "recipients": ["乙"], "content": "hi",
        "tag": "speech", "tick": 1, "target": None,
    }
    restored = Message.from_dict(old)
    assert restored.trigger_gm is False

    bus = MessageBus.from_dict({
        "known_agents": ["甲"],
        "messages": [old],
        "inboxes": {"甲": [old]},
    })
    assert bus.get_all()[0].trigger_gm is False
    assert bus.get_inbox("甲")[0].trigger_gm is False
