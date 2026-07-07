"""GM NPC 控制 Action：GM 通过工具控制 NPC 说话和行动。"""

from core.action import ActionSpec
from core.message import Message


class NpcSpeakAction(ActionSpec):
    name = "npc_speak"
    description = "控制 NPC 说话：让指定 NPC 角色说出指定内容，消息流对 Agent 透明。旁观者看到的跟普通说话一样"
    parameters = {"npc_name": {"type": "string"}, "content": {"type": "string"}, "target": {"type": "string"}}
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
            content=content, msg_type="speech", tick=world.tick,
        )
        world.message_bus.send(msg)
        suffix = f" -> {target}" if target else ""
        return [msg], {"summary": f"NPC {npc_name}{suffix}: {content}"}
