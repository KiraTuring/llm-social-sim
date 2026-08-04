"""Textual TUI 应用骨架"""

import asyncio
import datetime
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Header, Tree, Static, Button, Label, Collapsible


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
        width: 50;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: thick $primary;
    }
    LocationInfoScreen Static {
        margin: 0 0 1 0;
    }
    LocationInfoScreen Button {
        dock: bottom;
        margin: 1 0 0 0;
    }
    """

    def compose(self):
        env = self.world.get_environment_summary(self.location)
        visible = self.world.get_visible_locations(self.location)
        adjacent = self.world.get_adjacent_locations(self.location)
        agents = self.world.get_agents_in_location(self.location)

        with Vertical(classes="dialog"):
            yield Static(f"[bold]📍 {self.location}[/bold]")
            if env:
                yield Static(f"[bold]环境:[/bold] {env}")
            if visible:
                yield Static(f"[bold]可见地点:[/bold] {', '.join(visible)}")
            if adjacent:
                yield Static(f"[bold]可达地点:[/bold] {', '.join(adjacent)}")
            if agents:
                yield Static(f"[bold]当前角色:[/bold] {', '.join(agents)}")
            yield Button("关闭", id="close-btn")

    def on_button_pressed(self, event):
        if event.button.id == "close-btn":
            self.dismiss()

    def on_key(self, event):
        if event.key == "escape":
            self.dismiss()


class SimulationTuiApp(App):
    """Textual TUI 主应用"""

    TITLE = "LLM 社会模拟引擎"
    CSS = """
    Horizontal {
        height: 1fr;
    }
    #left-panel {
        width: 30%;
        min-width: 30;
        border: solid $primary;
    }
    #center-panel {
        width: 45%;
        border: solid $primary;
    }
    #right-panel {
        width: 25%;
        min-width: 20;
        border: solid $primary;
    }
    #controls {
        dock: bottom;
        height: 3;
        background: $surface;
        border-top: solid $primary;
        align: center middle;
    }
    Button {
        margin: 0 1;
    }
    #location-tree, #event-scroll, #agent-scroll {
        height: 100%;
    }
    Tree {
        overflow-x: hidden;
        background: transparent;
    }
    .gm-event {
        margin: 0 0 0 0;
    }
    .agent-collapsible {
        margin: 0 0 0 0;
    }
    CollapsibleTitle {
        width: 100%;
    }
    CollapsibleTitle:hover {
        background: $accent-darken-1;
        color: $text;
    }
    Collapsible {
        padding-bottom: 0;
        background: transparent;
    }
    Contents {
        padding: 1 0 0 3;
    }
    #scene-label {
        margin: 0 2;
        min-width: 16;
    }
    #status-label {
        margin: 0 1;
        text-style: italic;
        min-width: 30;
        max-width: 40;
    }
    #hint-label {
        margin: 0 2;
    }
    .panel-title {
        padding: 0 1;
        background: $primary-darken-2;
        color: $text;
        text-style: bold;
    }
    .tick-sep {
        color: $accent;
        text-style: bold;
    }
    """

    ACTION_STYLES = {
        "speak": ("💬", "cyan"),
        "whisper": ("🤫", "magenta"),
        "move": ("👣", "blue"),
        "observe": ("👁", "green"),
        "think": ("🧠", "yellow"),
        "interact": ("🤚", "dark_orange"),
        "radio": ("📻", "bright_magenta"),
    }

    def __init__(self, world, scene, gm, registry, config,
                 start_tick=1, remaining=10, mode=None, save_path=None):
        super().__init__()
        self.world = world
        self.scene = scene
        self.gm = gm
        self.registry = registry
        self.config = config
        self.start_tick = start_tick
        self.remaining = remaining
        self.save_path = save_path
        self._next_event = asyncio.Event()
        self._auto_mode = (mode == "auto")
        self._agent_expanded: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="left-panel"):
                yield Static("[bold]📍 地点[/bold]", classes="panel-title")
                yield Tree("所有地点", id="location-tree")
            with Vertical(id="center-panel"):
                yield Static("[bold]📜 事件流[/bold]", classes="panel-title")
                yield VerticalScroll(id="event-scroll")
            with Vertical(id="right-panel"):
                yield Static("[bold]👥 角色状态[/bold]", classes="panel-title")
                yield VerticalScroll(id="agent-scroll")
        with Horizontal(id="controls"):
            yield Label(f"📖 {self.scene.name}", id="scene-label")
            auto_label = "⏸ 暂停" if self._auto_mode else "▶ 自动"
            yield Button(auto_label, id="btn-auto", variant="primary")
            yield Button("⏭ 下一Tick", id="btn-next", variant="default")
            yield Button("💾 保存", id="btn-save", variant="success")
            yield Button("Q 退出", id="btn-quit", variant="error")
            yield Label("Tick 0/0", id="tick-label")
            yield Label("", id="status-label")
            yield Label("[dim]快捷键: Space=下一Tick  A=自动  S=保存  Q=退出[/]", id="hint-label")

    def on_mount(self) -> None:
        if self._auto_mode:
            self._next_event.set()
        self.run_worker(self._simulation_loop(), exclusive=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-next":
            self._next_event.set()
        elif event.button.id == "btn-auto":
            self._toggle_auto()
        elif event.button.id == "btn-save":
            self._save_state()
        elif event.button.id == "btn-quit":
            self.exit()

    def on_key(self, event) -> None:
        if event.key == "space":
            self._next_event.set()
        elif event.key == "a":
            self._toggle_auto()
        elif event.key == "s":
            self._save_state()
        elif event.key == "q":
            self.exit()

    def _set_status(self, text: str):
        self.query_one("#status-label", Label).update(text)

    def on_tree_node_selected(self, event: Tree.NodeSelected):
        location = event.node.data
        if location:
            self.push_screen(LocationInfoScreen(location, self.world))

    def on_collapsible_expanded(self, event: Collapsible.Expanded):
        name = getattr(event.collapsible, "_agent_name", None)
        if name:
            self._agent_expanded.add(name)

    def on_collapsible_collapsed(self, event: Collapsible.Collapsed):
        name = getattr(event.collapsible, "_agent_name", None)
        if name:
            self._agent_expanded.discard(name)

    def _toggle_auto(self):
        self._auto_mode = not self._auto_mode
        self.query_one("#btn-auto", Button).label = "⏸ 暂停" if self._auto_mode else "▶ 自动"
        if self._auto_mode:
            self._next_event.set()
        else:
            self._next_event.clear()

    async def _simulation_loop(self) -> None:
        from llm.client import LLMClient
        from core.logger import SimLogger
        from core.rules import RuleEngine

        log_level = getattr(__import__("logging"),
                            self.config["logging"].get("level", "INFO"))
        logger = SimLogger(
            log_file=self.config["logging"].get("file", "logs/simulation.log"),
            level=log_level,
        )
        llm = LLMClient(self.config["llm"], logger)
        self.gm.logger = logger
        rule_engine = RuleEngine()
        self.scene.setup_rules(rule_engine)

        end_tick = self.start_tick + self.remaining - 1
        tick_label = self.query_one("#tick-label", Label)

        for tick in range(self.start_tick, self.start_tick + self.remaining):
            self.world.tick = tick
            tick_label.update(f"Tick {tick}/{end_tick}")

            if not self._auto_mode:
                self._next_event.clear()

            self._set_status("📡 GM 生成事件...")
            await self.gm.check_and_inject(
                self.world, llm_client=llm if self.gm.use_llm else None
            )

            agent_actions = {}
            for agent_name in self.world.action_order:
                agent = self.world.agents[agent_name]
                self._set_status(f"🤔 {agent_name} 感知环境...")
                context = await agent.perceive(self.world, llm_client=llm)

                validation_context = self.world.build_validation_context(agent_name)

                self._set_status(f"🧠 {agent_name} 思考中...")
                action = await agent.think(
                    llm, self.registry, context, tick, validation_context
                )
                self._set_status(f"⚡ {agent_name} 行动中...")
                await agent.act(action, self.world, self.registry)
                agent_actions[agent_name] = action

            self._update_ui(agent_actions)
            self._set_status("")

            if self.config["simulation"]["rotate_order"]:
                self.world.rotate_order()

            if self._auto_mode:
                await asyncio.sleep(
                    self.config["simulation"].get("auto_delay", 2)
                )
            else:
                await self._next_event.wait()

        self._show_summary()

    def _update_ui(self, agent_actions):
        # Left panel: location tree
        tree = self.query_one("#location-tree", Tree)
        tree.clear()
        for loc in self.world.locations:
            icon = self.scene.render_config.get("location_icons", {}).get(loc, "📍")
            agents_here = self.world.get_agents_in_location(loc)
            label = f"{icon} {loc}"
            if agents_here:
                label += f" ({len(agents_here)}人)"
            branch = tree.root.add(label, data=loc)
            for name in agents_here:
                agent = self.world.agents[name]
                state_parts = [f"{k}:{v}" for k, v in agent.states.items()]
                branch.add_leaf(f"👤 {name}  {' | '.join(state_parts)}")
        tree.root.expand_all()

        # Center panel: append new tick entries
        scroll = self.query_one("#event-scroll", VerticalScroll)
        tick_label = f"═══ Tick {self.world.tick} "
        panel_width = scroll.size.width or 0
        dash_count = max(20, panel_width - len(tick_label) - 1)
        scroll.mount(Static(tick_label + "═" * dash_count, classes="tick-sep"))

        for event in self.world.event_log:
            if f"[tick {self.world.tick}]" in event:
                content = event.replace(f"[tick {self.world.tick}] ", "")
                scroll.mount(Static(f"[bold yellow]🎲 {content}[/bold yellow]", classes="gm-event"))

        for name in self.world.action_order:
            action = agent_actions.get(name)
            if not action:
                continue
            icon, color = self.ACTION_STYLES.get(action.action_type, ("▶", "white"))
            summary = f"{icon} [{color}]{name}[/] → {action.action_type}"
            if action.target:
                summary += f" -> [bold]{action.target}[/bold]"
            if action.content:
                summary += f": {action.content}"

            body_parts = []
            if action.internal_monologue:
                body_parts.append(f"🧠 {action.internal_monologue}")
            if action.result:
                for key, value in action.result.items():
                    label_map = {"observed": "观察"}
                    prefix = label_map.get(key, key)
                    body_parts.append(f"{prefix}: {value}")
            if not body_parts:
                body_parts.append("(无详细信息)")

            scroll.mount(Collapsible(
                Static("\n".join(body_parts)),
                title=summary, collapsed=True,
                classes="action-collapsible",
            ))

        scroll.scroll_end(animate=False)

        # Right panel: rebuild agent details
        right = self.query_one("#agent-scroll", VerticalScroll)
        right.remove_children()
        for name in self.world.action_order:
            agent = self.world.agents[name]
            title = f"{name} @ {agent.location}"

            body_parts = []
            state_str = " | ".join(f"{k}: {v}" for k, v in agent.states.items())
            if state_str:
                body_parts.append(f"[bold]状态:[/bold] {state_str}")
            rels = agent.relationships
            if rels:
                rel_parts = []
                for r_name, r_info in rels.items():
                    rel_parts.append(f"{r_name}({r_info.get('trust', 0)})")
                body_parts.append(f"[bold]关系:[/bold] {', '.join(rel_parts)}")
            memories = agent.memory._short_term[-5:]
            if memories:
                mem_parts = []
                for m in memories:
                    mem_parts.append(f"  - {m['event']}")
                body_parts.append(f"[bold]🧠 最近记忆:[/bold]\n" + "\n".join(mem_parts))

            c = Collapsible(
                Static("\n".join(body_parts)),
                title=title,
                collapsed=(name not in self._agent_expanded),
                classes="agent-collapsible",
            )
            c._agent_name = name
            right.mount(c)

    def _save_state(self):
        from core.save_load import save_simulation_state
        try:
            now = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            scene_module = self.scene.__class__.__module__.split(".")[-1]
            path = f"saves/{scene_module}_{now}.json"
            save_simulation_state(self.world, self.gm, scene_module, self.scene.name, path)
            self.notify(f"💾 已保存到 {path}", title="保存成功", timeout=4)
        except Exception as e:
            self.notify(f"保存失败: {e}", title="错误", severity="error", timeout=6)

    def _show_summary(self):
        scroll = self.query_one("#event-scroll", VerticalScroll)
        scroll.mount(Static("[bold green]=== 模拟完成 ===[/bold green]"))
        for name in self.world.action_order:
            agent = self.world.agents[name]
            lines = [f"[bold]{name}[/bold]"]
            rels = agent.relationships
            if rels:
                for r_name, r_info in rels.items():
                    lines.append(f"  {r_name}: {r_info.get('trust', 0)}")
            else:
                lines.append("  无关系记录")
            scroll.mount(Static("\n".join(lines)))
        scroll.scroll_end(animate=False)

        if self.save_path:
            from core.save_load import save_simulation_state
            scene_module = self.scene.__class__.__module__.split(".")[-1]
            save_simulation_state(
                self.world, self.gm, scene_module,
                self.scene.name, self.save_path,
            )
