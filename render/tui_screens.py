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
    """角色详情弹窗：静态身份/能力边界 + 深挖信息。

    动态内容（位置、状态、关系信任值、最近记忆）由右侧常驻面板承担，
    这里只放静态配置与长文本信息（印象、工具、记忆摘要、手动计划）。
    """

    def __init__(self, agent_name, world, registry=None):
        super().__init__()
        self.agent_name = agent_name
        self.world = world
        self.registry = registry

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
        from render.tui_info import format_agent_tools, is_npc

        agent = self.world.agents.get(self.agent_name)
        with VerticalScroll(classes="dialog"):
            if agent is None:
                yield Static(f"[bold]{_esc(self.agent_name)}[/bold] 不存在")
                yield Button("关闭", id="close-btn")
                return

            title = f"👤 {_esc(agent.name)}"
            if is_npc(self.agent_name, self.world):
                title = f"🎭 {_esc(agent.name)} (NPC)"
            if getattr(agent, "agent_type", "") == "ManualAgent":
                title += " [手动]"
            yield Static(f"[bold]{title}[/bold]")
            yield Static(f"[bold]身份:[/bold] {_esc(agent.role)}")
            yield Static(f"[bold]性格:[/bold] {_esc(agent.personality)}")
            yield Static(f"[bold]目标:[/bold] {_esc(agent.goal)}")

            if agent.relationships:
                imp_lines = [
                    f"  {_esc(r_name)}：{_esc(r_info.get('impression', ''))}"
                    for r_name, r_info in agent.relationships.items()
                    if r_info.get("impression")
                ]
                if imp_lines:
                    yield Static("[bold]关系印象:[/bold]\n" + "\n".join(imp_lines))

            cap_parts = []
            writable = getattr(agent, "writable_states", None)
            private = getattr(agent, "private_states", None)
            if writable:
                cap_parts.append(f"可写状态: {_esc(', '.join(sorted(writable)))}")
            if private:
                cap_parts.append(f"私有状态: {_esc(', '.join(sorted(private)))}")
            cap_parts.append(f"prompt 格式: {_esc(agent.prompt_format)}")
            cap_parts.append(f"内容截断: {agent.content_max_length}")
            yield Static("[bold]能力边界:[/bold]\n" + "\n".join(cap_parts))

            if self.registry is not None:
                tool_lines = format_agent_tools(self.registry)
                if tool_lines:
                    yield Static(
                        "[bold]🛠 可用工具:[/bold]\n" + "\n".join(tool_lines)
                    )

            summary = getattr(agent.memory, "summary", "") or ""
            if summary:
                yield Static(f"[bold]🧠 记忆摘要:[/bold]\n{_esc(summary)}")

            last_observed = getattr(agent, "last_observed_result", "") or ""
            if last_observed:
                yield Static(
                    f"[bold]👁 上次观察:[/bold] {_esc(last_observed)}"
                )

            plan = getattr(agent, "manual_plan", None)
            if plan:
                plan_lines = []
                for tick, entry in plan.get(agent.name, {}).items():
                    if tick != "*":
                        try:
                            if int(tick) < self.world.tick:
                                continue
                        except ValueError:
                            pass
                    act = entry.get("action_type", "?")
                    target = entry.get("target")
                    content = entry.get("content", "")
                    suffix = f" -> {_esc(target)}" if target else ""
                    if content:
                        suffix += f" \"{_esc(content)}\""
                    label = f"tick {tick}" if tick != "*" else "其余 tick"
                    plan_lines.append(f"  {label}: {act}{suffix}")
                if plan_lines:
                    yield Static(
                        "[bold]📋 手动计划:[/bold]\n" + "\n".join(plan_lines)
                    )

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


class SceneInfoScreen(ModalScreen):
    """场景配置弹窗：世界设定 + GM/Agent/LLM/模拟配置摘要。"""

    def __init__(self, scene, world, gm, config):
        super().__init__()
        self.scene = scene
        self.world = world
        self.gm = gm
        self.config = config

    CSS = """
    SceneInfoScreen {
        align: center middle;
    }
    SceneInfoScreen .dialog {
        width: 70%;
        max-width: 84;
        min-width: 46;
        height: auto;
        max-height: 85%;
        padding: 1 2;
        background: $surface;
        border: thick $primary;
    }
    SceneInfoScreen Static {
        margin: 0 0 1 0;
    }
    SceneInfoScreen Button {
        margin: 1 0 0 0;
    }
    """

    def compose(self):
        from render.tui_info import format_scene_sections

        with VerticalScroll(classes="dialog"):
            yield Static(f"[bold]📖 场景配置 — {_esc(self.scene.name)}[/bold]")
            for title, body in format_scene_sections(
                self.scene, self.world, self.gm, self.config
            ):
                yield Static(f"[bold]{_esc(title)}[/bold]\n{_esc(body)}")
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
