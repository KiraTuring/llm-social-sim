"""GM NPC 控制 Action：GM 通过工具控制 NPC 说话、移动、添加和移除。"""

from core.action import ActionSpec
from core.action import NPC_CONTROL
from core.character import NPC
from core.event import SOURCE_NPC
from core.message import Message


class AddNpcAction(ActionSpec):
    name = "npc_add"
    icon = "🆕"
    capabilities = frozenset({NPC_CONTROL})
    description = "动态创建一个新的 NPC 角色，使其出现在指定位置并可由 GM 通过 npc_speak 控制。用于剧情需要临时登场的角色"
    text_format = "[ACTION]npc_add[/ACTION]\n[CONTENT]{NPC名} 在 {位置}，身份:{角色}[/CONTENT]"

    def get_tool_schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "npc_name": {"type": "string", "description": "新 NPC 的角色名，不能与现有角色重名"},
                        "location": {"type": "string", "description": "NPC 出现的位置"},
                        "role": {"type": "string", "description": "身份/职业，如杂货商、卫兵"},
                        "personality": {"type": "string", "description": "性格描述（可选）"},
                        "goal": {"type": "string", "description": "目标/动机（可选）"},
                    },
                    "required": ["npc_name", "location"],
                },
            },
        }

    def validate_params(self, params, context):
        npc_name = params.get("npc_name", "")
        location = params.get("location", "")
        agent_names = context.get("agent_names", [])
        npc_names = context.get("npc_names", [])
        locations = context.get("locations", [])
        if npc_name in agent_names or npc_name in npc_names:
            return f"'{npc_name}' 已存在，不可重复创建"
        if location not in locations:
            return f"'{location}' 不是有效位置，可选: {', '.join(locations)}"
        return None

    def execute(self, agent_name, params, world):
        npc = NPC(
            name=params["npc_name"],
            location=params["location"],
            role=params.get("role", ""),
            personality=params.get("personality", ""),
            goal=params.get("goal", ""),
        )
        error = world.add_npc(npc)
        if error is not None:
            return [], {"result": error}
        world.add_event(
            f"新 NPC 出现: {npc.name}（{npc.role or '身份未知'}）在{npc.location}",
            source=npc.name,
            source_type=SOURCE_NPC,
        )
        return [], {"result": f"NPC '{npc.name}' 已出现在{npc.location}"}


class NpcSpeakAction(ActionSpec):
    name = "npc_speak"
    icon = "💬"
    capabilities = frozenset({NPC_CONTROL})
    description = "控制 NPC 说话：让指定 NPC 角色说出指定内容，消息流对 Agent 透明。旁观者看到的跟普通说话一样"
    text_format = "[ACTION]npc_speak[/ACTION]\n[TARGET]{NPC名}[/TARGET]\n[CONTENT]{内容}[/CONTENT]"

    def get_tool_schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "npc_name": {"type": "string", "description": "要说话的 NPC 角色名"},
                        "content": {"type": "string", "description": "说话内容"},
                        "target": {"type": "string", "description": "说话对象（可选），留空=对范围内所有人"},
                    },
                    "required": ["npc_name", "content"],
                },
            },
        }

    def validate_params(self, params, context):
        npc_name = params.get("npc_name", "")
        target = params.get("target", "")
        npc_names = context.get("npc_names", [])
        agent_names = context.get("agent_names", [])
        if npc_name not in npc_names:
            return f"'{npc_name}' 不是 NPC，可选 NPC: {', '.join(npc_names)}"
        if target and target not in agent_names:
            return f"'{target}' 不是有效角色，可选: {', '.join(agent_names)}"
        return None

    def execute(self, agent_name, params, world):
        npc_name = params["npc_name"]
        content = params["content"]
        target = params.get("target", "")

        recipients = world.get_hearable_agents(npc_name)
        msg = Message(
            sender=npc_name, recipients=recipients,
            target=target if target else None,
            content=content, tag="speech", tick=world.tick,
        )
        suffix = f" -> {target}" if target else ""
        world.add_event(
            f"NPC {npc_name}{suffix}: {content}",
            source=npc_name,
            source_type=SOURCE_NPC,
        )
        return [msg], None


