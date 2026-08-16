"""ActionTextParser 纯文本解析测试。"""

from core.action_parser import parse_action_text


def test_parse_full_action():
    text = (
        "[ACTION]speak[/ACTION]\n"
        "[TARGET]艾莉娅[/TARGET]\n"
        "[CONTENT]你好[/CONTENT]\n"
        "[THOUGHT]打招呼[/THOUGHT]\n"
        "[STATE]{\"mood\": \"开心\"}[/STATE]"
    )
    action = parse_action_text(text)
    assert action.action_type == "speak"
    assert action.target == "艾莉娅"
    assert action.content == "你好"
    assert action.internal_monologue == "打招呼"
    assert action.state_update == {"mood": "开心"}


def test_parse_missing_action_returns_none():
    assert parse_action_text("[CONTENT]只有内容[/CONTENT]") is None


def test_parse_bad_state_json_ignored():
    action = parse_action_text("[ACTION]think[/ACTION]\n[STATE]not-json[/STATE]")
    assert action is not None
    assert action.state_update is None
