"""通用 Action 实现：speak, whisper, move, observe, interact。"""

from core.action import ActionSpec
from core.message import Message


class SpeakAction(ActionSpec):
    name = "speak"
    description = "对某人或所有人说话"
    parameters = {"content": {"type": "string"}}
    text_format = "[ACTION]speak[/ACTION]\n[TARGET]{目标}[/TARGET]\n[CONTENT]{内容}[/CONTENT]\n[THOUGHT]{内心独白}[/THOUGHT]"

    def execute(self, agent_name, params, world):
        target = params.get("target", "all")
        content = params.get("content", "")
        recipients = ["all"] if target == "all" or not target else [target]

        msg = Message(sender=agent_name, recipients=recipients, content=content, msg_type="speech", tick=world.tick)
        world.message_bus.send(msg)
        return [msg]


class WhisperAction(ActionSpec):
    name = "whisper"
    description = "悄悄话（只有目标听到）"
    parameters = {"content": {"type": "string"}}
    text_format = "[ACTION]whisper[/ACTION]\n[TARGET]{目标}[/TARGET]\n[CONTENT]{内容}[/CONTENT]\n[THOUGHT]{内心独白}[/THOUGHT]"

    def execute(self, agent_name, params, world):
        target = params.get("target")
        content = params.get("content", "")

        if not target:
            return []

        msg = Message(sender=agent_name, recipients=[target], content=content, msg_type="whisper", tick=world.tick)
        world.message_bus.send(msg)
        return [msg]


class MoveAction(ActionSpec):
    name = "move"
    description = "移动到另一个位置"
    parameters = {"target": {"type": "string"}}
    text_format = "[ACTION]move[/ACTION]\n[TARGET]{目标位置}[/TARGET]\n[CONTENT]{移动描述}[/CONTENT]\n[THOUGHT]{内心独白}[/THOUGHT]"

    def execute(self, agent_name, params, world):
        target = params.get("target")
        content = params.get("content", "")

        if not target or target not in world.locations:
            return []

        agent = world.agents[agent_name]
        old_loc = agent.location
        agent.location = target

        msg = Message(
            sender=agent_name,
            recipients=["all"],
            content=f"从{old_loc}移动到了{target}",
            msg_type="action",
            tick=world.tick,
        )
        world.message_bus.send(msg)
        return [msg]


class ObserveAction(ActionSpec):
    name = "observe"
    description = "观察 surroundings（不做其他事）"
    parameters = {}
    text_format = "[ACTION]observe[/ACTION]\n[CONTENT]{观察内容}[/CONTENT]\n[THOUGHT]{内心独白}[/THOUGHT]"

    def execute(self, agent_name, params, world):
        return []


class InteractAction(ActionSpec):
    name = "interact"
    description = "与物品/环境互动"
    parameters = {"content": {"type": "string"}}
    text_format = "[ACTION]interact[/ACTION]\n[CONTENT]{互动描述}[/CONTENT]\n[THOUGHT]{内心独白}[/THOUGHT]"

    def execute(self, agent_name, params, world):
        content = params.get("content", "")

        msg = Message(sender=agent_name, recipients=["all"], content=content, msg_type="action", tick=world.tick)
        world.message_bus.send(msg)
        return [msg]