"""GM Action 实现：generate_event, modify_environment, 后续 add_agent/add_location 等。"""

from core.action import ActionSpec


class GenerateEventAction(ActionSpec):
    name = "generate_event"
    description = "根据当前世界状态生成一个随机事件"
    parameters = {"event_description": {"type": "string"}}
    text_format = "[ACTION]generate_event[/ACTION]\n[CONTENT]{事件描述，一句话，中文}[/CONTENT]"

    def get_tool_schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "事件描述，一句话，中文",
                        },
                    },
                    "required": ["content"],
                },
            },
        }

    def validate_params(self, params, context):
        return None

    def execute(self, agent_name, params, world):
        return [], None


class ModifyEnvironmentAction(ActionSpec):
    name = "modify_environment"
    description = "修改某个位置的环境状态指标（如引擎温度、冷却效率等）"
    parameters = {"location": {"type": "string"}, "key": {"type": "string"}, "value": {"type": "string"}}
    text_format = "[ACTION]modify_environment[/ACTION]\n[TARGET]{位置}[/TARGET]\n[CONTENT]{key} -> {value}[/CONTENT]"

    def get_tool_schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "要修改的位置名称",
                        },
                        "key": {
                            "type": "string",
                            "description": "指标名称，如冷却效率、引擎温度",
                        },
                        "value": {
                            "type": "string",
                            "description": "新值，如97%、偏高、稳定",
                        },
                    },
                    "required": ["location", "key", "value"],
                },
            },
        }

    def validate_params(self, params, context):
        loc = params.get("location", "")
        locations = context.get("locations", [])
        if loc and loc not in locations:
            return f"'{loc}' 不是有效位置，可选: {', '.join(locations)}"
        return None

    def execute(self, agent_name, params, world):
        return [], None
