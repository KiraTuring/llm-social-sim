"""Textual TUI 应用骨架"""

import asyncio
import datetime
import traceback

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Header, Tree, Static, Button, Label, Collapsible


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


class SimulationTuiApp(App):
    """Textual TUI 主应用"""

    TITLE = "LLM 社会模拟引擎"
    MAX_EVENT_ENTRIES = 400  # 事件流保留的最大条目数
    CSS = """
    Horizontal {
        height: 1fr;
        overflow-x: hidden;
    }
    #left-panel {
        width: 1fr;
        min-width: 24;
        border: solid $primary;
    }
    #center-panel {
        width: 2fr;
        min-width: 34;
        border: solid $primary;
    }
    #right-panel {
        width: 1fr;
        min-width: 18;
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
                 start_tick=1, remaining=10, mode=None, save_path=None,
                 logger=None, llm=None, rule_engine=None):
        super().__init__()
        self.world = world
        self.scene = scene
        self.gm = gm
        self.registry = registry
        self.config = config
        self.logger = logger
        self.llm = llm
        self.rule_engine = rule_engine
        self.start_tick = start_tick
        self.remaining = remaining
        self.save_path = save_path
        self._next_event = asyncio.Event()
        self._auto_mode = (mode == "auto")
        self._agent_expanded: set[str] = set()
        self._events_rendered = 0
        self._tree_nodes: dict = {}
        self._tree_labels: dict = {}
        self._tree_agent_nodes: dict = {}
        self._tree_agent_labels: dict = {}
        self._agent_panels: dict = {}
        self._agent_panel_bodies: dict = {}
        self._agent_panel_sigs: dict = {}
        self._agent_panel_order: tuple = ()

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
        self._update_hint_visibility()
        if self._auto_mode:
            self._next_event.set()
        self.run_worker(self._simulation_loop(), exclusive=True)

    def _update_hint_visibility(self) -> None:
        """窄终端隐藏快捷键提示，避免底部控制条溢出。"""
        try:
            hint = self.query_one("#hint-label", Label)
            hint.display = self.size.width >= 100
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-next":
            self._next_event.set()
        elif event.button.id == "btn-auto":
            self._toggle_auto()
        elif event.button.id == "btn-save":
            self._save_state()
        elif event.button.id == "btn-quit":
            self.exit()
        # 让按钮失焦，避免 Space/Enter 再次触发刚点过的按钮
        self.set_focus(None)

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
        data = event.node.data
        if not data or not isinstance(data, dict):
            return
        if data.get("type") == "location":
            self.push_screen(LocationInfoScreen(data["name"], self.world))
        elif data.get("type") == "agent":
            self.push_screen(AgentInfoScreen(data["name"], self.world))

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
        from core.engine import SimulationEngine

        engine = SimulationEngine(
            self.world, self.gm, self.registry,
            self.llm, self.rule_engine, self.logger, self.config,
        )

        end_tick = self.start_tick + self.remaining - 1
        tick_label = self.query_one("#tick-label", Label)
        total_agents = max(1, len(self.world.action_order))

        try:
            for tick in range(self.start_tick, self.start_tick + self.remaining):
                tick_label.update(f"Tick {tick}/{end_tick}")

                if not self._auto_mode:
                    self._next_event.clear()

                self._set_status("📡 GM 生成事件...")
                await engine.begin_tick(tick)

                # Tick 分隔线 + GM 事件先上屏，面板同步
                await self._render_tick_start()

                # 按单个 Agent 步进：每个角色行动完立即刷新 UI
                while engine.next_agent:
                    next_name = engine.next_agent
                    done = total_agents - len(engine.pending_agents)
                    self._set_status(f"🤔 {next_name} 行动中... ({done}/{total_agents})")
                    step = await engine.step_agent()
                    self._set_status("")
                    await self._render_agent_step(step)

                await engine.end_tick()
                self._set_status("")

                if self._auto_mode:
                    await asyncio.sleep(
                        self.config["simulation"].get("auto_delay", 2)
                    )
                else:
                    await self._next_event.wait()
        except Exception as e:
            self._handle_simulation_error(self.world.tick, e, self.logger)
        else:
            self._show_summary()

    async def _render_tick_start(self):
        """渲染 tick 分隔线和本 tick 新增的 GM 事件，同步两侧面板。"""
        self._update_hint_visibility()
        scroll = self.query_one("#event-scroll", VerticalScroll)
        tick_label = f"═══ Tick {self.world.tick} "
        panel_width = scroll.size.width or 0
        dash_count = max(20, panel_width - len(tick_label) - 1)
        scroll.mount(Static(tick_label + "═" * dash_count, classes="tick-sep"))

        prefix = f"[tick {self.world.tick}] "
        for event in self.world.event_log[self._events_rendered:]:
            if event.startswith(prefix):
                content = event[len(prefix):]
                scroll.mount(Static(f"[bold yellow]🎲 {_esc(content)}[/bold yellow]", classes="gm-event"))
        self._events_rendered = len(self.world.event_log)

        self._sync_location_tree()
        self._sync_agent_panel()
        scroll.scroll_end(animate=False)
        await self._trim_event_scroll(scroll)

    async def _render_agent_step(self, step):
        """单个 Agent 行动完成后增量渲染：事件流追加一条 + 面板同步。"""
        self._update_hint_visibility()
        scroll = self.query_one("#event-scroll", VerticalScroll)
        action = step.action
        if not action:
            return

        icon, color = self.ACTION_STYLES.get(action.action_type, ("▶", "white"))
        summary = f"{icon} [{color}]{_esc(step.agent_name)}[/] → {_esc(action.action_type)}"
        if action.target:
            summary += f" -> [bold]{_esc(action.target)}[/bold]"
        if action.content:
            summary += f": {_esc(action.content)}"

        body_parts = []
        if action.internal_monologue:
            body_parts.append(f"🧠 {_esc(action.internal_monologue)}")
        if action.result:
            for key, value in action.result.items():
                label_map = {"observed": "观察"}
                prefix = label_map.get(key, key)
                body_parts.append(f"{prefix}: {_esc(value)}")
        if not body_parts:
            body_parts.append("(无详细信息)")

        scroll.mount(Collapsible(
            Static("\n".join(body_parts)),
            title=summary, collapsed=True,
            classes="action-collapsible",
        ))

        self._sync_agent_panel()
        self._sync_location_tree()
        scroll.scroll_end(animate=False)
        await self._trim_event_scroll(scroll)

    async def _trim_event_scroll(self, scroll: VerticalScroll) -> None:
        """事件流超过上限时从头部移除旧条目，防止长跑内存增长。"""
        overflow = len(scroll.children) - self.MAX_EVENT_ENTRIES
        if overflow <= 0:
            return
        await scroll.remove_children(scroll.children[:overflow])

    def _sync_location_tree(self) -> None:
        """原地同步地点树：只更新变化的部分，保留用户的折叠状态。"""
        tree = self.query_one("#location-tree", Tree)

        for loc in self.world.locations:
            icon = self.scene.render_config.get("location_icons", {}).get(loc, "📍")
            agents_here = self.world.get_agents_in_location(loc)
            label = f"{icon} {_esc(loc)}"
            if agents_here:
                label += f" ({len(agents_here)}人)"
            branch = self._tree_nodes.get(loc)
            if branch is None:
                branch = tree.root.add(label, data={"type": "location", "name": loc})
                branch.expand()
                self._tree_nodes[loc] = branch
            elif self._tree_labels.get(loc) != label:
                branch.label = label
            self._tree_labels[loc] = label

        # 防御：清理已不存在的地点分支
        for loc in list(self._tree_nodes):
            if loc not in self.world.locations:
                self._tree_nodes.pop(loc).remove()
                self._tree_labels.pop(loc, None)

        # 同步角色叶子节点：状态变化改 label，移动时摘旧挂新
        live_names = set()
        for name, agent in self.world.agents.items():
            live_names.add(name)
            branch = self._tree_nodes.get(agent.location)
            state_parts = [f"{k}:{_esc(v)}" for k, v in agent.states.items()]
            label = f"👤 {_esc(name)}  {' | '.join(state_parts)}"
            node = self._tree_agent_nodes.get(name)
            if node is None:
                if branch is None:
                    continue
                node = branch.add_leaf(label, data={"type": "agent", "name": name})
                self._tree_agent_nodes[name] = node
                self._tree_agent_labels[name] = label
            elif node.parent is not branch:
                node.remove()
                if branch is None:
                    self._tree_agent_nodes.pop(name, None)
                    self._tree_agent_labels.pop(name, None)
                    continue
                node = branch.add_leaf(label, data={"type": "agent", "name": name})
                self._tree_agent_nodes[name] = node
                self._tree_agent_labels[name] = label
            elif self._tree_agent_labels.get(name) != label:
                node.label = label
                self._tree_agent_labels[name] = label

        for name in list(self._tree_agent_nodes):
            if name not in live_names:
                self._tree_agent_nodes.pop(name).remove()
                self._tree_agent_labels.pop(name, None)

    def _sync_agent_panel(self) -> None:
        """原地同步角色状态面板：内容没变不动，变了才更新，保留展开/滚动状态。"""
        right = self.query_one("#agent-scroll", VerticalScroll)
        order = tuple(self.world.action_order)
        if order != self._agent_panel_order:
            # 行动顺序变化（rotate_order）时整体重建以保持顺序，展开状态保留
            for w in self._agent_panels.values():
                w.remove()
            self._agent_panels.clear()
            self._agent_panel_bodies.clear()
            self._agent_panel_sigs.clear()
            self._agent_panel_order = order

        prev = None
        for name in self.world.action_order:
            agent = self.world.agents[name]
            title = f"{name} @ {agent.location}"

            body_parts = []
            state_str = " | ".join(f"{k}: {_esc(v)}" for k, v in agent.states.items())
            if state_str:
                body_parts.append(f"[bold]状态:[/bold] {state_str}")
            rels = agent.relationships
            if rels:
                rel_parts = []
                for r_name, r_info in rels.items():
                    rel_parts.append(f"{_esc(r_name)}({r_info.get('trust', 0)})")
                body_parts.append(f"[bold]关系:[/bold] {', '.join(rel_parts)}")
            memories = agent.memory._short_term[-5:]
            if memories:
                mem_parts = []
                for m in memories:
                    mem_parts.append(f"  - {_esc(m['event'])}")
                body_parts.append(f"[bold]🧠 最近记忆:[/bold]\n" + "\n".join(mem_parts))
            body = "\n".join(body_parts)
            sig = f"{title}\n{body}"

            c = self._agent_panels.get(name)
            if c is None:
                static = Static(body)
                c = Collapsible(
                    static,
                    title=title,
                    collapsed=(name not in self._agent_expanded),
                    classes="agent-collapsible",
                )
                c._agent_name = name
                if prev is not None:
                    right.mount(c, after=prev)
                else:
                    right.mount(c)
                self._agent_panels[name] = c
                self._agent_panel_bodies[name] = static
                self._agent_panel_sigs[name] = sig
                prev = c
                continue

            if self._agent_panel_sigs.get(name) != sig:
                static = self._agent_panel_bodies.get(name)
                if static is not None:
                    static.update(body)
                if c.title != title:
                    c.title = title
                self._agent_panel_sigs[name] = sig
            prev = c

    def _handle_simulation_error(self, tick: int, error: Exception, logger) -> None:
        """模拟中途异常：记录日志、提示用户并尽力保存崩溃快照。"""
        logger.error(f"Tick {tick} 模拟异常: {error}\n{traceback.format_exc()}")
        self._set_status(f"❌ Tick {tick} 模拟异常中断")

        scroll = self.query_one("#event-scroll", VerticalScroll)
        scroll.mount(Static(
            f"[bold red]❌ Tick {tick} 异常中断: {_esc(str(error))}[/bold red]",
            classes="gm-event",
        ))
        scroll.scroll_end(animate=False)
        self.notify(
            f"Tick {tick} 模拟异常: {error}",
            title="模拟中断",
            severity="error",
            timeout=8,
        )

        try:
            from core.save_load import save_simulation_state
            now = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            scene_module = self.scene.__class__.__module__.split(".")[-1]
            crash_path = f"saves/{scene_module}_crash_{now}.json"
            save_simulation_state(
                self.world, self.gm, scene_module, self.scene.name, crash_path
            )
            self.notify(f"崩溃快照已保存: {crash_path}", title="已保存", timeout=5)
        except Exception as save_error:
            logger.error(f"崩溃快照保存失败: {save_error}")

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
                    lines.append(f"  {_esc(r_name)}: {r_info.get('trust', 0)}")
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
