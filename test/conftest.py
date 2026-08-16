"""离线测试共享脚手架：基础配置、fake litellm 响应、手动计划写入。

pytest 会自动加载本文件；脚本式测试（python3.11 test/xxx.py）也可直接
`from conftest import ...` 使用（脚本所在目录在 sys.path 上）。
"""

import json
import os
import tempfile


def make_llm_config(response_mode: str = "tool_call", **overrides) -> dict:
    """构造离线 LLM 配置（test_key，不触网），可覆盖任意字段。"""
    config = {
        "provider": "deepseek",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "test_key",
        "response_mode": response_mode,
    }
    config.update(overrides)
    return config


OFFLINE_CONFIG = {
    "llm": make_llm_config(),
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
        "message_limit": 5,
    },
}


def make_response(
    text_content: str,
    tool_call: bool,
    func_name: str = "observe",
    func_args: str = '{"internal_monologue": "测试"}',
):
    """构造模拟的 litellm 响应，可自定义工具名和参数"""
    class FakeFunction:
        name = func_name
        arguments = func_args

    class FakeMessage:
        content = text_content
        tool_calls = None

    if tool_call:
        FakeMessage.tool_calls = [type("FakeToolCall", (), {
            "id": f"call_test_{func_name}",
            "type": "function",
            "function": FakeFunction(),
        })()]

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

        def model_dump_json(self):
            return '{"mock": true}'

    return FakeResponse()


def make_multi_response(calls):
    """构造含多个 tool call 的模拟响应。calls: [(func_name, func_args), ...]"""
    class FakeFunction:
        def __init__(self, name, arguments):
            self.name = name
            self.arguments = arguments

    class FakeToolCall:
        def __init__(self, name, arguments):
            self.id = f"call_{name}_{id(self)}"
            self.type = "function"
            self.function = FakeFunction(name, arguments)

    class FakeMessage:
        content = ""
        tool_calls = [FakeToolCall(n, a) for n, a in calls]

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

        def model_dump_json(self):
            return '{"mock": true}'

    return FakeResponse()


def write_plan(plan: dict) -> str:
    """把手动行动计划写入临时 JSON，返回路径。"""
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False)
    return path
