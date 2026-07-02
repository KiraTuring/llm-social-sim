"""通用 Action 实现：speak, whisper, move, observe, interact。"""

from core.action import ActionSpec
from core.message import Message, BROADCAST


class SpeakAction(ActionSpec):
    name = "speak"
    description = "对某人或所有人说话（同位置和相邻位置的人都能听到）"
    parameters = {"content": {"type": "string"}}
    text_format = "[ACTION]speak[/ACTION]\n[TARGET]{目标}[/TARGET]\n[CONTENT]{内容}[/CONTENT]\n[THOUGHT]{内心独白}[/THOUGHT]"

    def get_tool_schema(self):
        return {
            "type": "function",
            "function": {
                "name": "speak",
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "说话对象（留空=对所有人，不要填入任何文字）"},
                        "content": {"type": "string", "description": "说话内容"},
                        "internal_monologue": {"type": "string", "description": "内心独白"},
                    },
                    "required": ["content"],
                },
            },
        }

    def validate_params(self, params, context):
        target = params.get("target", "")
        agent_names = context.get("agent_names", [])
        agent_name = context.get("agent_name", "")
        if target == agent_name:
            return "不能对自己说话"
        others = [n for n in agent_names if n != agent_name]
        if target and target != BROADCAST and target not in agent_names:
            return f"'{target}' 不存在，可用的说话对象: {', '.join(others)}（要对所有人说话请将 target 留空）"
        return None

    def execute(self, agent_name, params, world):
        target = params.get("target", BROADCAST)
        content = params.get("content", "")
        recipients = [BROADCAST] if target == BROADCAST or not target else [target]

        if recipients != [BROADCAST]:
            agent = world.agents[agent_name]
            hearable_locs = [agent.location] + world.reverse_visibility.get(agent.location, [])
            bystanders = set()
            for loc in hearable_locs:
                for name in world.get_agents_in_location(loc):
                    bystanders.add(name)
            bystanders.discard(agent_name)
            bystanders.discard(target)
            recipients = list({target} | bystanders)

        msg_target = None if recipients == [BROADCAST] else target
        msg = Message(sender=agent_name, recipients=recipients, target=msg_target, content=content, msg_type="speech", tick=world.tick)
        world.message_bus.send(msg)
        return [msg], None


class WhisperAction(ActionSpec):
    name = "whisper"
    description = "悄悄话（只有目标能听到内容，周围的人会注意到你在窃窃私语）"
    parameters = {"content": {"type": "string"}}
    text_format = "[ACTION]whisper[/ACTION]\n[TARGET]{目标}[/TARGET]\n[CONTENT]{内容}[/CONTENT]\n[THOUGHT]{内心独白}[/THOUGHT]"

    def get_tool_schema(self):
        return {
            "type": "function",
            "function": {
                "name": "whisper",
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "说话对象"},
                        "content": {"type": "string", "description": "说话内容"},
                        "internal_monologue": {"type": "string", "description": "内心独白"},
                    },
                    "required": ["target", "content"],
                },
            },
        }

    def validate_params(self, params, context):
        target = params.get("target", "")
        agent_names = context.get("agent_names", [])
        agent_name = context.get("agent_name", "")
        if not target:
            return "请指定窃窃私语的对象"
        if target == agent_name:
            return "不能对自己窃窃私语"
        others = [n for n in agent_names if n != agent_name]
        if target not in agent_names:
            return f"'{target}' 不存在，可用的说话对象: {', '.join(others)}"
        agent_loc = context.get("agent_location", "")
        if target not in context.get("agents_by_location", {}).get(agent_loc, []):
            return f"'{target}' 不在你当前的位置"
        return None

    def execute(self, agent_name, params, world):
        target = params.get("target")
        content = params.get("content", "")

        if not target:
            return [], None

        whisper_msg = Message(sender=agent_name, recipients=[target], target=target, content=content, msg_type="whisper", tick=world.tick)
        world.message_bus.send(whisper_msg)
        messages = [whisper_msg]

        agent = world.agents[agent_name]
        hearable_locs = [agent.location] + world.reverse_visibility.get(agent.location, [])
        bystanders = set()
        for loc in hearable_locs:
            for name in world.get_agents_in_location(loc):
                bystanders.add(name)
        bystanders.discard(agent_name)
        bystanders.discard(target)

        if bystanders:
            notice = Message(sender=agent_name, recipients=list(bystanders), content=f"对 {target} 窃窃私语", msg_type="action", tick=world.tick)
            world.message_bus.send(notice)
            messages.append(notice)

        return messages, None


