"""Action 系统和 Action 注册表。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from core.message import Message
    from core.world import WorldState


class Action(BaseModel):
    """解析后的 Action 数据结构"""

    action_type: str
    target: Optional[str] = None
    content: str
    params: dict = Field(default_factory=dict)
    internal_monologue: str = ""
    result: Optional[dict] = None
    state_update: Optional[dict] = None
    raw_tool_calls: list[dict] = Field(default_factory=list)
    raw_content: str = ""


class ActionSpec(ABC):
    """所有 Action 的基类，场景自定义 Action 继承此类"""

    name: str
    description: str
    text_format: str
    icon: str = "▶"                       # UI 展示图标（纯字符串，与框架无关）
    result_labels: dict[str, str] = {}    # result key -> 展示名，如 {"observed": "观察"}
    capabilities: frozenset[str] = frozenset()  # 声明式能力标签，core 据此判断行为，不依赖具体 Action 名

    def has_capability(self, capability: str) -> bool:
        """该 Action 是否具备某能力。"""
        return capability in self.capabilities

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
                    "properties": {},
                },
            },
        }


class ActionRegistry:
    """管理当前场景所有可用的 Action"""

    def __init__(self, include_agent_params=True):
        self._actions: dict[str, ActionSpec] = {}
        self._include_agent_params = include_agent_params

    def register(self, action: ActionSpec) -> None:
        """注册一个 Action；重名直接抛错，避免场景配置错误被静默覆盖。"""
        if action.name in self._actions:
            raise ValueError(f"Action 重复注册: {action.name}")
        self._actions[action.name] = action

    def get(self, name: str) -> ActionSpec | None:
        """获取指定 Action"""
        return self._actions.get(name)

    def get_action_names(self) -> list[str]:
        """获取所有注册的 Action 名称"""
        return list(self._actions.keys())

    def has_capability(self, capability: str) -> bool:
        """是否注册了具备某能力的 Action（core 用来做能力判断，而非按名字找工具）。"""
        return any(action.has_capability(capability) for action in self._actions.values())

    def get_action_names_with_capability(self, capability: str) -> list[str]:
        """返回具备某能力的所有 Action 名称。"""
        return [name for name, action in self._actions.items() if action.has_capability(capability)]

    def get_display_meta(self) -> dict:
        """返回所有 Action 的 UI 展示元数据 {name: {icon, result_labels}}。

        供 CLI/TUI/WebUI 统一读取，避免在 UI 层硬编码图标与结果标签。
        """
        return {
            name: {"icon": spec.icon, "result_labels": spec.result_labels}
            for name, spec in self._actions.items()
        }

    def get_tool_schemas(self) -> list[dict]:
        """返回所有 action 的 tool schema 列表，自动注入公共参数"""
        schemas = [act.get_tool_schema() for act in self._actions.values()]
        if self._include_agent_params:
            im_spec = {"type": "string", "description": "内心独白"}
            su_spec = {
                "type": "object",
                "description": "状态更新（可选）。根据当前情况更新状态，可更新的字段名见【你的状态】",
            }
            for s in schemas:
                props = s["function"]["parameters"]["properties"]
                props["internal_monologue"] = im_spec
                props["state_update"] = su_spec
        return schemas

    def describe(self, indent: str = "") -> str:
        """返回所有工具的格式化描述列表。"""
        return "\n".join(
            f"{indent}- {name}: {spec.description}"
            for name, spec in self._actions.items()
        )

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
        """从文本解析 Action（用于 text_parse 模式）。"""
        from core.action_parser import parse_action_text

        return parse_action_text(text)


def format_result_values(result: dict | None, max_length: int = 200) -> str:
    """把 result 的 value 拼成一行摘要（空 result 返回空串）。"""
    if not result:
        return ""
    return " | ".join(str(v)[:max_length] for v in result.values())


def format_tool_result(action_type: str, result: dict | None, max_length: int = 200) -> str:
    """统一的工具返回摘要，用于 tool role 消息"""
    if not result:
        return f"'{action_type}' 已执行"
    return format_result_values(result, max_length)


def validate_content_length(content: str, context: dict, max_default: int = 200) -> str | None:
    """校验内容长度，返回错误信息或 None（各 content 型工具共用）。"""
    max_len = context.get("content_max_length", max_default)
    if len(content) > max_len:
        return f"内容过长（{len(content)}字），超出限制（{max_len}字），请精简到{max_len}字以内"
    return None
