"""GM Action 实现：narrate, modify_environment, 后续 add_agent/add_location 等。"""

from core.action import ActionSpec
from core.message import BROADCAST, Message


class NarrateAction(ActionSpec):
    name = "narrate"
    description = "GM 旁白：向所有角色发出一段世界叙事或事件公告（如环境变化、远处声响、氛围描述等）. 避免全知视角，只描述角色能感知到的内容"
    parameters = {"event_description": {"type": "string"}}
    text_format = "[ACTION]narrate[/ACTION]\n[CONTENT]{叙事内容，一句话，中文}[/CONTENT]"

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
        content = params.get("content", "")
        if not content:
            return [], {"summary": "事件描述为空"}
        msg = Message(sender="GM", recipients=[BROADCAST], content=content,
                      msg_type="system_event", tick=world.tick)
        world.message_bus.send(msg)
        return [], {"summary": content}


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
        loc = params.get("location", "")
        key = params.get("key", "")
        value = params.get("value", "")
        if not loc or not key or not value:
            return [], {"summary": "参数不完整，需要 location/key/value"}
        err = world.update_environment(loc, key, value)
        summary = err if err else f"环境变更: {loc}.{key} → {value}"
        return [], {"summary": summary}
