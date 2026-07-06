"""Textual TUI 应用骨架"""

import asyncio
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Tree, RichLog, Static, Button, Label


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
    Tree, RichLog, Static {
        height: 100%;
    }
    #agent-detail {
        padding: 0 1;
    }
    #scene-label {
        margin: 0 2;
        min-width: 16;
    }
    """

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

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="left-panel"):
                yield Tree("📍 地点", id="location-tree")
            with Vertical(id="center-panel"):
                yield RichLog(id="event-log", highlight=True, markup=True)
            with Vertical(id="right-panel"):
                yield Static("", id="agent-detail")
        with Horizontal(id="controls"):
            yield Label(f"📖 {self.scene.name}", id="scene-label")
            auto_label = "⏸ 暂停" if self._auto_mode else "▶ 自动"
            yield Button(auto_label, id="btn-auto", variant="primary")
            yield Button("⏭ 下一Tick", id="btn-next", variant="default")
            yield Button("Q 退出", id="btn-quit", variant="error")
            yield Label("Tick 0/0", id="tick-label")

    def on_mount(self) -> None:
        if self._auto_mode:
            self._next_event.set()
        self.run_worker(self._simulation_loop(), exclusive=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-next":
            self._next_event.set()
        elif event.button.id == "btn-auto":
            self._toggle_auto()
        elif event.button.id == "btn-quit":
            self.exit()

    def on_key(self, event) -> None:
        if event.key == "space":
            self._next_event.set()
        elif event.key == "a":
            self._toggle_auto()
        elif event.key == "q":
            self.exit()

    def _toggle_auto(self):
        self._auto_mode = not self._auto_mode
        self.query_one("#btn-auto", Button).label = "⏸ 暂停" if self._auto_mode else "▶ 自动"
        if self._auto_mode:
            self._next_event.set()

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

            await self.gm.check_and_inject(
                self.world, llm_client=llm if self.gm.use_llm else None
            )

            agent_actions = {}
            for agent_name in self.world.action_order:
                agent = self.world.agents[agent_name]
                context = await agent.perceive(self.world, llm_client=llm)

                validation_context = self.world.build_validation_context(agent_name)

                action = await agent.think(
                    llm, self.registry, context, tick, validation_context
                )
                await agent.act(action, self.world, self.registry)
                agent_actions[agent_name] = action

            self._update_ui(agent_actions)

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
        log = self.query_one("#event-log", RichLog)
        log.write(f"\n[bold blue]── Tick {self.world.tick} {'─' * 40}[/bold blue]\n")

        tree = self.query_one("#location-tree", Tree)
        tree.clear()
        for loc in self.world.locations:
            icon = self.scene.render_config.get("location_icons", {}).get(loc, "📍")
            agents_here = self.world.get_agents_in_location(loc)
            label = f"{icon} {loc}"
            if agents_here:
                label += f" ({len(agents_here)}人)"
            branch = tree.root.add(label)
            for name in agents_here:
                agent = self.world.agents[name]
                state_parts = [f"{k}:{v}" for k, v in agent.states.items()]
                branch.add_leaf(f"👤 {name}  {' | '.join(state_parts)}")
        tree.root.expand_all()

        for event in self.world.event_log:
            if f"[tick {self.world.tick}]" in event:
                content = event.replace(f"[tick {self.world.tick}] ", "")
                log.write(f"[bold yellow]🎲 GM: {content}[/bold yellow]")

        for name in self.world.action_order:
            action = agent_actions.get(name)
            if not action:
                continue
            line = f"[cyan]{name}[/cyan] → {action.action_type}"
            if action.target:
                line += f" -> [bold]{action.target}[/bold]"
            if action.content:
                truncated = action.content[:80]
                line += f": {truncated}"
            log.write(line)
            if action.result:
                for key, value in action.result.items():
                    label_map = {"observed": "观察"}
                    prefix = label_map.get(key, key)
                    log.write(f"  [green]{prefix}: {value}[/green]")

        log.scroll_end()

        parts = ["[bold]👥 角色详情[/bold]", ""]
        for name in self.world.action_order:
            agent = self.world.agents[name]
            parts.append(f"[bold]{name}[/bold] @ {agent.location}")
            state_str = " | ".join(f"{k}: {v}" for k, v in agent.states.items())
            if state_str:
                parts.append(state_str)
            rels = agent.relationships
            if rels:
                rel_parts = []
                for r_name, r_info in rels.items():
                    rel_parts.append(f"{r_name}({r_info.get('trust', 0)})")
                parts.append("关系: " + ", ".join(rel_parts))
            parts.append("")
        self.query_one("#agent-detail", Static).update("\n".join(parts).strip())

    def _show_summary(self):
        log = self.query_one("#event-log", RichLog)
        log.write("\n[bold green]=== 模拟完成 ===[/bold green]")
        for name in self.world.action_order:
            agent = self.world.agents[name]
            log.write(f"\n[bold]{name}[/bold]")
            rels = agent.relationships
            if rels:
                for r_name, r_info in rels.items():
                    log.write(f"  {r_name}: {r_info.get('trust', 0)}")
            else:
                log.write("  无关系记录")

        if self.save_path:
            from core.save_load import save_simulation_state
            scene_module = self.scene.__class__.__module__.split(".")[-1]
            save_simulation_state(
                self.world, self.gm, scene_module,
                self.scene.name, self.save_path,
            )
