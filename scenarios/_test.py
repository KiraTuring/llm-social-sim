"""测试专用场景：与生产场景解耦，供通用机制测试复用。

命名用下划线前缀（`_test` → `_TestScene`）：
- `list_available_scenes()` 的 glob `[!_]*.py` 不会收录它，`--list-scenes` 不出现
- 但 `scenarios/utils.py::load_scene` 仍能通过模块路径解析，存档往返测试可用
"""

from scenarios.base import Scene


class _TestScene(Scene):
    name = "测试场景"
    locations = ["大厅", "花园", "书房"]

    connections = [
        ("大厅", "花园"),
        ("大厅", "书房"),
        ("花园", "书房"),
    ]

    visibility = {
        "大厅": ["花园", "书房"],
        "花园": ["大厅", "书房"],
        "书房": ["大厅", "花园"],
    }

    states = {"情绪": "平静", "精力": 100}
    writable_states = ["情绪"]

    agents = [
        {
            "name": "测试甲",
            "role": "旅人",
            "personality": "谨慎好奇",
            "goal": "打探消息",
            "location": "大厅",
            "relationships": {
                "测试乙": {"trust": 0, "impression": "同路的旅伴"},
            },
        },
        {
            "name": "测试乙",
            "role": "商贩",
            "personality": "健谈圆滑",
            "goal": "兜售货物",
            "location": "花园",
            "relationships": {
                "测试甲": {"trust": 0, "impression": "路过的旅人"},
            },
        },
    ]

    npc_names = ["测试守卫"]
    npcs = [
        {
            "name": "测试守卫",
            "location": "书房",
            "role": "守卫",
            "personality": "沉默寡言",
            "goal": "看守书房",
        },
    ]

    world_description = (
        '"测试场景"是一座用于测试机制的庄园，布局规整。\n'
        "  大厅 → 宽敞的会客厅，灯火通明\n"
        "  花园 → 种满花草的院子，微风拂过\n"
        "  书房 → 藏书的房间，烛光摇曳"
    )

    gm_events = [
        (3, "大厅的灯闪了一下"),
        (6, "花园传来一阵风声", {"花园": {"风力": "加强"}}),
        (9, "书房的烛台翻倒了"),
    ]
    gm_random_events = [
        "一只乌鸦落在了窗台上",
        "远处的钟声敲响了",
    ]
    gm_llm_prompt = "你是一个测试用世界的 Game Master，请生成符合测试氛围的简短随机事件。"

    initial_environment = {
        "大厅": {"灯火": "亮着"},
        "花园": {"风力": "平稳"},
        "书房": {"烛光": "明亮"},
    }

    interactable_keys = {
        "大厅": ["灯火"],
        "花园": ["风力"],
    }

    render_config = {
        "location_icons": {"大厅": "🏠", "花园": "🌳", "书房": "📚"},
    }

    def setup(self, registry):
        from core.actions.common import InteractAction, MoveAction, ObserveAction, SpeakAction, ThinkAction, WhisperAction

        for action_cls in [SpeakAction, WhisperAction, MoveAction, ObserveAction, InteractAction, ThinkAction]:
            registry.register(action_cls())

    def setup_gm(self, registry):
        from core.actions.gm_npc import AddNpcAction, NpcMoveAction, NpcSpeakAction, RemoveNpcAction
        from core.actions.gm_tools import ModifyCharStateAction, ModifyEnvironmentAction, NarrateAction

        for action_cls in [NarrateAction, ModifyEnvironmentAction, ModifyCharStateAction, NpcSpeakAction, AddNpcAction, NpcMoveAction, RemoveNpcAction]:
            registry.register(action_cls())
