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
                        "content": {"type": "string", "description": "说话内容，简短的一两句话"},
                    },
                    "required": ["content"],
                },
            },
        }

    def validate_params(self, params, context):
        content = params.get("content", "")
        max_len = context.get("content_max_length", 200)
        if len(content) > max_len:
            return f"内容过长（{len(content)}字），超出限制（{max_len}字），请精简到{max_len}字以内"
        target = params.get("target", "")
        agent_names = context.get("agent_names", [])
        agent_name = context.get("agent_name", "")
        if target == agent_name:
            return "不能对自己说话"
        others = [n for n in agent_names if n != agent_name]
        if not target or target == BROADCAST:
            return None
        if target not in agent_names:
            return f"'{target}' 不存在，可用的说话对象: {', '.join(others)}（要对所有人说话请将 target 留空）"
        hearable_agents = context.get("hearable_agents", agent_names)
        if target not in hearable_agents:
            return f"'{target}' 离你太远，听不到。当前能对话的人: {', '.join(hearable_agents)}"
        return None

    def execute(self, agent_name, params, world):
        target = params.get("target", BROADCAST)
        content = params.get("content", "")

        recipients = world.get_hearable_agents(agent_name)
        msg_target = None if target == BROADCAST or not target else target
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
                        "content": {"type": "string", "description": "说话内容，简短的一两句话"},
                    },
                    "required": ["target", "content"],
                },
            },
        }

    def validate_params(self, params, context):
        content = params.get("content", "")
        max_len = context.get("content_max_length", 200)
        if len(content) > max_len:
            return f"内容过长（{len(content)}字），超出限制（{max_len}字），请精简到{max_len}字以内"
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
            return f"'{target}' 不在你当前的位置({agent_loc})，无法窃窃私语"
        return None

    def execute(self, agent_name, params, world):
        target = params.get("target")
        content = params.get("content", "")

        if not target:
            return [], None

        whisper_msg = Message(sender=agent_name, recipients=[target], target=target, content=content, msg_type="whisper", tick=world.tick)
        world.message_bus.send(whisper_msg)
        messages = [whisper_msg]

        notice_recipients = world.get_hearable_agents(agent_name, exclude=target)
        if notice_recipients:
            notice = Message(sender=agent_name, recipients=notice_recipients, content=f"对 {target} 窃窃私语", msg_type="action", tick=world.tick)
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
        adjacent = context.get("adjacent_locations")
        if adjacent is not None and target and target not in adjacent:
            if not adjacent:
                return f"'{target}' 不可达。当前在 {agent_loc}，没有可前往的位置。"
            return f"'{target}' 从当前位置不可直接到达。当前在 {agent_loc}，可前往: {', '.join(adjacent)}"
        return None

    def execute(self, agent_name, params, world):
        target = params.get("target")

        agent = world.agents[agent_name]
        old_loc = agent.location

        old_recipients = world.get_hearable_agents(agent_name)
        error = world.move_agent(agent_name, target)
        if error is not None:
            return [], None

        new_recipients = world.get_hearable_agents(agent_name)
        recipients = list(set(old_recipients + new_recipients))

        msg = Message(
            sender=agent_name,
            recipients=recipients,
            content=f"从{old_loc}移动到了{target}",
            msg_type="action",
            tick=world.tick,
        )
        world.message_bus.send(msg)
        return [msg], None


