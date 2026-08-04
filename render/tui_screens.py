"""TUI 弹窗屏幕：地点详情与角色详情。"""

from rich.markup import escape
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static, Button


def _esc(text: str) -> str:
    """转义 Rich 标记，避免 LLM 输出中的方括号被当作样式解析。"""
    return escape(str(text))


class LocationInfoScreen(ModalScreen):
    """地点详情弹窗"""

    def __init__(self, location, world):
        super().__init__()
        self.location = location
        self.world = world

    CSS = """
    LocationInfoScreen {
        align: center middle;
    }
    LocationInfoScreen .dialog {
        width: 60%;
        max-width: 72;
        min-width: 40;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        background: $surface;
        border: thick $primary;
    }
    LocationInfoScreen Static {
        margin: 0 0 1 0;
    }
    LocationInfoScreen Button {
        margin: 1 0 0 0;
    }
    """

    def compose(self):
        env = self.world.get_environment_summary(self.location)
        visible = self.world.get_visible_locations(self.location)
        adjacent = self.world.get_adjacent_locations(self.location)
        agents = self.world.get_agents_in_location(self.location)

        with VerticalScroll(classes="dialog"):
            yield Static(f"[bold]📍 {_esc(self.location)}[/bold]")
            if env:
                yield Static(f"[bold]环境:[/bold] {_esc(env)}")
            if visible:
                yield Static(f"[bold]可见地点:[/bold] {_esc(', '.join(visible))}")
            if adjacent:
                yield Static(f"[bold]可达地点:[/bold] {_esc(', '.join(adjacent))}")
            if agents:
                yield Static(f"[bold]当前角色:[/bold] {_esc(', '.join(agents))}")
            yield Button("关闭", id="close-btn")

    def on_button_pressed(self, event):
        if event.button.id == "close-btn":
            self.dismiss()

    def on_key(self, event):
        if event.key == "escape":
            self.dismiss()

    def on_click(self, event):
        if event.widget is self:
            self.dismiss()


class AgentInfoScreen(ModalScreen):
    """角色详情弹窗"""

    def __init__(self, agent_name, world):
        super().__init__()
        self.agent_name = agent_name
        self.world = world

    CSS = """
    AgentInfoScreen {
        align: center middle;
    }
    AgentInfoScreen .dialog {
        width: 60%;
        max-width: 72;
        min-width: 40;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        background: $surface;
        border: thick $primary;
    }
    AgentInfoScreen Static {
        margin: 0 0 1 0;
    }
    AgentInfoScreen Button {
        margin: 1 0 0 0;
    }
    """

    def compose(self):
        agent = self.world.agents.get(self.agent_name)
        with VerticalScroll(classes="dialog"):
            if agent is None:
                yield Static(f"[bold]{_esc(self.agent_name)}[/bold] 不存在")
                yield Button("关闭", id="close-btn")
                return
            yield Static(f"[bold]👤 {_esc(agent.name)}[/bold]")
            yield Static(f"[bold]身份:[/bold] {_esc(agent.role)}")
            yield Static(f"[bold]性格:[/bold] {_esc(agent.personality)}")
            yield Static(f"[bold]目标:[/bold] {_esc(agent.goal)}")
            yield Static(f"[bold]位置:[/bold] {_esc(agent.location)}")
            if agent.states:
                state_str = " | ".join(f"{k}: {_esc(v)}" for k, v in agent.states.items())
                yield Static(f"[bold]状态:[/bold] {state_str}")
            if agent.relationships:
                rel_lines = []
                for r_name, r_info in agent.relationships.items():
                    trust = r_info.get("trust", 0)
                    impression = r_info.get("impression", "")
                    rel_lines.append(f"  {_esc(r_name)} (信任 {trust})：{_esc(impression)}")
                yield Static("[bold]关系:[/bold]\n" + "\n".join(rel_lines))
            memories = agent.memory._short_term[-8:]
            if memories:
                mem_lines = [f"  - {_esc(m['event'])}" for m in memories]
                yield Static("[bold]🧠 最近记忆:[/bold]\n" + "\n".join(mem_lines))
            yield Button("关闭", id="close-btn")

    def on_button_pressed(self, event):
        if event.button.id == "close-btn":
            self.dismiss()

    def on_key(self, event):
        if event.key == "escape":
            self.dismiss()

    def on_click(self, event):
        if event.widget is self:
            self.dismiss()
