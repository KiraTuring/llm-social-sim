"""模拟引擎：tick 级与 Agent 级步进的公共执行核心。

CLI 与 TUI 共用同一个引擎，保证 GM 注入、规则触发、日志记录、
行动顺序等行为完全一致，避免两套主循环漂移。

完整 tick（CLI / 测试用）::

    engine = SimulationEngine(world, gm, llm, rule_engine, logger, config)
    actions = await engine.run_tick(tick)

Agent 级步进（TUI 用，每个角色行动完即可刷新 UI）::

    await engine.begin_tick(tick)
    while (step := await engine.step_agent()) is not None:
        ...  # 单个角色已完成 perceive → think → act
    await engine.end_tick()
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from core.action import format_result_values
from core.event import SOURCE_AGENT

if TYPE_CHECKING:
    from core.action import Action
    from core.gm import GMAgent
    from core.message import Message
    from core.rules import RuleEngine
    from core.world import WorldState


@dataclass
class AgentStep:
    """一次 Agent 步进的结果。"""

    agent_name: str
    action: "Action | None"
    messages: list["Message"]
    tick: int


def _format_action_event_text(agent_name: str, action: "Action") -> str:
    """把 Agent 行动格式化为 GM 可读的文本摘要。

    完整结构化信息（含 state_update）由调用方写入 event_log 的 meta，
    GM 只读 text。
    """
    parts = [f"{agent_name} {action.action_type}"]
    if action.target:
        parts.append(f"-> {action.target}")
    if action.content:
        parts.append(f": {action.content}")
    if action.result:
        result_text = format_result_values(action.result, 200)
        if result_text:
            parts.append(f"(结果: {result_text})")
    if action.internal_monologue:
        parts.append(f"(内心: {action.internal_monologue})")
    return " ".join(parts)


class SimulationEngine:
    """模拟引擎。

    不感知任何 UI：渲染、延时、等待按键都由调用方负责。
    """

    def __init__(
        self,
        world: "WorldState",
        gm: "GMAgent",
        llm: Any,
        rule_engine: "RuleEngine",
        logger: Any,
        config: dict,
    ):
        self.world = world
        self.gm = gm
        self.llm = llm
        self.rule_engine = rule_engine
        self.logger = logger
        self.config = config

        self.agent_actions: dict[str, "Action"] = {}
        self._current_tick: int | None = None
        self._next_agent_idx = 0
        # 统一重建位置索引：CLI/TUI/测试的 world 在此前已就绪
        self.world.rebuild_location_index()

    @property
    def pending_agents(self) -> list[str]:
        """当前 tick 尚未步进的 Agent 列表（TUI 可用来显示进度）。"""
        if self._current_tick is None:
            return []
        return self.world.action_order[self._next_agent_idx:]

    @property
    def next_agent(self) -> str | None:
        """下一个将要步进的 Agent 名，没有则返回 None。"""
        pending = self.pending_agents
        return pending[0] if pending else None

    async def begin_tick(self, tick: int) -> None:
        """开始一个 tick：设置 tick、GM 注入事件、重置步进游标。"""
        self.world.tick = tick
        self._current_tick = tick
        self._next_agent_idx = 0
        self.agent_actions = {}

        self.logger.log_tick_start(tick)
        await self.gm.check_and_inject(
            self.world, llm_client=self.llm if self.gm.use_llm else None
        )

    async def step_agent(self) -> AgentStep | None:
        """执行下一个 Agent 的 perceive → think → act。

        返回本次步进结果；所有 Agent 都执行完后返回 None。
        """
        if self._current_tick is None:
            raise RuntimeError("begin_tick() 未调用，无法 step_agent()")

        order = self.world.action_order
        if self._next_agent_idx >= len(order):
            return None

        agent_name = order[self._next_agent_idx]
        self._next_agent_idx += 1
        agent = self.world.agents[agent_name]
        tick = self._current_tick

        context = await agent.perceive(self.world, llm_client=self.llm)
        validation_context = self.world.build_validation_context(agent_name)
        action = await agent.think(self.llm, context, tick, validation_context)
        messages = await agent.act(action, self.world)

        self.agent_actions[agent_name] = action

        if action is not None:
            self.world.add_event(
                _format_action_event_text(agent_name, action),
                source=agent_name,
                source_type=SOURCE_AGENT,
                meta={
                    "action_type": action.action_type,
                    "target": action.target,
                    "content": action.content,
                    "result": action.result,
                    "internal_monologue": action.internal_monologue,
                    "state_update": action.state_update or action.params.get("state_update"),
                },
            )

        action_dict = {
            "action_type": action.action_type,
            "target": action.target,
            "content": action.content,
            "internal_monologue": action.internal_monologue,
            "result": action.result,
        } if action else {}
        self.logger.log_agent_action(agent_name, tick, action_dict)

        for msg in messages:
            self.logger.log_message({
                "sender": msg.sender,
                "recipients": msg.recipients,
                "target": msg.target,
                "content": msg.content,
                "msg_type": msg.msg_type,
                "tick": msg.tick,
            })
            self.rule_engine.trigger(msg.msg_type, msg, self.world)

        return AgentStep(
            agent_name=agent_name,
            action=action,
            messages=messages,
            tick=tick,
        )

    async def end_tick(self) -> None:
        """结束当前 tick：轮换行动顺序、记录结束日志。"""
        if self._current_tick is None:
            return
        if self.config["simulation"].get("rotate_order", False):
            self.world.rotate_order()
        self.logger.log_tick_end(self._current_tick)
        self._current_tick = None

    async def run_tick(self, tick: int) -> dict[str, "Action"]:
        """完整执行一个 tick，返回 {agent_name: action}。"""
        await self.begin_tick(tick)
        while await self.step_agent() is not None:
            pass
        await self.end_tick()
        return self.agent_actions
