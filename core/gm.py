"""GM Agent：注入事件，推进剧情。"""

import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.world import WorldState


class GMAgent:
    """GM Agent：注入事件，推进剧情"""

    def __init__(self, events: list, random_events: list, chance: float, use_llm: bool = False):
        self.scheduled_events = events
        self.random_events = random_events
        self.random_chance = chance
        self.use_llm = use_llm

    async def check_and_inject(self, world: "WorldState", llm_client=None):
        """每个 tick 检查是否需要注入事件"""

        scheduled = self._check_scheduled(world)
        random_ev = self._check_random()

        llm_ev = None
        # TODO: LLM 动态事件生成（当 use_llm=True 且本 tick 没有计划事件时）
        # if self.use_llm and llm_client and not scheduled:
        #     llm_ev = await self._generate_llm_event(world, llm_client)

        events = [e for e in [scheduled, random_ev, llm_ev] if e]

        for event in events:
            world.add_event(event)
            self._broadcast_event(event, world)

    def _check_scheduled(self, world: "WorldState") -> str | None:
        """检查计划事件"""
        for tick, event in self.scheduled_events[:]:
            if tick == world.tick:
                self.scheduled_events.remove((tick, event))
                return event
        return None

    def _check_random(self) -> str | None:
        """检查随机事件"""
        if self.random_events and random.random() < self.random_chance:
            return random.choice(self.random_events)
        return None

    def _broadcast_event(self, event: str, world: "WorldState"):
        """广播事件给所有 Agent"""
        from core.message import Message, BROADCAST

        msg = Message(sender="GM", recipients=[BROADCAST], content=event, msg_type="system_event", tick=world.tick)
        world.message_bus.send(msg)

    # TODO: 实现 LLM 动态事件生成
    # async def _generate_llm_event(self, world: "WorldState", llm_client) -> str | None:
    #     """根据当前世界状态，让 LLM 生成一个能推动剧情的事件"""
    #     context = self._build_world_context(world)
    #     messages = [{"role": "user", "content": context}]
    #     response, _ = await llm_client.call(
    #         system_prompt="你是 GM，负责生成推动剧情的事件...",
    #         messages=messages,
    #         action_registry=None,
    #     )
    #     return response
    #
    # def _build_world_context(self, world: "WorldState") -> str:
    #     """构建世界状态上下文"""
    #     pass