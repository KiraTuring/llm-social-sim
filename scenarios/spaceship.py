"""宇宙飞船场景：封闭空间内的角色张力，适合测试 GM 和 agent 行为。"""

from core.message import BROADCAST
from core.rules import RuleEngine
from scenarios.base import Scene


class SpaceshipScene(Scene):
    name = "孤星号"
    locations = ["驾驶舱", "生活舱", "引擎室", "医疗舱"]

    connections = [
        ("驾驶舱", "生活舱"),
        ("生活舱", "引擎室"),
        ("生活舱", "医疗舱"),
    ]

    visibility = {
        "驾驶舱": ["生活舱"],
        "生活舱": ["驾驶舱", "医疗舱"],
        "医疗舱": ["生活舱"],
        "引擎室": [],
    }

    world_description = (
        '"孤星号"是一艘在深空航行的中型勘探飞船，内部结构如下：\n'
        "  驾驶舱 → 飞船控制中心\n"
        "    走廊连接生活舱\n"
        "  生活舱 → 生活区，连接着驾驶舱和医疗舱\n"
        "    走廊尽头是一道厚重的隔离门，通往被完全隔离的引擎室\n"
        "  引擎室 → 完全封闭的主机舱，内部噪音极大\n"
        "    与外界只能通过无线电联系\n"
            "  医疗舱 → 与生活舱相连"
    )

    instruction = (
        "如果你的情绪或精力因当前事件发生了变化（比如感到紧张、焦虑、放松、疲惫等），"
        "请在行动时通过 state_update 参数同步更新你的状态。"
    )

    agents = [
        {
            "name": "科尔",
            "role": "船长",
            "personality": "冷静果断，重视任务胜过一切，对权威和规则有执念",
            "goal": "确保按计划抵达目标星系，证明自己的指挥能力",
            "location": "驾驶舱",
            "relationships": {
                "叶莲娜": {"trust": 2, "impression": "称职但过于多疑的工程师，总在报告不存在的隐患"},
                "芬恩": {"trust": 1, "impression": "可靠的导航员，就是话太少"},
            },
            "states": {"情绪": "平静", "精力": 100},
            "writable_states": ["情绪"],
        },
        {
            "name": "叶莲娜",
            "role": "工程师",
            "personality": "直觉敏锐，技术偏执，相信自己的判断胜过仪器数据",
            "goal": "找出引擎的异常震动源，在出事之前修好它",
            "location": "引擎室",
            "relationships": {
                "科尔": {"trust": 1, "impression": "任务第一的船长，不太相信我的直觉，得拿出证据"},
                "芬恩": {"trust": 1, "impression": "导航员，好像有心事，但技术不错"},
            },
            "states": {"情绪": "平静", "精力": 100},
            "writable_states": ["情绪"],
        },
        {
            "name": "芬恩",
            "role": "导航员",
            "personality": "沉默内敛，观察力强，不擅长直面冲突",
            "goal": "核实航向偏差的真实原因，在暴露之前决定要不要说出来",
            "location": "驾驶舱",
            "relationships": {
                "科尔": {"trust": 1, "impression": "船长很严厉，如果报告航向偏差会很麻烦"},
                "叶莲娜": {"trust": 2, "impression": "工程师技术好，但关注点和我不一样"},
            },
            "states": {"情绪": "平静", "精力": 100},
            "writable_states": ["情绪"],
        },
    ]

    initial_environment = {
        "驾驶舱": {
            "航向偏差": "+0.02°",
            "重力": "稳定",
            "通讯状态": "静默",
        },
        "引擎室": {
            "引擎震动": "轻微",
            "冷却效率": "100%",
            "燃料": "87%",
        },
        "生活舱": {
            "温度": "22°C",
            "氧气": "正常",
        },
        "医疗舱": {
            "医疗物资": "充足",
        },
    }

    interactable_keys = {
        "驾驶舱": ["通讯状态"],
        "引擎室": ["冷却效率"],
        "生活舱": ["温度"],
        "医疗舱": [],
    }

    gm_events = []
    # gm_events = [
    #     (2, "重力系统出现轻微波动，船体有几秒钟的失重感", {"生活舱": {"重力": "轻微波动"}}),
    #     (5, "通讯阵列收到一段长距离的微弱信号，来源不明", {"驾驶舱": {"通讯状态": "收到不明信号"}}),
    #     (8, "主引擎的冷却系统效率下降 3%，仍在安全范围内", {"引擎室": {"冷却效率": "97%"}}),
    #     (10, "通讯系统出现强烈电磁干扰，无线电通讯中断", {"驾驶舱": {"通讯状态": "干扰"}}),
    # ]

    gm_random_events = [
        "照明系统闪烁了一下，很快恢复正常",
        "舱内温度微降，通风系统的气流声变大了",
        "飞船外部的隔热板传来一声轻微的异响",
        "走廊的灯忽明忽暗，空气中有一丝焦糊味",
    ]

    gm_llm_prompt = (
        "你是角色扮演游戏的Game Master，扮演一艘孤立宇宙飞船的系统 AI。\n"
        "你的职责是生成飞船系统状态变化和环境事件，推动故事发展，制造戏剧冲突，刺激角色产生情绪变化。\n"
        "可以触发的类型：设备异常、舱内环境变化、通讯信号、航行数据波动。\n"
        "规则：\n"
        "- 不要引入外星生物、其他飞船或新角色\n"
        "- 不要制造致命危机或不可逆损坏\n"
        "- 你只负责飞船和环境，不替角色做决定\n"
        "- 如果角色调整了环境参数（如冷却效率、通讯状态），可以产生相应的系统响应信号\n"
        "- narrate 工具的 target 参数：留空=全船广播，角色名=只发给该角色，位置名=只发给该位置的人。在调用时注意考虑不同事件的影响范围\n"
        "比如，如果引擎室的冷却效率下降，narrate 工具可以只发给身处引擎室的角色，而不是广播给全船\n"
        "- 不要有冗余信息，如果调用多个工具，各工具给出的信息应当是互不重叠的，从而让角色自己去拼凑事件的全貌。而不是在一个工具中给出事件的全部信息，让另一个工具变得可有可无\n"
        "比如，如果事件是\"引擎室的冷却系统效率下降\"，可以用 modify_environment 工具修改冷却效率，在 narrate 工具中只告诉角色\"你听到引擎室传来一阵异响\"，而不是直接告诉角色\"冷却效率下降了\"，更不要直接在narrate 工具中透露具体的系统数据。\n"
        "- 如果你认为现在不是触发事件的好时机，可以选择不触发事件，直接回复'完成'。\n"
    )

    render_config = {
        "location_icons": {"驾驶舱": "🛸", "生活舱": "🛏️", "引擎室": "⚙️", "医疗舱": "💊"},
    }

    def setup_rules(self, engine: RuleEngine):
        @engine.on("system_event")
        def _on_system_event(msg, world):
            scary_words = ["异常", "故障", "偏离", "警告", "危险"]
            if any(word in msg.content for word in scary_words):
                for agent in world.agents.values():
                    agent.states["情绪"] = "紧张"

    def setup(self, registry):
        from core.actions.common import InteractAction, MoveAction, ObserveAction, SpeakAction, WhisperAction
        from core.actions.communication import RadioAction

        for action_cls in [SpeakAction, WhisperAction, MoveAction, ObserveAction, InteractAction]:
            registry.register(action_cls())
        registry.register(RadioAction())