class ObserveAction(ActionSpec):
    name = "observe"
    description = "观察四周。每次调用返回同样的环境信息（取决于你所在位置和可见范围），不会因为调用多次而得到不同结果。并非所有位置都能看到"
    parameters = {}
    text_format = "[ACTION]observe[/ACTION]\n[THOUGHT]{内心独白}[/THOUGHT]"

    def get_tool_schema(self):
        return {
            "type": "function",
            "function": {
                "name": "observe",
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        }

    def execute(self, agent_name, params, world):
        agent = world.agents[agent_name]

        visible_locs = world.get_visible_locations(agent.location)

        env_parts = []
        interactable = world.interactable_keys or {}
        for loc in [agent.location]:
            env = world.environment.get(loc, {})
            if env:
                items = []
                for k, v in env.items():
                    allowed = interactable.get(loc, [])
                    suffix = "[可调]" if allowed and k in allowed else ""
                    items.append(f"{k} {v}{suffix}")
                env_parts.append(f"{loc}({', '.join(items)})")

        seen = []
        for loc in visible_locs:
            for name in world.get_agents_in_location(loc):
                if name == agent_name:
                    continue
                other = world.agents[name]
                visible = {k: v for k, v in other.states.items() if k not in other._private_states}
                state_str = " ".join(f"{k}:{v}" for k, v in visible.items())
                seen.append(f"{name}({other.role})在{loc} - {state_str}")

        parts = [f"你在{agent.location}"]
        if env_parts:
            parts.append("环境: " + ", ".join(env_parts))
        if seen:
            parts.append("看到: " + "，".join(seen))
        else:
            parts.append("没有看到其他人")

        result_str = " | ".join(parts)
        if agent._last_observed_result == result_str:
            return [], {"observed": "你又观察了一会儿，没有什么新的发现"}

        agent._last_observed_result = result_str
        return [], {"observed": result_str}


class ThinkAction(ActionSpec):
    name = "think"
    description = "思考或等待。在你需要思考、等别人回复、或没有明确可做的事时使用。你的思考会写入你的记忆"
    parameters = {}
    text_format = "[ACTION]think[/ACTION]\n[THOUGHT]{内心独白}[/THOUGHT]"

    def get_tool_schema(self):
        return {
            "type": "function",
            "function": {
                "name": "think",
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "internal_monologue": {"type": "string", "description": "你的思考内容"},
                    },
                    "required": ["internal_monologue"],
                },
            },
        }

    def execute(self, agent_name, params, world):
        thought = params.get("internal_monologue", "思考中")
        return [], {"thought": thought}


class InteractAction(ActionSpec):
    name = "interact"
    description = "与物品/环境互动，可附带对环境指标的修改（如调节设备、开关系统等）"
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
                        "content": {"type": "string", "description": "互动描述，简短的一两句话"},
                        "modifications": {
                            "type": "array",
                            "description": "对环境指标的修改（可选），只操作当前位置的仪表/设备",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "target": {"type": "string", "description": "指标名称，如冷却效率、通讯状态"},
                                    "value": {"type": "string", "description": "新值，如正常、开启"},
                                },
                                "required": ["target", "value"],
                            },
                        },
                    },
                    "required": ["content"],
                },
            },
        }

    def validate_params(self, params, context):
        content = params.get("content", "")
        max_len = context.get("content_max_length", 200)
        if len(content) > max_len:
            return f"内容过长（{len(content)}字），超出限制（{max_len}字），请精简到{max_len}字以内"
        modifications = params.get("modifications")
        if not modifications:
            return None
        interactable = context.get("interactable_keys", {})
        location = context["agent_location"]
        if not interactable:
            return None
        allowed = interactable.get(location, [])
        if not allowed:
            return f"当前所在位置 '{location}' 没有可调节的指标"
        for mod in modifications:
            target = mod.get("target", "")
            if target not in allowed:
                return f"'{target}' 在 {location} 不可调节，可调项: {', '.join(allowed)}"
        return None

    def execute(self, agent_name, params, world):
        content = params.get("content", "")
        modifications = params.get("modifications", [])

        recipients = world.get_hearable_agents(agent_name)
        msg = Message(sender=agent_name, recipients=recipients, content=content, msg_type="interact", tick=world.tick)
        world.message_bus.send(msg)

        if modifications:
            agent = world.agents[agent_name]
            for mod in modifications:
                target = mod["target"]
                value = mod["value"]
                world.update_environment(agent.location, target, value)
            items = ", ".join(f"{m['target']}→{m['value']}" for m in modifications)
            return [msg], {"summary": f"指标修改: {items}"}

        return [msg], None
