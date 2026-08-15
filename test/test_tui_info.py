"""TUI 信息格式化纯函数测试：工具列表、场景分节白名单、NPC 判断。"""

from core.action import ActionRegistry
from core.gm import GMAgent
from render.tui_info import format_agent_tools, format_scene_sections, is_npc
from scenarios._test import _TestScene


def _config() -> dict:
    return {
        "llm": {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "api_key": "SECRET_KEY_123",
            "base_url": "https://secret.example.com",
            "response_mode": "tool_call",
        },
        "simulation": {
            "max_ticks": 20,
            "mode": "auto",
            "auto_delay": 2,
            "rotate_order": False,
            "manual_agents": ["测试甲"],
        },
        "agent": {
            "prompt_format": "text",
            "memory_short_limit": 10,
            "memory_compress_threshold": 30,
            "content_max_length": 200,
            "inbox_limit": 5,
        },
        "gm": {
            "prompt_format": "chat",
            "chat_history_max_messages": 40,
            "use_llm": True,
            "random_event_chance": 0.2,
            "llm_event_chance": 0.2,
            "message_limit": 5,
        },
    }


def test_agent_tools_only_names_and_descriptions():
    """角色工具列表：只显示名称与描述，不含参数"""
    registry = ActionRegistry()
    _TestScene().setup(registry)
    tool_lines = format_agent_tools(registry)
    tool_text = "\n".join(tool_lines)
    assert "speak" in tool_text and "move" in tool_text
    assert "interact" in tool_text and "observe" in tool_text
    assert "internal_monologue" not in tool_text
    assert "target(string)" not in tool_text
    assert "参数" not in tool_text

def test_gm_tools_list():
    """GM 工具列表"""
    gm_registry = ActionRegistry(include_agent_params=False)
    _TestScene().setup_gm(gm_registry)
    gm_tool_text = "\n".join(format_agent_tools(gm_registry))
    assert "narrate" in gm_tool_text
    assert "modify_environment" in gm_tool_text
    assert "npc_speak" in gm_tool_text and "npc_add" in gm_tool_text
    assert "internal_monologue" not in gm_tool_text

def test_scene_sections_whitelist():
    """场景分节：白名单行为 + 剩余事件实时性 + 敏感信息不泄漏"""
    scene = _TestScene()
    config = _config()
    world = scene.init_world()
    world.tick = 6
    gm_registry = ActionRegistry(include_agent_params=False)
    scene.setup_gm(gm_registry)
    gm = GMAgent.from_config(scene, config, gm_registry)

    sections = format_scene_sections(scene, world, gm, config)
    titles = [t for t, _ in sections]
    assert "🌍 世界设定" in titles and "🎲 GM 配置" in titles
    assert "📅 事件" in titles and "🤖 Agent 参数" in titles
    assert "⚙️ LLM" in titles and "⏱ 模拟参数" in titles

    full_text = "\n".join(f"{t}\n{b}" for t, b in sections)
    assert "SECRET_KEY_123" not in full_text, "泄漏了 api_key"
    assert "secret.example.com" not in full_text, "泄漏了 base_url"
    assert "deepseek-chat" in full_text
    assert "tool_call" in full_text
    assert "测试甲" in full_text and "手动控制" in full_text
    assert "prompt 格式: chat" in full_text  # GM 段
    assert "narrate" in full_text and "modify_environment" in full_text  # GM 工具名
    assert "\nGM prompt:" in full_text  # GM prompt 前有换行
    assert "tick 6" in full_text and "tick 9" in full_text
    assert "tick 3" not in full_text, "已过 tick 的计划事件不应展示"
    assert "随机事件池" in full_text
    assert "世界设定" in full_text and "测试场景" in full_text

def test_npc_judgement():
    """NPC 判断：在/不在 world.npc_names"""
    test_world = _TestScene().init_world()
    assert is_npc("测试守卫", test_world)
    assert not is_npc("测试甲", test_world)
