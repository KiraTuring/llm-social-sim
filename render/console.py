"""控制台渲染模块。"""

from rich.console import Console
from rich.panel import Panel

from core.event import SOURCE_AGENT, SOURCE_GM, SOURCE_NPC, SOURCE_RULE

EVENT_STYLES = {
    SOURCE_GM: ("🎲 GM", "bold yellow"),
    SOURCE_NPC: ("🎭 NPC", "bold magenta"),
    SOURCE_AGENT: ("👤", "bold cyan"),
    SOURCE_RULE: ("⚙️", "bold white"),
}


class ConsoleRenderer:
    """控制台渲染器"""

    _PREVIEW_LEN = 60  # 收件箱/独白预览截断长度

    def __init__(self, render_config=None, show_full_inbox=False, show_full_monologue=True):
        self.console = Console()
        self.render_config = render_config or {}
        self.show_full_inbox = show_full_inbox
        self.show_full_monologue = show_full_monologue

    def _format_inbox_line(self, m, truncate=False):
        """格式化收件箱消息为 [sender -> target] content 格式"""
        sender = m["sender"]
        if m.get("target"):
            sender += f" -> {m['target']}"
        text = m["content"]
        if truncate and len(text) > self._PREVIEW_LEN:
            text = text[:self._PREVIEW_LEN] + "..."
        return f"[{sender}] {text}"

    def render_tick(self, world, actions=None):
        """渲染一个 tick 的所有行动"""

        if world.tick == 1:
            print("\n" + "=" * 50)
            print(f"模拟开始 - Tick {world.tick}")
            print("=" * 50 + "\n")

        self.console.print(f"[bold blue]┌─ Tick {world.tick} ─{'─' * 34}┐[/bold blue]")

        tick_events = world.event_log_for_tick(world.tick)
        for event in tick_events:
            if event.source_type not in (SOURCE_GM, SOURCE_NPC):
                continue  # Agent 行动由角色面板渲染，避免顶部事件区重复展示
            prefix, style = EVENT_STYLES.get(event.source_type, ("🎲 GM", "bold yellow"))
            self.console.print(f"[{style}]{prefix}: {event.text}[/{style}]")

        for name in world.action_order:
            agent = world.agents[name]
            action = actions.get(name) if actions else None
            panel = self._render_agent(agent, world, action)
            self.console.print(panel)

        self.console.print(f"[bold blue]└{'─' * 44}┘[/bold blue]\n")

    def _render_agent(self, agent, world, action=None):
        """渲染单个 Agent 的行动"""

        location_emoji = self.render_config.get("location_icons", {}).get(agent.location, "📍")

        content = f"[bold]{location_emoji} {agent.name}[/bold] [{agent.location}]\n"
        state_str = " | ".join(f"{k}: {v}" for k, v in agent.states.items())
        content += f"[dim]{state_str}[/dim]\n"

        inbox = agent.perceived_inbox
        if inbox:
            if self.show_full_inbox:
                lines = [f"  {self._format_inbox_line(m)}" for m in inbox]
                content += "[dim](收到:\n" + "\n".join(lines) + ")[/dim]\n"
            else:
                content += f"[dim](收到: {self._format_inbox_line(inbox[-1], truncate=True)})[/dim]\n"

        if action:
            action_line = action.action_type
            if action.target:
                action_line += f" -> {action.target}"
            if action.content and action.content != "N/A":
                action_line += f": {action.content}"
            content += f"[cyan]→ {action_line}[/cyan]\n"

        if action and action.result:
            for key, value in action.result.items():
                label = {"observed": "观察"}.get(key, key)
                content += f"[green]  {label}: {value}[/green]\n"

        if action and action.internal_monologue:
            if self.show_full_monologue:
                content += f"[dim](内心: {action.internal_monologue})[/dim]\n"
            else:
                content += f"[dim](内心: {action.internal_monologue[:self._PREVIEW_LEN]}...)[/dim]\n"

        return Panel(content.expandtabs(), border_style="bright_black", expand=False)

    def render_summary(self, world):
        """渲染最终摘要"""

        self.console.print("\n" + "=" * 50)
        self.console.print("[bold green]模拟完成[/bold green]")
        self.console.print("=" * 50 + "\n")

        for name in world.action_order:
            agent = world.agents[name]
            rel_lines = []
            for r_name, rel in agent.relationships.items():
                attrs = "，".join(f"{k}: {v}" for k, v in rel.items())
                rel_lines.append(f"  {r_name}: {attrs}" if attrs else f"  {r_name}")
            self.console.print(f"[bold]{name}[/bold]")
            if rel_lines:
                self.console.print("[dim]关系:[/dim]\n" + "\n".join(rel_lines))
            else:
                self.console.print("[dim]无关系记录[/dim]")
            self.console.print()
