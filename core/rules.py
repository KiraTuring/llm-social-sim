"""规则引擎：监听事件，触发状态变化。"""

from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from core.agent import Agent
    from core.message import Message
    from core.world import WorldState


class RuleEngine:
    """规则引擎：根据事件触发 Agent 状态变化"""

    def __init__(self):
        self._handlers: dict[str, list[Callable]] = {}

    def on(self, event_type: str):
        """事件监听装饰器"""

        def decorator(handler: Callable):
            if event_type not in self._handlers:
                self._handlers[event_type] = []
            self._handlers[event_type].append(handler)
            return handler

        return decorator

    def trigger(self, event_type: str, message: "Message", world: "WorldState"):
        """触发事件"""
        if event_type not in self._handlers:
            return

        for handler in self._handlers[event_type]:
            try:
                handler(message, world)
            except Exception as e:
                print(f"[RuleEngine] 处理事件失败: {e}")

    def setup_default_rules(self):
        """设置默认规则"""

        @self.on("speech")
        def _on_speech(msg: "Message", world: "WorldState"):
            if not msg.recipients or msg.recipients == ["all"]:
                return

            target = msg.recipients[0]
            if target in world.agents:
                agent = world.agents[target]
                sender = msg.sender
                if sender in agent.relationships:
                    trust = agent.relationships[sender].get("trust", 0)

                    lower_words = ["笨", "蠢", "滚", "闭嘴"]
                    if any(word in msg.content for word in lower_words):
                        agent.relationships[sender]["trust"] = max(-5, trust - 2)
                        agent.mood = "愤怒"

                    praise_words = ["不错", "好", "谢谢", "佩服"]
                    if any(word in msg.content for word in praise_words):
                        agent.relationships[sender]["trust"] = min(5, trust + 1)

        @self.on("trade_offer")
        def _on_trade_offer(msg: "Message", world: "WorldState"):
            if msg.recipients and msg.recipients[0] in world.agents:
                target = world.agents[msg.recipients[0]]
                sender = msg.sender
                if sender in target.relationships:
                    target.relationships[sender]["trust"] = min(5, target.relationships[sender].get("trust", 0) + 1)

        @self.on("system_event")
        def _on_system_event(msg: "Message", world: "WorldState"):
            scary_words = ["危险", "杀", "威胁", "追杀"]
            if any(word in msg.content for word in scary_words):
                for agent in world.agents.values():
                    agent.mood = "紧张"