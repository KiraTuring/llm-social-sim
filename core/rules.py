"""规则引擎：监听事件，触发状态变化。"""

from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from core.message import Message
    from core.world import WorldState


class RuleEngine:
    """规则引擎：根据事件触发 Agent 状态变化"""

    def __init__(self, logger=None):
        self._handlers: dict[str, list[Callable]] = {}
        self.logger = logger

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
                if self.logger:
                    self.logger.error(f"[RuleEngine] 处理事件失败: {e}")