class MoveAction(ActionSpec):
    name = "move"
    description = "移动到另一个位置"
    parameters = {"target": {"type": "string"}}
    text_format = "[ACTION]move[/ACTION]\n[TARGET]{目标位置}[/TARGET]\n[CONTENT]{移动描述}[/CONTENT]\n[THOUGHT]{内心独白}[/THOUGHT]"

    def get_tool_schema(self):
        return {
            "type": "function",
            "function": {
                "name": "move",
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "目标位置"},
                        "content": {"type": "string", "description": "移动描述（可选）"},
                        "internal_monologue": {"type": "string", "description": "内心独白"},
                    },
                    "required": ["target"],
                },
            },
        }

    def validate_params(self, params, context):
        target = params.get("target", "")
        locations = context.get("locations", [])
        agent_loc = context.get("agent_location", "")
        if target == agent_loc:
            return f"已经在 '{target}'，无需移动"
        others = [loc for loc in locations if loc != agent_loc]
        if target and target not in locations:
            return f"'{target}' 不是有效位置，可选: {', '.join(others)}"
        return None

    def execute(self, agent_name, params, world):
        target = params.get("target")
        content = params.get("content", "")

        if not target or target not in world.locations:
            return [], None

        agent = world.agents[agent_name]
        old_loc = agent.location
        agent.location = target

        msg = Message(
            sender=agent_name,
            recipients=[BROADCAST],
            content=f"从{old_loc}移动到了{target}",
            msg_type="action",
            tick=world.tick,
        )
        world.message_bus.send(msg)
        return [msg], None


class ObserveAction(ActionSpec):
    name = "observe"
    description = "观察四周（了解当前和可见位置的人与环境。并非所有位置都能看到，有些位置从当前位置不可见）"
    parameters = {}
    text_format = "[ACTION]observe[/ACTION]\n[CONTENT]{观察内容}[/CONTENT]\n[THOUGHT]{内心独白}[/THOUGHT]"

    def get_tool_schema(self):
        return {
            "type": "function",
            "function": {
                "name": "observe",
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "观察内容（可选）"},
                        "internal_monologue": {"type": "string", "description": "内心独白"},
                    },
                },
            },
        }

    def execute(self, agent_name, params, world):
        agent = world.agents[agent_name]

        visible_locs = [agent.location]
        visible_locs += world.visibility.get(agent.location, [])

        seen = []
        for loc in visible_locs:
            for name in world.get_agents_in_location(loc):
                if name == agent_name:
                    continue
                other = world.agents[name]
                seen.append(f"{name}({other.role})在{loc} - 情绪:{other.mood}")

        parts = [f"你在{agent.location}"]
        if seen:
            parts.append("看到: " + "，".join(seen))
        else:
            parts.append("没有看到其他人")

        return [], {"observed": " | ".join(parts)}


class InteractAction(ActionSpec):
    name = "interact"
    description = "与物品/环境互动"
    parameters = {"content": {"type": "string"}}
    text_format = "[ACTION]interact[/ACTION]\n[CONTENT]{互动描述}[/CONTENT]\n[THOUGHT]{内心独白}[/THOUGHT]"

    def get_tool_schema(self):
        return {
            "type": "function",
            "function": {
                "name": "interact",
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "互动描述"},
                        "internal_monologue": {"type": "string", "description": "内心独白"},
                    },
                    "required": ["content"],
                },
            },
        }

    def execute(self, agent_name, params, world):
        content = params.get("content", "")

        msg = Message(sender=agent_name, recipients=[BROADCAST], content=content, msg_type="action", tick=world.tick)
        world.message_bus.send(msg)
        return [msg], None
