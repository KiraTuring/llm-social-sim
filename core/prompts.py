"""Prompt 构建纯函数：把长文本拼装从 Agent/GM 运行时中拆出。"""

from __future__ import annotations

from core.capabilities import IDLE, NPC_CONTROL


def build_agent_system_prompt(agent, registry) -> str:
    """构建 Agent 的 system prompt。"""
    action_names = ", ".join(registry.get_action_names())
    desc_lines = registry.describe()

    relations_text = "\n".join(
        f"- {name}: " + "，".join(f"{key}: {value}" for key, value in rel.items())
        for name, rel in agent.relationships.items()
    )

    world_part = f"\n\n## 世界\n{agent.world_description}" if agent.world_description else ""

    idle_actions = registry.get_action_names_with_capability(IDLE)
    if idle_actions:
        names = "、".join(idle_actions)
        idle_guide = f"如果你在思考、等人回复、或没有明确可做的事，优先使用 {names}。"
    else:
        idle_guide = "如果你没有明确可做的事，请选择一个副作用最小的可用行动。"

    prompt = f"""## 模拟规则
你在扮演 {agent.name}（{agent.role}），在一个持续运行的社交模拟世界中进行角色扮演。
模拟以 tick 为单位推进，每个 tick 你可以执行一次行动。{world_part}

注意：你在调用工具之前输出的任何对话文字都不会被其他角色看到，也不会对模拟产生任何影响。只有工具调用本身会改变环境和其他角色。

记忆：你过去做的事、说的话和观察到的情况会被记住，在「你最近记得的事」中显示。

其他角色和你一样自主行动——你有自己的目标和性格，他们也有。

行动顺序：所有角色在同一 tick 内按固定顺序依次行动。排在后面的角色可以看到前面角色的行动（说话、移动等），但排在前面的角色要等到下一 tick 才能知道后面的人做了什么。

## 你是谁
你是 {agent.name}（{agent.role}）。{agent.personality}

## 你的目标
{agent.goal}

## 你能做的事
行动类型: {action_names}
{desc_lines}

## 你和其他人的关系
{relations_text if relations_text else "暂无"}

## 输出要求
优先选择一个工具来行动。{idle_guide}
所有工具都包含可选的 internal_monologue 字段（内心独白，别人看不到）。"""

    if agent.instruction:
        prompt += f"\n\n{agent.instruction}"

    return prompt


def build_gm_prompt(registry, llm_prompt: str, world_description: str) -> str:
    """构建 GM 的 system prompt，自动追加可用工具。"""
    lines = []
    if llm_prompt:
        lines.append(llm_prompt)
    if world_description:
        lines.append("")
        lines.append(world_description)

    has_npc = registry.has_capability(NPC_CONTROL)
    response_rule = (
        "- 留意角色最近的消息，基于角色与环境的互动、角色对 NPC 的对话产生合理的事件响应或后续影响。"
        "普通玩家之间的聊天通常不需要回应"
        if has_npc
        else "- 留意角色最近的消息，基于角色与环境的互动产生合理的事件响应或后续影响。"
        "普通聊天通常不需要回应"
    )

    gm_rule_prompt = f"""
重要规则：
- 不要生成和近期事件冲突或简单重复的事件，可以是新事件或对近期事件的后续
- 禁止创造场景中不存在的位置——所有可用位置已在世界描述中列出
{response_rule}
- 事件要简短自然，一句话
- 最多同时生成一个新事件。可以多次调用工具，但所有调用都围绕同一个事件
"""
    lines.append(_gm_role_rules(registry))
    lines.append(gm_rule_prompt)
    lines.append("")
    lines.append("注意：你在调用工具之前输出的任何对话文字都不会被其他角色看到，也不会对模拟产生任何影响，相当于内心独白。只有工具调用本身会影响环境和其他角色。")
    lines.append("你可以使用以下工具（可一次调用多个）：")
    lines.append(registry.describe(indent="  "))
    return "\n".join(lines)


def _gm_role_rules(registry) -> str:
    """按场景是否有 NPC 生成角色控制权规则（无 NPC 场景不自相矛盾）。"""
    npc_tools = registry.get_action_names_with_capability(NPC_CONTROL)
    if npc_tools:
        return (
            "角色分两类：NPC 由你控制（使用已注册的 NPC 控制工具："
            f"{', '.join(npc_tools)}）；Player（玩家）是自主角色，禁止替其做决定、发言或改变位置"
        )
    return "本场景没有 NPC，所有角色都是自主 Player，禁止替任何角色做决定、发言或改变位置"


def build_gm_world_context(world, event_tick_window: int) -> str:
    """构建世界状态上下文（中等粒度），位置按 world.locations 顺序输出。"""
    parts = [f"当前是第 {world.tick} 个时间步。"]

    has_npc = bool(world.npcs)
    if has_npc:
        parts.append("\n角色位置与状态（Player 自主行动，NPC 由你控制）：")
    else:
        parts.append("\n角色位置与状态（Player 自主行动）：")

    for loc in world.locations:
        names = world.get_characters_in_location(loc)
        if not names:
            continue
        statuses = []
        for n in names:
            state_str = ", ".join(f"{k}:{v}" for k, v in world.characters[n].states.items())
            tag = " [NPC]" if (has_npc and n in world.npcs) else (" [Player]" if has_npc else "")
            statuses.append(f"{n}{tag}({state_str})")
        parts.append(f"  {loc}: {', '.join(statuses)}")

    env_lines = []
    for loc in world.locations:
        summary = world.get_environment_summary(loc)
        if summary:
            env_lines.append(f"  {loc}: {summary}")
    if env_lines:
        parts.append("\n环境状态：")
        parts.extend(env_lines)

    events = world.event_log_for_last_ticks(event_tick_window)
    if events:
        lines = [f"  [tick {e.tick}] {e.text}" for e in events]
        parts.append("\n最近事件：")
        parts.extend(lines)

    return "\n".join(parts)
