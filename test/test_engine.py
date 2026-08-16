"""SimulationEngine 测试：tick 级与 Agent 级步进、规则触发、GM 注入、顺序轮换。"""

import os
import tempfile

from conftest import write_plan
from core.action import ActionRegistry
from core.agent import Agent
from app.factory import create_agent, create_gm
from core.engine import SimulationEngine
from core.gm import GMAgent
from core.logger import SimLogger
from core.manual_agent import ManualAgent
from core.rules import RuleEngine
from core.scene import validate_agent_configs
from memory.memory import AgentMemory
from scenarios.tavern import TavernScene

SCENE = TavernScene()
REGISTRY = ActionRegistry()
SCENE.setup(REGISTRY)

CFG = {c["name"]: c for c in SCENE.agents}

CONFIG = {
    "agent": {
        "prompt_format": "text",
        "memory_short_limit": 10,
        "memory_compress_threshold": 30,
        "content_max_length": 200,
    },
    "gm": {
        "prompt_format": "text",
        "chat_history_max_messages": 40,
        "use_llm": False,
        "random_event_chance": 0.0,
        "llm_event_chance": 0.0,
        "event_tick_window": 3,
    },
    "simulation": {"rotate_order": False},
    "logging": {"level": "INFO"},
}


async def build_engine(plans: dict | None = None, rotate_order: bool = False) -> tuple:
    """构建完整场景 + 引擎（全部角色使用 ManualAgent，不需要 LLM）。"""
    validate_agent_configs(SCENE.agents)
    world = SCENE.init_world()
    for name, cfg in CFG.items():
        plan = (plans or {}).get(name, {})
        agent = create_agent(
            SCENE, cfg, CONFIG, registry=REGISTRY, manual_file=write_plan({name: plan})
        )
        world.agents[name] = agent
    world.action_order = [n for n in world.agents if n not in world.npc_names]

    gm_registry = ActionRegistry(include_agent_params=False)
    SCENE.setup_gm(gm_registry)
    gm = create_gm(SCENE, CONFIG, gm_registry)

    fd, log_path = tempfile.mkstemp(suffix=".log")
    os.close(fd)
    logger = SimLogger(log_file=log_path, level=20)

    rule_engine = RuleEngine()
    SCENE.setup_rules(rule_engine)

    config = dict(CONFIG)
    config["simulation"] = {"rotate_order": rotate_order}
    engine = SimulationEngine(world, gm, None, rule_engine, logger, config)
    return engine, world, logger


async def test_run_tick_full_execution_with_rules():
    """run_tick 完整执行：所有 Agent 行动、规则触发（酒馆辱骂降信任）"""
    engine, world, logger = await build_engine({
        "老巴克": {"1": {"action_type": "speak", "target": "艾莉娅", "content": "闭嘴，蠢货"}},
    })
    actions = await engine.run_tick(1)
    assert set(actions) == set(world.action_order), actions
    assert world.tick == 1
    assert actions["老巴克"].action_type == "speak", actions["老巴克"]
    # 酒馆规则：辱骂 → 艾莉娅对老巴克信任 -2，情绪愤怒
    trust = world.agents["艾莉娅"].relationships["老巴克"]["trust"]
    assert trust == -1, trust  # 初始 1 → -2 → -1
    assert world.agents["艾莉娅"].states["情绪"] == "愤怒", world.agents["艾莉娅"].states
    logger.close()


async def test_agent_level_steps_then_engine_reusable():
    """Agent 级步进：begin_tick → step_agent 逐个返回 → None → end_tick；引擎可复用"""
    engine, world, logger = await build_engine()
    await engine.begin_tick(2)
    assert engine.next_agent == world.action_order[0]
    steps = []
    while (step := await engine.step_agent()) is not None:
        steps.append(step.agent_name)
    assert steps == world.action_order, steps
    assert engine.pending_agents == []
    assert engine.next_agent is None
    await engine.end_tick()
    assert world.tick == 2
    # 引擎可复用：步进完还能继续跑完整 tick
    actions = await engine.run_tick(3)
    assert set(actions) == set(world.action_order)
    logger.close()