class NpcMoveAction(ActionSpec):
    name = "npc_move"
    icon = "🚶"
    capabilities = frozenset({NPC_CONTROL})
    description = "移动一个 NPC 到任意有效位置（GM 全能，不受连通性限制），用于角色走动或事件结束后离场"
    text_format = "[ACTION]npc_move[/ACTION]\n[TARGET]{NPC名}[/TARGET]\n[CONTENT]{目标位置}[/CONTENT]"

    def get_tool_schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "npc_name": {"type": "string", "description": "要移动的 NPC 角色名"},
                        "target": {"type": "string", "description": "目标位置（任意有效位置，不受连通性限制）"},
                        "content": {"type": "string", "description": "移动时的行为表现（可选，别人能看到；不要写内心想法）"},
                    },
                    "required": ["npc_name", "target"],
                },
            },
        }

    def validate_params(self, params, context):
        npc_name = params.get("npc_name", "")
        target = params.get("target", "")
        npc_names = context.get("npc_names", [])
        npc_locations = context.get("npc_locations", {})
        locations = context.get("locations", [])
        if npc_name not in npc_names:
            return f"'{npc_name}' 不是 NPC，可选 NPC: {', '.join(npc_names)}"
        if target not in locations:
            return f"'{target}' 不是有效位置，可选: {', '.join(locations)}"
        if npc_name in npc_locations and target == npc_locations[npc_name]:
            return f"NPC '{npc_name}' 已经在 '{target}'，无需移动"
        return None

    def execute(self, agent_name, params, world):
        npc_name = params["npc_name"]
        target = params["target"]

        old_recipients = world.get_hearable_agents(npc_name)
        old_loc = world.npcs[npc_name].location
        error = world.move_character(npc_name, target)
        if error is not None:
            return [], {"result": error}
        new_recipients = world.get_hearable_agents(npc_name)
        recipients = list(set(old_recipients + new_recipients))

        desc = f"从{old_loc}移动到了{target}"
        content = params.get("content", "").strip()
        if content:
            desc = f"{desc}，{content}"

        msg = Message(
            sender=npc_name,
            recipients=recipients,
            content=desc,
            tag="action",
            tick=world.tick,
        )
        world.add_event(
            f"NPC {npc_name}: {desc}",
            source=npc_name,
            source_type=SOURCE_NPC,
        )
        return [msg], {"result": f"NPC '{npc_name}' 已从{old_loc}移动到{target}"}


class RemoveNpcAction(ActionSpec):
    name = "npc_remove"
    icon = "🚪"
    capabilities = frozenset({NPC_CONTROL})
    description = "移除一个 NPC（叙事上表现为'xx离开了'）。静默执行，离开的播报请用 narrate 自行描述"
    text_format = "[ACTION]npc_remove[/ACTION]\n[TARGET]{NPC名}[/TARGET]\n[CONTENT]离开描述（可选）[/CONTENT]"

    def get_tool_schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "npc_name": {"type": "string", "description": "要移除的 NPC 角色名"},
                    },
                    "required": ["npc_name"],
                },
            },
        }

    def validate_params(self, params, context):
        npc_name = params.get("npc_name", "")
        npc_names = context.get("npc_names", [])
        if npc_name not in npc_names:
            return f"'{npc_name}' 不是 NPC，可选 NPC: {', '.join(npc_names)}"
        return None

    def execute(self, agent_name, params, world):
        npc_name = params["npc_name"]
        error = world.remove_npc(npc_name)
        if error is not None:
            return [], {"result": error}
        world.add_event(
            f"NPC {npc_name} 离开了",
            source=npc_name,
            source_type=SOURCE_NPC,
        )
        return [], {"result": f"NPC '{npc_name}' 已移除"}
