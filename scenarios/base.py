"""场景基类：定义场景接口和通用逻辑。"""

from core.action import ActionRegistry
from core.world import WorldState


class Scene:
    """场景基类"""

    name: str
    locations: list[str]
    agents: list[dict]
    gm_events: list[tuple[int, str]]
    gm_random_events: list[str]

    def setup(self, registry: ActionRegistry) -> None:
        """注册场景特定的 actions"""
        pass

    def init_world(self) -> WorldState:
        """初始化世界状态"""
        from core.message import MessageBus

        world = WorldState(locations=self.locations)
        world.message_bus = MessageBus()

        for agent_cfg in self.agents:
            world.message_bus.register_agent(agent_cfg["name"])

        return world

    def get_gm_config(self) -> dict:
        """获取 GM 配置"""
        return {"events": self.gm_events, "random_events": self.gm_random_events}
