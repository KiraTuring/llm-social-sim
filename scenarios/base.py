"""场景基类：定义场景接口和通用逻辑。"""

from core.action import ActionRegistry
from core.rules import RuleEngine
from core.world import WorldState


class Scene:
    """场景基类"""

    name: str
    locations: list[str]
    agents: list[dict]
    gm_events: list[tuple[int, str]]
    gm_random_events: list[str]
    gm_llm_prompt: str = ""
    world_description: str = ""
    initial_environment: dict[str, dict[str, str]] = {}
    interactable_keys: dict[str, list[str]] = {}
    connections: list[tuple[str, str]] = []
    visibility: dict[str, list[str]] | None = None
    render_config: dict = {}
    instruction: str = ""
    states: dict = {}
    writable_states: list = []
    private_states: list = []

    def setup(self, registry: ActionRegistry) -> None:
        """注册场景特定的 actions"""
        pass

    def setup_rules(self, engine: RuleEngine) -> None:
        """注册场景特定的规则"""
        pass

    def init_world(self) -> WorldState:
        """初始化世界状态"""
        from core.message import MessageBus

        world = WorldState(locations=self.locations)
        world.message_bus = MessageBus()

        world.connections = self.connections
        world._adjacency = WorldState.compute_adjacency(self.connections)
        world.visibility = self.visibility or {}
        world.reverse_visibility = WorldState.compute_reverse_visibility(world.visibility)
        world.environment = {k: dict(v) for k, v in self.initial_environment.items()}
        world.interactable_keys = self.interactable_keys

        for agent_cfg in self.agents:
            world.message_bus.register_agent(agent_cfg["name"])

        return world

    def get_gm_config(self) -> dict:
        """获取 GM 配置"""
        return {
            "events": self.gm_events,
            "random_events": self.gm_random_events,
            "llm_prompt": self.gm_llm_prompt,
        }
