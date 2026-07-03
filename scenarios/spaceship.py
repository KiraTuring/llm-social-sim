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
        "驾驶舱": ["生活舱", "引擎室"],
        "生活舱": ["驾驶舱"],
        "引擎室": ["驾驶舱"],
        "医疗舱": [],
    }

    world_description = (
        '"孤星号"是一艘在深空航行的中型勘探飞船，内部结构如下：\n'
        "  驾驶舱 → 飞船控制中心，可通过监控屏观察引擎室状态\n"
        "    走廊连接生活舱\n"
        "  生活舱 → 生活区，连接着驾驶舱、引擎室和医疗舱\n"
        "  引擎室 → 主机舱，能通过摄像头看到驾驶舱的部分情况\n"
        "    引擎噪音较大，说话可能听不太清\n"
        "  医疗舱 → 封闭医疗室，看不到其他位置"
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
        },
    ]

    gm_events = [
        (4, "重力系统出现轻微波动，船体有几秒钟的失重感"),
        (8, "通讯阵列收到一段长距离的微弱信号，来源不明"),
        (12, "主引擎的冷却系统效率下降 3%，仍在安全范围内"),
    ]

    gm_random_events = [
        "照明系统闪烁了一下，很快恢复正常",
        "舱内温度微降，通风系统的气流声变大了",
        "监控屏上闪过一道静电干扰",
        "飞船外部的隔热板传来一声轻微的异响",
    ]

    gm_llm_prompt = (
        "你是一艘孤立宇宙飞船的系统 AI。\n"
        "你的职责是生成飞船系统状态变化和环境事件。\n"
        "可以触发的类型：设备异常、舱内环境变化、通讯信号、航行数据波动。\n"
        "规则：\n"
        "- 不要引入外星生物、其他飞船或新角色\n"
        "- 不要制造致命危机或不可逆损坏\n"
        "- 你只负责飞船和环境，不替角色做决定"
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
                    agent.mood = "紧张"

    def setup(self, registry):
        from core.actions.common import InteractAction, MoveAction, ObserveAction, SpeakAction, WhisperAction

        for action_cls in [SpeakAction, WhisperAction, MoveAction, ObserveAction, InteractAction]:
            registry.register(action_cls())
