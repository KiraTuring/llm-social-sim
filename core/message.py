"""消息系统和消息路由。"""

from dataclasses import dataclass


BROADCAST = "all"
"""发送给 \"all\" 表示广播给所有已知 Agent"""


@dataclass
class Message:
    """单条消息"""

    sender: str
    recipients: list[str]
    content: str
    msg_type: str
    tick: int
    target: str | None = None


class MessageBus:
    """消息总线，负责消息分发和存储"""

    def __init__(self):
        self._messages: list[Message] = []
        self._inboxes: dict[str, list[Message]] = {}
        self._known_agents: set[str] = set()

    def to_dict(self) -> dict:
        """序列化为可保存的 dict（替代直接访问私有属性）"""
        return {
            "known_agents": list(self._known_agents),
            "messages": [
                {"sender": m.sender, "recipients": m.recipients, "content": m.content,
                 "msg_type": m.msg_type, "tick": m.tick, "target": m.target}
                for m in self._messages
            ],
            "inboxes": {
                name: [
                    {"sender": m.sender, "recipients": m.recipients, "content": m.content,
                     "msg_type": m.msg_type, "tick": m.tick, "target": m.target}
                    for m in msgs
                ]
                for name, msgs in self._inboxes.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MessageBus":
        """从 dict 恢复 MessageBus"""
        bus = cls()
        bus._known_agents = set(data["known_agents"])
        bus._messages = [Message(**m) for m in data["messages"]]
        bus._inboxes = {
            name: [Message(**m) for m in msgs]
            for name, msgs in data["inboxes"].items()
        }
        return bus

    def register_agent(self, agent_name: str) -> None:
        """注册一个 Agent（初始化 inbox）"""
        if agent_name not in self._inboxes:
            self._inboxes[agent_name] = []
        self._known_agents.add(agent_name)

    def send(self, msg: Message) -> None:
        """发送消息"""
        self._messages.append(msg)

        if BROADCAST in msg.recipients:
            for agent_name in self._known_agents:
                if agent_name == msg.sender:
                    continue
                if agent_name not in self._inboxes:
                    self._inboxes[agent_name] = []
                self._inboxes[agent_name].append(msg)
        else:
            for recipient in msg.recipients:
                if recipient not in self._inboxes:
                    self._inboxes[recipient] = []
                self._inboxes[recipient].append(msg)

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