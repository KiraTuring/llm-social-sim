"""控制台渲染模块。"""

from rich.console import Console
from rich.panel import Panel
from rich.text import Text


class ConsoleRenderer:
    """控制台渲染器"""

    def __init__(self, show_full_inbox=False, show_full_monologue=True):
        self.console = Console()
        self.show_full_inbox = show_full_inbox
        self.show_full_monologue = show_full_monologue

    def render_tick(self, world, actions=None):
        """渲染一个 tick 的所有行动"""

        if world.tick == 1:
            print("\n" + "=" * 50)
            print(f"模拟开始 - Tick {world.tick}")
            print("=" * 50 + "\n")

        self.console.print(f"[bold blue]┌─ Tick {world.tick} ─{'─' * 34}┐[/bold blue]")

        tick_events = [e for e in world.event_log if f"[tick {world.tick}]" in e]
        for event in tick_events:
            content = event.replace(f"[tick {world.tick}] ", "")
            self.console.print(f"[bold yellow]🎲 GM: {content}[/bold yellow]")

        for name in world.action_order:
            agent = world.agents[name]
            action = actions.get(name) if actions else None
            panel = self._render_agent(agent, world, action)
            self.console.print(panel)

        self.console.print(f"[bold blue]└{'─' * 44}┘[/bold blue]\n")

    def _render_agent(self, agent, world, action=None):
        """渲染单个 Agent 的行动"""

        location_emoji = {"主厅": "🏠", "吧台": "🍺", "角落": "🪑", "壁炉旁": "🔥", "后厨": "🍳"}.get(agent.location, "📍")

        content = f"[bold]{location_emoji} {agent.name}[/bold] [{agent.location}]\n"
        content += f"[dim]情绪: {agent.mood} | 精力: {agent.energy}[/dim]\n"

        inbox = world.message_bus.get_inbox(agent.name)
        if inbox:
            recent = inbox[-1]
            if self.show_full_inbox:
                content += f"[dim](收到: {recent.sender}: {recent.content})[/dim]\n"
            else:
                content += f"[dim](收到: {recent.sender}: {recent.content[:30]}...)[/dim]\n"

        if action and action.content and action.content != "N/A":
            action_text = f"{action.action_type}: {action.content}"
            content += f"[cyan]→ {action_text}[/cyan]\n"

        if action and action.internal_monologue:
            if self.show_full_monologue:
                content += f"[dim](内心: {action.internal_monologue})[/dim]\n"
            else:
                content += f"[dim](内心: {action.internal_monologue[:60]}...)[/dim]\n"

        return Panel(content.expandtabs(), border_style="bright_black", expand=False)

    def render_summary(self, world):
        """渲染最终摘要"""

        self.console.print("\n" + "=" * 50)
        self.console.print("[bold green]模拟完成[/bold green]")
        self.console.print("=" * 50 + "\n")

        for name in world.action_order:
            agent = world.agents[name]
            trust_text = "\n".join([f"  {k}: {v.get('trust', 0)}" for k, v in agent.relationships.items()])
            self.console.print(f"[bold]{name}[/bold]")
            if trust_text:
                self.console.print(f"[dim]信任度:[/dim]\n{trust_text}")
            else:
                self.console.print("[dim]无关系记录[/dim]")
            self.console.print()