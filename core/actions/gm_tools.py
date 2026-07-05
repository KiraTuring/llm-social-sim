"""GM Action 实现：narrate, modify_environment, 后续 add_agent/add_location 等。"""

from core.action import ActionSpec
from core.message import BROADCAST, Message


class NarrateAction(ActionSpec):
    name = "narrate"
    description = "GM 旁白：向角色发出世界叙事或事件公告。target=留空（世界广播）、角色名（私信）、位置名（该位置及能观察到该位置的所有人）。避免全知视角，只描述角色能感知到的内容"
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
                        "target": {
                            "type": "string",
                            "description": "接收对象（可选）：留空=全船广播，角色名=私信给该角色，位置名=发给该位置的所有人",
                        },
                    },
                    "required": ["content"],
                },
            },
        }

    def validate_params(self, params, context):
        target = params.get("target", "")
        if not target:
            return None
        agent_names = context.get("agent_names", [])
        locations = context.get("locations", [])
        if target in agent_names or target in locations:
            return None
        return f"'{target}' 不是有效的角色或位置，可选角色: {', '.join(agent_names)}，可选位置: {', '.join(locations)}"

    def execute(self, agent_name, params, world):
        content = params.get("content", "")
        if not content:
            return [], {"summary": "事件描述为空"}
        target = params.get("target", "")

        if target in world.agents:
            recipients = [target]
        elif target in world.locations:
            recipients = world.get_hearable_agents(target, use_location=True)
            if not recipients:
                return [], {"summary": f"'{target}' 及可见位置均无人在场"}
        else:
            recipients = [BROADCAST]

        msg = Message(sender="GM", recipients=recipients, target=target if target else None,
                      content=content, msg_type="system_event", tick=world.tick)
        world.message_bus.send(msg)
        prefix = f"[{target}] " if target else ""
        summary = f"旁白: {prefix}{content}"
        return [], {"summary": summary}


class ModifyEnvironmentAction(ActionSpec):
    name = "modify_environment"
    description = "修改某个位置的环境状态指标, 你可以添加新的指标或修改已有指标的值。"
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


class ModifyCharStateAction(ActionSpec):
    name = "modify_char_state"
    description = "修改角色的非主观状态（如精力、体力、伤势等）。情绪类主观状态由角色自主控制，此工具应仅用于角色无法自行改变的外部属性。"
    parameters = {"target": {"type": "string"}, "key": {"type": "string"}, "value": {"type": "string"}}
    text_format = "[ACTION]modify_char_state[/ACTION]\n[TARGET]{角色名}[/TARGET]\n[CONTENT]{key} -> {value}[/CONTENT]"

    def get_tool_schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": "目标角色名",
                        },
                        "key": {
                            "type": "string",
                            "description": "状态名称，如精力、体力、伤势",
                        },
                        "value": {
                            "type": "string",
                            "description": "新值",
                        },
                    },
                    "required": ["target", "key", "value"],
                },
            },
        }

    def validate_params(self, params, context):
        target = params.get("target", "")
        agent_names = context.get("agent_names", [])
        if target and target not in agent_names:
            return f"'{target}' 不是有效角色名，可选: {', '.join(agent_names)}"
        return None

    def execute(self, agent_name, params, world):
        target = params.get("target", "")
        key = params.get("key", "")
        value = params.get("value", "")
        if not target or not key or not value:
            return [], {"summary": "参数不完整，需要 target/key/value"}
        if target not in world.agents:
            return [], {"summary": f"未找到角色 {target}"}
        world.agents[target].states[key] = value
        summary = f"角色状态: {target}.{key} → {value}"
        return [], {"summary": summary}
