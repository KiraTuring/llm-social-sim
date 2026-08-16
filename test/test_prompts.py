"""Prompt 构建纯函数测试。"""

from core.action import ActionRegistry
from core.prompts import build_agent_system_prompt, build_gm_prompt, build_gm_world_context
from scenarios._test import _TestScene


def _make_agent_registry():
    registry = ActionRegistry()
    _TestScene().setup(registry)
    return registry


def _make_gm_registry():
    registry = ActionRegistry(include_agent_params=False)
    _TestScene().setup_gm(registry)
    return registry


def test_agent_prompt_contains_idle_capability_tool():
    scene = _TestScene()
    registry = _make_agent_registry()
    agent = _build_agent(scene, registry)
    prompt = build_agent_system_prompt(agent, registry)
    assert "think" in prompt
    assert "speak" in prompt


def test_gm_prompt_npc_control_branch():
    registry = _make_gm_registry()
    prompt = build_gm_prompt(registry, "自定义 GM prompt", "测试世界")
    assert "NPC 由你控制" in prompt
    assert "npc_speak" in prompt


def test_gm_world_context_ordered_by_locations():
    scene = _TestScene()
    world = scene.init_world()
    text = build_gm_world_context(world, 3)
    env_start = text.index("环境状态")
    assert text.index("大厅", env_start) < text.index("花园", env_start) < text.index("书房", env_start)


def _build_agent(scene, registry):
    from app.factory import create_agent

    config = {
        "agent": {
            "prompt_format": "text",
            "memory_short_limit": 10,
            "memory_compress_threshold": 30,
            "content_max_length": 200,
        }
    }
    return create_agent(scene, scene.agents[0], config, registry=registry)
