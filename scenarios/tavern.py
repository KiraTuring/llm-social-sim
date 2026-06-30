"""酒馆场景：角色定义、初始状态、GM事件表。"""

from scenarios.base import Scene


class TavernScene(Scene):
    name = "破釜酒馆"
    locations = ["主厅", "吧台", "角落", "壁炉旁", "后厨"]

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
        (3, "一个穿黑甲的士兵推门进来，环顾四周"),
        (6, "酒馆外传来马蹄声，似乎有军队经过"),
        (9, "壁炉里的火突然暗了下来，一股寒意蔓延"),
    ]

    gm_random_events = [
        "一个醉汉开始大声唱歌，打破了宁静",
        "壁炉里的火噼啪作响，火星四溅",
        "有人不小心打翻了酒杯，声音清脆",
        "一只流浪猫从后厨溜了出来",
    ]

    def setup(self, registry):
        """注册酒馆场景特定的 actions"""
        from core.actions.common import InteractAction, MoveAction, ObserveAction, SpeakAction, WhisperAction

        for action_cls in [SpeakAction, WhisperAction, MoveAction, ObserveAction, InteractAction]:
            registry.register(action_cls())