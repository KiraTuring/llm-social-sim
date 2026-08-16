"""消息系统和消息路由。"""

from __future__ import annotations

from dataclasses import dataclass


BROADCAST = "all"
"""发送给 \"all\" 表示广播给所有已知 Agent"""


@dataclass
class Message:
    """单条消息

    trigger_gm: 发送方显式声明本条消息是否需要 GM 关注（与环境互动、对 NPC 说话等）。
    GM 触发判断以此字段为准，不依赖消息类型字符串。
    """

    sender: str
    recipients: list[str]
    content: str
    msg_type: str
    tick: int
    target: str | None = None
    trigger_gm: bool = False

    def to_dict(self) -> dict:
        """序列化为可保存的 dict"""
        return {
            "sender": self.sender,
            "recipients": self.recipients,
            "content": self.content,
            "msg_type": self.msg_type,
            "tick": self.tick,
            "target": self.target,
            "trigger_gm": self.trigger_gm,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        """从 dict 恢复；旧存档缺 trigger_gm 字段时回退为 False（向后兼容）。"""
        return cls(**{**data, "trigger_gm": data.get("trigger_gm", False)})


class MessageBus:
    """消息总线，负责消息分发和存储。

    有界存储：全局消息数和单个 Agent 收件箱都在 send 阶段裁剪到上限；
    超出上限后直接丢弃，不做持久化归档。
    """

    def __init__(self, max_messages: int = 100, max_inbox_per_agent: int = 10):
        self.max_messages = max_messages
        self.max_inbox_per_agent = max_inbox_per_agent
        self._messages: list[Message] = []
        self._inboxes: dict[str, list[Message]] = {}
        self._known_agents: set[str] = set()

    def set_limits(self, max_messages: int | None = None,
                   max_inbox_per_agent: int | None = None) -> None:
        """运行时更新上限（run.py 从配置注入，便于 load 后覆盖默认值）。"""
        if max_messages is not None:
            self.max_messages = max_messages
        if max_inbox_per_agent is not None:
            self.max_inbox_per_agent = max_inbox_per_agent
            # 已存在的 inbox 立即按新上限裁剪
            for name in list(self._inboxes):
                if max_inbox_per_agent <= 0:
                    self._inboxes[name] = []
                else:
                    self._inboxes[name] = self._inboxes[name][-max_inbox_per_agent:]
        self._trim_messages()

    def to_dict(self) -> dict:
        """序列化为可保存的 dict（替代直接访问私有属性）"""
        return {
            # 排序保证存档输出确定性（set 迭代顺序受哈希随机化影响）
            "known_agents": sorted(self._known_agents),
            "messages": [m.to_dict() for m in self._messages],
            "inboxes": {
                name: [m.to_dict() for m in msgs]
                for name, msgs in self._inboxes.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MessageBus":
        """从 dict 恢复 MessageBus"""
        bus = cls()
        bus._known_agents = set(data["known_agents"])
        bus._messages = [Message.from_dict(m) for m in data["messages"]]
        bus._inboxes = {
            name: [Message.from_dict(m) for m in msgs]
            for name, msgs in data["inboxes"].items()
        }
        return bus

    def register_agent(self, agent_name: str) -> None:
        """注册一个 Agent（初始化 inbox）"""
        if agent_name not in self._inboxes:
            self._inboxes[agent_name] = []
        self._known_agents.add(agent_name)

    def send(self, msg: Message) -> None:
        """发送消息。

        只投递给已注册的 Agent，避免为 NPC/未知角色创建永远不清理的 inbox。
        全局消息流和单个 inbox 都在这里裁剪到配置上限。
        """
        self._messages.append(msg)
        self._trim_messages()

        if BROADCAST in msg.recipients:
            recipients = [n for n in self._known_agents if n != msg.sender]
        else:
            recipients = [r for r in msg.recipients if r in self._known_agents]

        for recipient in recipients:
            inbox = self._inboxes.setdefault(recipient, [])
            inbox.append(msg)
            if len(inbox) > self.max_inbox_per_agent:
                del inbox[: len(inbox) - self.max_inbox_per_agent]

    def _trim_messages(self) -> None:
        """全局消息流只保留最近 max_messages 条。"""
        if len(self._messages) > self.max_messages:
            del self._messages[: len(self._messages) - self.max_messages]

    def get_inbox(self, agent_name: str) -> list[Message]:
        """获取某人的收件箱"""
        return self._inboxes.get(agent_name, []).copy()

    def clear_inbox(self, agent_name: str) -> None:
        """清空某人的收件箱"""
        self._inboxes[agent_name] = []

    def get_all(self) -> list[Message]:
        """获取所有消息（只读）"""
        return self._messages.copy()

    def get_recent(self, limit: int = 10) -> list[Message]:
        """获取最近的消息"""
        return self._messages[-limit:]
