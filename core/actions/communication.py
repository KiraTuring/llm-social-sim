"""通讯 Action 实现：无线电通讯等远程通信。"""

from core.action import ActionSpec, validate_content_length
from core.message import Message


class RadioAction(ActionSpec):
    name = "radio"
    description = "通过无线电与任意位置的队友通话。只有通话对象能听到内容，可见范围内的人会看到你在使用无线电但听不到内容、也不知道通话对象。"
    text_format = ("[ACTION]radio[/ACTION]\n"
                   "[TARGET]{通话对象}[/TARGET]\n"
                   "[CONTENT]{通话内容}[/CONTENT]\n"
                   "[THOUGHT]{内心独白}[/THOUGHT]")

    # 环境指标中哪些值表示无线电不可用
    _blocked_values = {"干扰", "故障"}

    def get_tool_schema(self):
        return {
            "type": "function",
            "function": {
                "name": "radio",
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "通话对象的名字"},
                        "content": {"type": "string", "description": "通话内容，简短的一两句话"},
                    },
                    "required": ["target", "content"],
                },
            },
        }

    def validate_params(self, params, context):
        content = params.get("content", "")
        if error := validate_content_length(content, context):
            return error
        target = params.get("target", "")
        if not target:
            return "通话对象不能为空"
        agent_names = context.get("agent_names", [])
        if target not in agent_names:
            return f"'{target}' 不是有效的角色，可选: {', '.join(agent_names)}"
        if target == context.get("agent_name"):
            return "不能用无线电联系自己"
        return None

    def execute(self, agent_name, params, world):
        target = params.get("target", "")
        content = params.get("content", "")

        # Check for radio interference
        agent = world.agents[agent_name]
        env = world.environment.get(agent.location, {})
        for val in env.values():
            if val in self._blocked_values:
                return [], {"summary": "无线电受干扰，无法通信"}

        # 1. Send full message to target
        target_msg = Message(
            sender=agent_name, recipients=[target], target=target,
            content=content, msg_type="radio", tick=world.tick,
        )
        world.message_bus.send(target_msg)
        msgs = [target_msg]

        # 2. Notice to speaker's bystanders (they see the speaker on the radio)
        speaker_bystanders = world.get_hearable_agents(agent_name, exclude=target)
        if speaker_bystanders:
            speaker_notice = Message(
                sender=agent_name, recipients=speaker_bystanders, target=None,
                content="对着无线电说了几句话", msg_type="action", tick=world.tick,
            )
            world.message_bus.send(speaker_notice)
            msgs.append(speaker_notice)

        # 3. Notice to target's bystanders (they see the target's radio receiving)
        target_bystanders = world.get_hearable_agents(target, exclude=agent_name)
        if target_bystanders:
            target_notice = Message(
                sender=target, recipients=target_bystanders, target=None,
                content="身上的无线电中传来一段通话声", msg_type="action", tick=world.tick,
            )
            world.message_bus.send(target_notice)
            msgs.append(target_notice)

        return msgs, None
