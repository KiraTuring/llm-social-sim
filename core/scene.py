"""核心场景框架：定义场景接口和通用逻辑。"""

from __future__ import annotations

import copy

from core.action import ActionRegistry
from core.rules import RuleEngine
from core.world import WorldState


class Scene:
    """场景基类"""

    name: str
    locations: list[str]
    agents: list[dict]
    gm_events: list[tuple[int, str]] = []
    gm_random_events: list[str] = []
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
    npc_names: list[str] = []
    npcs: list[dict] = []

    def setup(self, registry: ActionRegistry) -> None:
        """注册场景特定的 actions"""
        pass

    def setup_gm(self, registry: ActionRegistry) -> None:
        """注册 GM 可用工具。基类默认只注册 narrate（最基础的 GM 能力）。

        场景需要更多工具时覆盖本方法做全量白名单注册（与 setup() 的
        Agent 工具格式一致）——要什么就注册什么，不要则不注册：
        ```
        def setup_gm(self, registry):
            from actions.gm_tools import NarrateAction, ModifyEnvironmentAction, ModifyCharStateAction
            from actions.gm_npc import NpcSpeakAction, AddNpcAction
            for action_cls in [NarrateAction, ModifyEnvironmentAction, ModifyCharStateAction, NpcSpeakAction, AddNpcAction]:
                registry.register(action_cls())
        ```
        """
        from actions.gm_tools import NarrateAction

        registry.register(NarrateAction())

    def setup_rules(self, engine: RuleEngine) -> None:
        """注册场景特定的规则"""
        pass

    def init_world(self) -> WorldState:
        """初始化世界状态"""
        from core.message import MessageBus
        from core.character import NPC

        world = WorldState()
        world.message_bus = MessageBus()
        world.apply_scene_config(self)

        for agent_cfg in self.agents:
            world.message_bus.register_agent(agent_cfg["name"])

        for npc_cfg in self.npcs:
            world.add_npc(NPC(
                name=npc_cfg["name"],
                location=npc_cfg["location"],
                role=npc_cfg.get("role", ""),
                personality=npc_cfg.get("personality", ""),
                goal=npc_cfg.get("goal", ""),
                states=npc_cfg.get("states"),
            ))

        return world

    def get_gm_config(self) -> dict:
        """获取 GM 配置"""
        # 深拷贝：gm_events 是类属性，GM 触发时会 remove 已触发事件，
        # 不拷贝会让同一进程里的多个引擎/场景互相干扰（如测试、热加载）。
        return {
            "events": copy.deepcopy(self.gm_events),
            "random_events": list(self.gm_random_events),
            "llm_prompt": self.gm_llm_prompt,
        }
