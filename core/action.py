"""Action 系统和 Action 注册表。"""

import re
from abc import ABC, abstractmethod

from pydantic import BaseModel, Field


class Action(BaseModel):
    """解析后的 Action 数据结构"""

    action_type: str
    target: str | None = None
    content: str
    params: dict = Field(default_factory=dict)
    internal_monologue: str = ""
    result: dict | None = None
    state_update: dict | None = None


class ActionSpec(ABC):
    """所有 Action 的基类，场景自定义 Action 继承此类"""

    name: str
    description: str
    parameters: dict
    text_format: str

    @abstractmethod
    def execute(self, agent_name: str, params: dict, world: "WorldState") -> tuple[list["Message"], dict | None]:
        """执行 action，返回 (产生的消息, 结果数据)"""
        pass

    def validate_params(self, params: dict, context: dict) -> str | None:
        """校验参数，返回 None=合法, str=错误信息。context 包含 agent_names, locations 等"""
        return None

    def get_tool_schema(self) -> dict:
        """返回该 action 的 tool calling schema"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "internal_monologue": {"type": "string", "description": "内心独白"},
                    },
                },
            },
        }


class ActionRegistry:
    """管理当前场景所有可用的 Action"""

    def __init__(self, include_state_update=True):
        self._actions: dict[str, ActionSpec] = {}
        self._include_state_update = include_state_update

    def register(self, action: ActionSpec) -> None:
        """注册一个 Action"""
        self._actions[action.name] = action

    def get(self, name: str) -> ActionSpec | None:
        """获取指定 Action"""
        return self._actions.get(name)

    def get_action_names(self) -> list[str]:
        """获取所有注册的 Action 名称"""
        return list(self._actions.keys())

    def get_tool_schemas(self) -> list[dict]:
        """返回所有 action 的 tool schema 列表，自动注入公共参数"""
        schemas = [act.get_tool_schema() for act in self._actions.values()]
        if self._include_state_update:
            spec = {
                "type": "object",
                "description": "状态更新（可选）。根据当前情况更新状态，可更新的字段名见【你的状态】",
            }
            for s in schemas:
                s["function"]["parameters"]["properties"]["state_update"] = spec
        return schemas

    def get_text_guide(self) -> str:
        """动态生成文本格式说明"""
        formats = "\n\n".join([act.text_format for act in self._actions.values()])
        return f"""你必须严格按以下格式之一输出，不要有任何额外内容：

{formats}

注意：
- [THOUGHT] 是你的内心独白，别人看不到
- [STATE] 可选，JSON 格式的状态更新，如 {{"mood": "紧张"}}
- 如果某个字段不需要，可以省略或填 N/A
- [ACTION] 必须是以下之一：{', '.join(self.get_action_names())}"""

    def parse_text(self, text: str) -> Action:
        """从文本解析 Action（用于 text_parse 模式）"""
        thought_match = re.search(r"\[THOUGHT\](.*?)\[/THOUGHT\]", text, re.DOTALL)
        action_match = re.search(r"\[ACTION\](.*?)\[/ACTION\]", text, re.DOTALL)
        target_match = re.search(r"\[TARGET\](.*?)\[/TARGET\]", text, re.DOTALL)
        content_match = re.search(r"\[CONTENT\](.*?)\[/CONTENT\]", text, re.DOTALL)
        state_match = re.search(r"\[STATE\](.*?)\[/STATE\]", text, re.DOTALL)
        action_type = action_match.group(1).strip() if action_match else "speak"
        content = content_match.group(1).strip() if content_match else ""
        target = target_match.group(1).strip() if target_match else None
        internal_monologue = thought_match.group(1).strip() if thought_match else ""
        state_update = None
        if state_match:
            import json
            try:
                state_update = json.loads(state_match.group(1).strip())
            except json.JSONDecodeError:
                pass

        return Action(
            action_type=action_type,
            target=target,
            content=content,
            internal_monologue=internal_monologue,
            state_update=state_update,
        )