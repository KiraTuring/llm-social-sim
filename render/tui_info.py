"""TUI 信息格式化纯函数：工具列表、场景配置分节、NPC 判断。

与 Textual 解耦，便于无头单测。
"""


def is_npc(agent_name: str, world) -> bool:
    """判断角色是否属于 GM 控制的 NPC（名字出现在 world.npc_names 中）。"""
    return agent_name in world.npc_names


def format_agent_tools(registry) -> list[str]:
    """把 ActionRegistry 的 tool schema 格式化为展示行（只显示名称与描述）。"""
    return [
        f"- {schema['function']['name']} — {schema['function'].get('description', '')}"
        for schema in registry.get_tool_schemas()
    ]


def format_scene_sections(scene, world, gm, config) -> list[tuple[str, str]]:
    """生成场景配置弹窗的分节文本（标题, 正文）。

    只用字段白名单拼接，绝不输出 llm.api_key / base_url 等敏感原文。
    地点连通/可见性不在其中——地点详情弹窗已覆盖。
    """
    sections = []

    # 世界设定
    world_parts = []
    if getattr(scene, "world_description", ""):
        world_parts.append(getattr(scene, "world_description", ""))
    if getattr(scene, "instruction", ""):
        world_parts.append(f"【额外指引】{getattr(scene, 'instruction', '')}")
    sections.append(
        ("🌍 世界设定", "\n\n".join(world_parts) if world_parts else "(无)")
    )

    # GM 配置
    gm_cfg = config.get("gm", {})
    gm_lines = [
        f"LLM 事件: {'开启' if getattr(gm, 'use_llm', False) else '关闭'}",
        f"随机事件概率: {gm_cfg.get('random_event_chance', '?')} | "
        f"LLM 事件概率: {gm_cfg.get('llm_event_chance', '?')}",
        f"消息上限: {gm_cfg.get('message_limit', '?')} | "
        f"prompt 格式: {gm_cfg.get('prompt_format', '?')} | "
        f"历史上限: {gm_cfg.get('chat_history_max_messages', '?')}",
    ]
    gm_prompt = getattr(gm, "llm_prompt", "") or ""
    if gm_prompt:
        shown = gm_prompt if len(gm_prompt) <= 200 else gm_prompt[:200] + "…"
        gm_lines.append(f"\nGM prompt: {shown}")
    gm_registry = getattr(gm, "registry", None)
    if gm_registry is not None:
        gm_tool_names = [
            s.get("function", {}).get("name", "?")
            for s in gm_registry.get_tool_schemas()
        ]
        if gm_tool_names:
            gm_lines.append(f"GM 工具: {', '.join(gm_tool_names)}")
    sections.append(("🎲 GM 配置", "\n".join(gm_lines)))

    # 事件：剩余计划事件 + 随机事件池
    event_lines = []
    remaining = [
        e for e in getattr(gm, "scheduled_events", []) if e[0] >= world.tick
    ]
    if remaining:
        event_lines.append("【剩余计划事件】")
        event_lines.extend(f"  tick {e[0]}: {e[1]}" for e in remaining)
    else:
        event_lines.append("【剩余计划事件】无")
    random_pool = list(getattr(gm, "random_events", []))
    if random_pool:
        event_lines.append("【随机事件池】")
        event_lines.extend(f"  - {e}" for e in random_pool)
    else:
        event_lines.append("【随机事件池】无")
    sections.append(("📅 事件", "\n".join(event_lines)))

    # Agent 全局参数
    agent_cfg = config.get("agent", {})
    agent_lines = [
        f"prompt 格式: {agent_cfg.get('prompt_format', '?')}",
        f"短期记忆上限: {agent_cfg.get('memory_short_limit', '?')}",
        f"压缩阈值: {agent_cfg.get('memory_compress_threshold', '?')}",
        f"内容截断: {agent_cfg.get('content_max_length', '?')}",
    ]
    sections.append(("🤖 Agent 参数", "\n".join(agent_lines)))

    # LLM 摘要（白名单，不含密钥）
    llm_cfg = config.get("llm", {})
    llm_lines = [
        f"{key}: {llm_cfg[key]}"
        for key in ("provider", "model", "response_mode")
        if llm_cfg.get(key)
    ]
    sections.append(("⚙️ LLM", "\n".join(llm_lines) if llm_lines else "(无)"))

    # 模拟参数
    sim_cfg = config.get("simulation", {})
    sim_lines = [
        f"总 tick: {sim_cfg.get('max_ticks', '?')}",
        f"模式: {sim_cfg.get('mode', '?')}",
        f"自动间隔: {sim_cfg.get('auto_delay', '?')}s",
        f"行动顺序轮换: {'开启' if sim_cfg.get('rotate_order') else '关闭'}",
    ]
    mb_cfg = config.get("message_bus", {})
    sim_lines.append(
        f"消息上限: {mb_cfg.get('max_messages', '?')} | "
        f"收件箱上限: {mb_cfg.get('max_inbox_per_agent', '?')}"
    )
    manual_agents = sim_cfg.get("manual_agents") or []
    if manual_agents:
        sim_lines.append("手动控制: " + ", ".join(manual_agents))
    sections.append(("⏱ 模拟参数", "\n".join(sim_lines)))

    return sections
