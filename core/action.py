"""Action 系统和 Action 注册表。"""

import re
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class Action(BaseModel):
    """解析后的 Action 数据结构"""

    action_type: str
    target: str | None = None
    content: str
    params: dict = Field(default_factory=dict)
    internal_monologue: str = ""


class ActionSpec(ABC):
    """所有 Action 的基类，场景自定义 Action 继承此类"""

    name: str
    description: str
    parameters: dict
    text_format: str

    @abstractmethod
    def execute(self, agent_name: str, params: dict, world: "WorldState") -> list["Message"]:
        """执行 action，返回产生的消息"""
        pass


class ActionRegistry:
    """管理当前场景所有可用的 Action"""

    def __init__(self):
        self._actions: dict[str, ActionSpec] = {}

    def register(self, action: ActionSpec) -> None:
        """注册一个 Action"""
        self._actions[action.name] = action

    def get(self, name: str) -> ActionSpec | None:
        """获取指定 Action"""
        return self._actions.get(name)

    def get_action_names(self) -> list[str]:
        """获取所有注册的 Action 名称"""
        return list(self._actions.keys())

    def get_tool_schema(self) -> dict:
        """动态生成 tool calling schema，包含所有 action 作为 enum"""
        action_names = self.get_action_names()
        return {
            "type": "function",
            "function": {
                "name": "act",
                "description": "执行一个行动",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action_type": {
                            "type": "string",
                            "enum": action_names,
                            "description": "行动类型",
                        },
                        "target": {"type": "string", "description": "目标（可选）"},
                        "content": {"type": "string", "description": "行动内容"},
                        "params": {"type": "object", "description": "额外参数"},
                        "internal_monologue": {"type": "string", "description": "内心独白"},
                    },
                    "required": ["action_type", "content"],
                },
            },
        }

    def get_text_guide(self) -> str:
        """动态生成文本格式说明"""
        formats = "\n\n".join([act.text_format for act in self._actions.values()])
        return f"""你必须严格按以下格式之一输出，不要有任何额外内容：

{formats}

注意：
- [THOUGHT] 是你的内心独白，别人看不到
- 如果某个字段不需要，可以省略或填 N/A
- [ACTION] 必须是以下之一：{', '.join(self.get_action_names())}"""

    def parse_text(self, text: str) -> Action:
        """从文本解析 Action（用于 text_parse 模式）"""
        thought_match = re.search(r"\[THOUGHT\](.*?)\[/THOUGHT\]", text, re.DOTALL)
        action_match = re.search(r"\[ACTION\](.*?)\[/ACTION\]", text, re.DOTALL)
        target_match = re.search(r"\[TARGET\](.*?)\[/TARGET\]", text, re.DOTALL)
        content_match = re.search(r"\[CONTENT\](.*?)\[/CONTENT\]", text, re.DOTALL)
        offer_match = re.search(r"\[OFFER\](.*?)\[/OFFER\]", text, re.DOTALL)
        request_match = re.search(r"\[REQUEST\](.*?)\[/REQUEST\]", text, re.DOTALL)

        action_type = action_match.group(1).strip() if action_match else "speak"
        content = content_match.group(1).strip() if content_match else ""
        target = target_match.group(1).strip() if target_match else None
        internal_monologue = thought_match.group(1).strip() if thought_match else ""

        params = {}
        if offer_match and request_match:
            params["offer"] = offer_match.group(1).strip()
            params["request"] = request_match.group(1).strip()

        return Action(
            action_type=action_type,
            target=target,
            content=content,
            params=params,
            internal_monologue=internal_monologue,
        )