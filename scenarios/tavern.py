"""酒馆场景：角色定义、初始状态、GM事件表。"""

from core.message import BROADCAST
from core.rules import RuleEngine
from scenarios.base import Scene


class TavernScene(Scene):
    name = "破釜酒馆"
    locations = ["主厅", "吧台", "角落", "壁炉旁", "后厨"]

    connections = [
        ("主厅", "吧台"),
        ("主厅", "角落"),
        ("吧台", "角落"),
        ("壁炉旁", "吧台"),
        ("主厅", "壁炉旁"),
        ("吧台", "后厨"),
    ]

    visibility = {
        "主厅": ["吧台", "角落", "壁炉旁"],
        "吧台": ["主厅", "壁炉旁", "后厨"],
        "角落": ["主厅", "壁炉旁"],
        "壁炉旁": ["主厅", "吧台", "角落"],
        "后厨": ["吧台"],
    }

    world_description = (
        '"破釜酒馆"是一间坐落于路口的老旧酒馆，室内光线昏暗，空气中弥漫着麦酒和壁炉的烟火气。\n'
        "  主厅 → 几张歪斜的木桌散落在大厅里，墙上挂着褪色的挂毯和猎鹿头\n"
        "  吧台 → 吧台后面是一排排酒桶和擦得发亮的酒杯\n"
        "  角落 → 光线最暗的角落，适合不想被注意的人\n"
        "  壁炉旁 → 壁炉里烧着旺火，是酒馆最暖和的地方\n"
        "  后厨 → 飘出炖菜和面包的香气"
    )

    states = {"情绪": "平静", "精力": 100}
    writable_states = ["情绪"]

    agents = [
        {
            "name": "老巴克",
            "role": "酒馆老板",
            "personality": "圆滑世故，消息灵通，只想安稳做生意",
            "goal": "今晚别出乱子，把酒卖出去",
            "location": "吧台",
            "relationships": {
                "雷恩": {"trust": 0, "impression": "欠酒钱的常客，有点心虚"},
                "艾莉娅": {"trust": 1, "impression": "新来的旅人，看着有些蹊跷"},
            },
        },
        {
            "name": "雷恩",
            "role": "佣兵常客",
            "personality": "寡言警觉，身手好，欠着酒钱",
            "goal": "躲开仇家，找到活干还清欠款",
            "location": "角落",
            "relationships": {
                "老巴克": {"trust": 0, "impression": "酒馆老板，消息灵通但心机深"},
                "艾莉娅": {"trust": 0, "impression": "神秘旅人，带着上锁的盒子"},
            },
        },
        {
            "name": "艾莉娅",
            "role": "神秘旅人",
            "personality": "好奇但谨慎，带着一个上锁的木盒",
            "goal": "打听到'黑松镇'的位置，不暴露身份",
            "location": "主厅",
            "relationships": {
                "老巴克": {"trust": 1, "impression": "酒馆老板，应该消息灵通"},
                "雷恩": {"trust": 0, "impression": "佣兵，看起来危险但也许有用"},
            },
        },
    ]

    gm_events = [
        (3, "屋外传来一声闷雷，似乎要下雨了"),
        (6, "酒馆外传来马蹄声，似乎有军队经过"),
        (9, "壁炉里的火突然暗了下来，一股寒意蔓延", {"壁炉旁": {"火焰大小": "微弱"}}),
    ]

    render_config = {
        "location_icons": {"主厅": "🏠", "吧台": "🍺", "角落": "🪑", "壁炉旁": "🔥", "后厨": "🍳"},
    }

    initial_environment = {
        "主厅": {"喧闹程度": "正常", "温度": "暖和"},
        "吧台": {"酒桶存量": "充足", "灯火": "亮着"},
        "角落": {"光线": "昏暗"},
        "壁炉旁": {"火焰大小": "旺盛"},
        "后厨": {"炉火": "烧着", "今日特供": "炖菜"},
    }

    interactable_keys = {
        "吧台": ["酒桶存量"],
        "壁炉旁": ["火焰大小"],
    }

    gm_llm_prompt = (
        "你是一个中世纪酒馆世界的 Game Master。\n"
        "请根据当前世界状态生成一个符合酒馆氛围的简短随机事件。\n"
        "可以是有趣的小插曲、外面的动静、或是环境细节。\n"
        "不要生成过于戏剧性或破坏世界设定的事件。\n"
        "避免角色直接互动或替角色说话——你只负责世界环境的变化。\n"
        "不要引入新人物或新的地点，你没有能力创建新的角色或地点。\n"
    )

    gm_random_events = [
        "一个醉汉开始大声唱歌，打破了宁静",
        "壁炉里的火噼啪作响，火星四溅",
        "有人不小心打翻了酒杯，声音清脆",
        "一只流浪猫从后厨溜了出来",
    ]

    def setup_rules(self, engine: RuleEngine):
        """酒馆场景的规则：辱骂/称赞影响信任，恐怖事件影响情绪"""

        @engine.on("speech")
        def _on_speech(msg, world):
            if not msg.recipients or msg.recipients == [BROADCAST]:
                return

            target = msg.recipients[0]
            if target in world.agents:
                agent = world.agents[target]
                sender = msg.sender
                if sender in agent.relationships:
                    trust = agent.relationships[sender].get("trust", 0)

                    lower_words = ["笨", "蠢", "滚", "闭嘴"]
                    if any(word in msg.content for word in lower_words):
                        agent.relationships[sender]["trust"] = max(-5, trust - 2)
                        agent.states["情绪"] = "愤怒"

                    praise_words = ["不错", "好", "谢谢", "佩服"]
                    if any(word in msg.content for word in praise_words):
                        agent.relationships[sender]["trust"] = min(5, trust + 1)

        # TODO: 未来添加贸易 Action 后启用
        @engine.on("trade_offer")
        def _on_trade_offer(msg, world):
            if msg.recipients and msg.recipients[0] in world.agents:
                target = world.agents[msg.recipients[0]]
                sender = msg.sender
                if sender in target.relationships:
                    target.relationships[sender]["trust"] = min(5, target.relationships[sender].get("trust", 0) + 1)

        @engine.on("system_event")
        def _on_system_event(msg, world):
            scary_words = ["危险", "杀", "威胁", "追杀"]
            if any(word in msg.content for word in scary_words):
                for agent in world.agents.values():
                    agent.states["情绪"] = "紧张"

    def setup(self, registry):
        """注册酒馆场景特定的 actions"""
        from core.actions.common import InteractAction, MoveAction, ObserveAction, SpeakAction, WhisperAction

        for action_cls in [SpeakAction, WhisperAction, MoveAction, ObserveAction, InteractAction]:
            registry.register(action_cls())