async def test_agent_action_recorded_to_event_log():
    """每个 Agent 行动统一写入 event_log：text 给 GM，meta 含 state_update 等完整信息。
    无计划的 Agent（雷恩/艾莉娅）返回 None，不产生事件。"""
    engine, world, logger = await build_engine({
        "老巴克": {
            "1": {
                "action_type": "speak",
                "target": "艾莉娅",
                "content": "欢迎光临",
                "internal_monologue": "招呼客人",
                "params": {"state_update": {"情绪": "平静"}},
            },
        },
    })
    await engine.run_tick(1)

    agent_events = [e for e in world.event_log_for_tick(1) if e.source_type == "agent"]
    assert len(agent_events) == 1, agent_events
    assert engine.agent_actions["老巴克"].action_type == "speak"
    assert engine.agent_actions["雷恩"] is None
    assert engine.agent_actions["艾莉娅"] is None

    speak_events = [e for e in agent_events if e.meta and e.meta["action_type"] == "speak"]
    assert len(speak_events) == 1
    event = speak_events[0]
    assert event.source == "老巴克"
    assert "老巴克 speak" in event.text
    assert "欢迎光临" in event.text
    assert "招呼客人" in event.text
    assert event.meta["state_update"] == {"情绪": "平静"}
    logger.close()


async def test_agent_without_plan_produces_no_action_or_event():
    """ManualAgent 无计划 → think 返回 None：不产生事件、agent_actions 记为 None。"""
    engine, world, logger = await build_engine()
    actions = await engine.run_tick(1)
    assert all(v is None for v in actions.values()), actions
    assert world.event_log_for_tick(1) == [], world.event_log_texts()
    logger.close()


async def test_gm_scheduled_event_injected():
    """GM 计划事件注入（tick 3 酒馆闷雷）"""
    engine, world, logger = await build_engine()
    await engine.begin_tick(3)
    assert any("闷雷" in e.text for e in world.event_log), world.event_log_texts()
    assert world.environment["壁炉旁"].get("火焰大小") == "旺盛"
    await engine.end_tick()
    logger.close()


async def test_rotate_order_rotates_action_order():
    """rotate_order 轮换行动顺序"""
    engine, world, logger = await build_engine(rotate_order=True)
    original = list(world.action_order)
    await engine.run_tick(4)
    assert world.action_order == original[1:] + original[:1], world.action_order
    logger.close()


async def test_step_without_begin_tick_raises():
    """begin_tick 未调用就 step_agent → RuntimeError"""
    engine, world, logger = await build_engine()
    try:
        await engine.step_agent()
        raise AssertionError("应当抛出 RuntimeError")
    except RuntimeError:
        pass
    logger.close()


async def test_move_keeps_location_index_consistent():
    """move 后位置索引一致（MoveAction 走 world.move_character 增量维护）"""
    engine, world, logger = await build_engine({
        "雷恩": {"1": {"action_type": "move", "target": "主厅"}},
    })
    await engine.run_tick(1)
    assert world.agents["雷恩"].location == "主厅", world.agents["雷恩"].location
    assert "雷恩" in world.get_agents_in_location("主厅"), world.get_agents_in_location("主厅")
    assert "雷恩" not in world.get_agents_in_location("角落"), world.get_agents_in_location("角落")
    logger.close()


def test_update_relationship_generalized():
    """update_relationship 通用关系属性操作：增量夹取、无界赋值、未知角色 no-op"""
    mem = AgentMemory(name="测试", short_limit=10, compress_threshold=30)
    rel_agent = Agent(
        name="测试", role="测试角色", personality="p", goal="g", location="主厅",
        relationships={"乙": {"trust": 0, "impression": ""}}, memory=mem,
        registry=ActionRegistry(),
    )
    rel_agent.update_relationship("乙", {"trust": -2})
    assert rel_agent.relationships["乙"]["trust"] == -2, rel_agent.relationships["乙"]
    rel_agent.update_relationship("乙", {"trust": -10})
    assert rel_agent.relationships["乙"]["trust"] == -5, rel_agent.relationships["乙"]
    rel_agent.update_relationship("乙", {"trust": 100})
    assert rel_agent.relationships["乙"]["trust"] == 5, rel_agent.relationships["乙"]
    rel_agent.update_relationship("乙", {"impression": "新印象"})
    assert rel_agent.relationships["乙"]["impression"] == "新印象"
    rel_agent.update_relationship("不存在", {"trust": 1})
    assert rel_agent.relationships["乙"]["trust"] == 5, rel_agent.relationships["乙"]
