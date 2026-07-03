"""GM Action 实现：generate_event, 后续 add_agent/add_location 等。"""

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
