"""酒馆场景：角色定义、初始状态、GM事件表。"""

from core.rules import RuleEngine
from scenarios.base import Scene


class TavernScene(Scene):
    name = "破釜酒馆"
    locations = ["主厅", "吧台", "角落", "壁炉旁", "后厨"]

    visibility = {
        "主厅": ["吧台", "角落", "壁炉旁", "后厨"],
        "吧台": ["主厅", "壁炉旁"],
        "角落": ["主厅", "壁炉旁"],
        "壁炉旁": ["主厅", "吧台"],
        "后厨": ["主厅"],
    }

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
        (9, "壁炉里的火突然暗了下来，一股寒意蔓延"),
    ]

    render_config = {
        "location_icons": {"主厅": "🏠", "吧台": "🍺", "角落": "🪑", "壁炉旁": "🔥", "后厨": "🍳"},
    }

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
            if not msg.recipients or msg.recipients == ["all"]:
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
                        agent.mood = "愤怒"

                    praise_words = ["不错", "好", "谢谢", "佩服"]
                    if any(word in msg.content for word in praise_words):
                        agent.relationships[sender]["trust"] = min(5, trust + 1)

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
                    agent.mood = "紧张"

    def setup(self, registry):
        """注册酒馆场景特定的 actions"""
        from core.actions.common import InteractAction, MoveAction, ObserveAction, SpeakAction, WhisperAction

        for action_cls in [SpeakAction, WhisperAction, MoveAction, ObserveAction, InteractAction]:
            registry.register(action_cls())
