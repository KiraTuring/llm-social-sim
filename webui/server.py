"""WebUI 服务：LLM 社会模拟引擎的浏览器前端。

启动::

    python3 webui/server.py                # 默认 http://0.0.0.0:8080
    python3 webui/server.py --port 9000    # 指定端口

打开浏览器访问 http://127.0.0.1:8080 即可。

后端复用 run.py 的世界装配逻辑（load_config / _prepare_world / _setup_services）
与 SimulationEngine，保证 Web 与 CLI/TUI 的模拟行为完全一致。
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import sys
import traceback
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
STATIC_DIR = BASE_DIR / "static"
SAVES_DIR = PROJECT_ROOT / "saves"

# 确保直接以 python webui/server.py 启动时能 import 项目根下的模块。
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

# 复用 CLI 的世界装配与引擎，行为与 run.py / TUI 完全一致。
from run import _prepare_world, _setup_services, load_config  # noqa: E402
from core.engine import SimulationEngine  # noqa: E402
from core.event import SOURCE_AGENT, SOURCE_GM, SOURCE_ICONS, SOURCE_NPC, SOURCE_RULE  # noqa: E402
from core.scene_loader import list_available_scenes  # noqa: E402
from core.save_load import save_simulation_state  # noqa: E402
from render.tui_info import format_scene_sections  # noqa: E402

EVENT_LOG_CAP = 2000   # 快照中事件流最多返回的条数（前端增量渲染）
MESSAGE_CAP = 300      # 快照中消息流最多返回的条数


# --------------------------------------------------------------------------- #
# 请求模型
# --------------------------------------------------------------------------- #
class StartRequest(BaseModel):
    scene: str | None = None
    mode: str | None = None          # "interactive" | "auto"
    max_ticks: int | None = None     # 覆盖 config 的 max_ticks
    load: str | None = None          # 存档文件名（saves/ 下）


class SaveRequest(BaseModel):
    path: str | None = None          # 相对 saves/ 的文件名；为空则自动生成


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #
class SimSession:
    """管理一个进行中的模拟会话。

    状态机：idle -> ready -> running/ready -> done / error
    - ready：已装配世界，等待用户步进
    - running：auto 循环进行中
    - done：剩余 tick 已跑完
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()          # 保护步进操作，避免并发跑 tick
        self._subscribers: set[asyncio.Queue] = set()
        self.reset()

    # -- 生命周期 ---------------------------------------------------------- #
    def reset(self) -> None:
        if getattr(self, "logger", None) is not None:
            try:
                self.logger.close()
            except Exception:
                pass
        self.config: dict | None = None
        self.scene = None
        self.world = None
        self.gm = None
        self.registry = None
        self.engine: SimulationEngine | None = None
        self.logger = None
        self.llm = None
        self.rule_engine = None

        self.start_tick = 0
        self.end_tick = 0
        self.next_tick = 0
        self.mode = "interactive"
        self.status = "idle"
        self.load_path: str | None = None
        self.error: str | None = None

        self._auto_task: asyncio.Task | None = None
        self._stopping = False

    @property
    def ready(self) -> bool:
        return self.world is not None

    @property
    def auto_running(self) -> bool:
        return self._auto_task is not None and not self._auto_task.done()

    def start(self, scene_name: str | None, mode: str | None,
              max_ticks: int | None, load_path: str | None) -> None:
        """装配新会话（新场景或从存档恢复）。失败抛 ValueError。"""
        self._cancel_auto()
        if self.logger is not None:
            self.logger.close()

        config = load_config()
        simulation = config.setdefault("simulation", {})
        if mode in ("interactive", "auto"):
            simulation["mode"] = mode
        self.mode = simulation.get("mode", "interactive")

        self.load_path = load_path
        if load_path:
            load_path = str(SAVES_DIR / load_path)

        try:
            world, scene, gm, registry, start_tick, remaining = _prepare_world(
                config, scene_name, [], load_path, max_ticks,
            )
            logger, llm, rule_engine = _setup_services(config, scene, gm, world)
        except SystemExit as e:  # run.py 装配失败会 sys.exit
            raise ValueError(f"装配失败: {e}") from e
        except Exception as e:
            raise ValueError(f"装配失败: {e}") from e

        engine = SimulationEngine(world, gm, llm, rule_engine, logger, config)

        self.config = config
        self.scene = scene
        self.world = world
        self.gm = gm
        self.registry = registry
        self.engine = engine
        self.logger = logger
        self.llm = llm
        self.rule_engine = rule_engine

        self.start_tick = start_tick
        self.end_tick = start_tick + remaining - 1
        self.next_tick = start_tick
        self.error = None
        self.status = "ready"
        self._stopping = False

    # -- 广播 -------------------------------------------------------------- #
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def _broadcast(self, event: str, data: dict | None = None) -> None:
        payload = {"event": event, "data": data or {}}
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    # -- 步进 -------------------------------------------------------------- #
    async def _finish_current_tick(self) -> None:
        """把已 begin 但未跑完的当前 tick 剩余 agent 全部跑完并 end。"""
        engine = self.engine
        if engine._current_tick is None:
            return
        tick = engine._current_tick
        total = max(1, len(engine.world.action_order))
        while engine.next_agent:
            done = total - len(engine.pending_agents)
            self._broadcast("status", {
                "status": "running",
                "message": f"🤔 {engine.next_agent} 行动中... ({done}/{total})",
            })
            await engine.step_agent()
            self._broadcast("update", {"type": "agent"})
        await engine.end_tick()
        self.next_tick += 1
        self._broadcast("update", {"type": "tick_end", "tick": tick})

    async def _run_full_tick(self) -> bool:
        """完整运行下一个 tick。返回是否推进了 tick。"""
        engine = self.engine
        if engine._current_tick is not None:
            await self._finish_current_tick()
        if self.next_tick > self.end_tick:
            return False

        tick = self.next_tick
        self.status = "running"
        self._broadcast("status", {"status": "running", "message": "📡 GM 生成事件..."})
        await engine.begin_tick(tick)
        self._broadcast("update", {"type": "tick_start", "tick": tick})
        total = max(1, len(engine.world.action_order))
        while engine.next_agent:
            done = total - len(engine.pending_agents)
            self._broadcast("status", {
                "status": "running",
                "message": f"🤔 {engine.next_agent} 行动中... ({done}/{total})",
            })
            await engine.step_agent()
            self._broadcast("update", {"type": "agent"})
        await engine.end_tick()
        self.next_tick += 1
        self._broadcast("update", {"type": "tick_end", "tick": tick})
        self._broadcast("status", {"status": "ready", "message": ""})
        return True

    async def step_agent(self) -> str:
        """单步一个 agent。返回 "agent" / "tick_end" / "done"。"""
        engine = self.engine
        if engine._current_tick is None:
            if self.next_tick > self.end_tick:
                self.status = "done"
                return "done"
            self._broadcast("status", {"status": "running", "message": "📡 GM 生成事件..."})
            await engine.begin_tick(self.next_tick)
            self._broadcast("update", {"type": "tick_start", "tick": self.next_tick})

        total = max(1, len(engine.world.action_order))
        done = total - len(engine.pending_agents)
        if engine.next_agent:
            self._broadcast("status", {
                "status": "running",
                "message": f"🤔 {engine.next_agent} 行动中... ({done}/{total})",
            })
        step = await engine.step_agent()
        if step is not None:
            self._broadcast("update", {"type": "agent", "agent": step.agent_name})
            return "agent"

        await engine.end_tick()
        done_tick = self.next_tick
        self.next_tick += 1
        self._broadcast("update", {"type": "tick_end", "tick": done_tick})
        self._broadcast("status", {"status": "ready", "message": ""})
        return "tick_end"

    # -- auto 模式 --------------------------------------------------------- #
    async def start_auto(self) -> None:
        if self.auto_running:
            return
        self._stopping = False
        self.status = "running"
        self._auto_task = asyncio.create_task(self._auto_loop())
        self._broadcast("status", {"status": "running", "message": "自动运行中…"})

    async def _auto_loop(self) -> None:
        try:
            while not self._stopping:
                advanced = await self._run_full_tick()
                if not advanced:
                    break
                if self._stopping:
                    break
                delay = self.config["simulation"].get("auto_delay", 2) if self.config else 2
                await asyncio.sleep(max(0.0, float(delay)))
            if self.next_tick > self.end_tick:
                self.status = "done"
                self._broadcast("done", {})
            else:
                self.status = "ready"
                self._broadcast("status", {"status": "ready", "message": "已暂停"})
        except Exception as e:  # noqa: BLE001
            self.error = f"{e}\n{traceback.format_exc()}"
            self.status = "error"
            self._broadcast("sim_error", {"message": str(e)})

    def _cancel_auto(self) -> None:
        self._stopping = True
        self._auto_task = None

    def request_stop(self) -> None:
        """请求停止 auto。当前 tick 会在 tick 边界自然结束。"""
        self._stopping = True
        self._broadcast("status", {"status": "stopping", "message": "将在当前 tick 结束后停止…"})

    # -- 存档 -------------------------------------------------------------- #
    def save(self, path: str | None = None) -> str:
        if not self.ready:
            raise ValueError("没有可保存的会话")
        scene_module = self.scene.__class__.__module__.split(".")[-1]
        if not path:
            now = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            path = f"{scene_module}_{now}.json"
        if not path.endswith(".json"):
            path += ".json"
        # 统一写入 saves/ 目录，避免依赖启动时的工作目录
        target = Path(path)
        if not target.is_absolute():
            target = SAVES_DIR / target
        target.parent.mkdir(parents=True, exist_ok=True)
        save_simulation_state(self.world, self.gm, scene_module, self.scene.name, str(target))
        try:
            return str(target.relative_to(SAVES_DIR))
        except ValueError:
            return str(target)

    # -- 快照 -------------------------------------------------------------- #
    def snapshot(self) -> dict:
        if not self.ready:
            return {
                "ok": True,
                "ready": False,
                "status": self.status,
                "scenes": list_available_scenes(),
            }

        world, scene, gm = self.world, self.scene, self.gm

        locations = []
        for loc in world.locations:
            locations.append({
                "name": loc,
                "icon": scene.render_config.get("location_icons", {}).get(loc, "📍"),
                "environment": dict(world.environment.get(loc, {})),
                "interactable": list(world.interactable_keys.get(loc, [])),
                "visible": world.get_visible_locations(loc),
                "adjacent": world.get_adjacent_locations(loc),
                "characters": world.get_characters_in_location(loc),
            })

        characters = []
        for name, ch in world.characters.items():
            is_npc = name in world.npc_names
            d: dict[str, Any] = {
                "name": name,
                "location": ch.location,
                "role": ch.role,
                "personality": ch.personality,
                "goal": ch.goal,
                "states": ch.states,
                "is_npc": is_npc,
            }
            if not is_npc:
                agent = world.agents[name]
                plan = getattr(agent, "manual_plan", None)
                plan = {k: v for k, v in (plan or {}).items()} if plan else None
                d.update({
                    "relationships": agent.relationships,
                    "agent_type": getattr(agent, "agent_type", "Agent"),
                    "recent_memories": agent.recent_memories(8),
                    "memory_summary": getattr(agent.memory, "summary", "") or "",
                    "last_observed_result": agent.last_observed_result or "",
                    "perceived_inbox": agent.perceived_inbox,
                    "writable_states": sorted(agent.writable_states),
                    "private_states": sorted(agent.private_states),
                    "manual_plan": plan,
                })
            characters.append(d)

        events = [e.to_dict() for e in world.event_log]
        event_total = len(events)
        events = events[-EVENT_LOG_CAP:]

        messages = [m.to_dict() for m in world.message_bus.get_all()]
        messages = messages[-MESSAGE_CAP:]

        scheduled = [[e[0], e[1], e[2] if len(e) > 2 else None]
                     for e in getattr(gm, "scheduled_events", [])]

        config_sections = format_scene_sections(scene, world, gm, self.config)

        return {
            "ok": True,
            "ready": True,
            "status": self.status,
            "session": {
                "status": self.status,
                "mode": self.mode,
                "auto_running": self.auto_running,
                "scene_name": scene.name,
                "scene_id": scene.__class__.__module__.split(".")[-1],
                "tick": world.tick,
                "start_tick": self.start_tick,
                "end_tick": self.end_tick,
                "next_tick": self.next_tick,
                "remaining": max(0, self.end_tick - self.next_tick + 1),
                "loaded_from": self.load_path,
                "error": self.error,
            },
            "scene": {
                "name": scene.name,
                "locations": list(world.locations),
                "connections": [list(c) for c in world.connections],
                "visibility": {k: list(v) for k, v in scene.visibility.items()} if scene.visibility else {},
                "world_description": scene.world_description or "",
                "instruction": scene.instruction or "",
                "render_config": scene.render_config or {},
                "interactable_keys": world.interactable_keys or {},
                "npc_names": sorted(world.npc_names),
            },
            "world": {
                "tick": world.tick,
                "locations": locations,
                "characters": characters,
                "action_order": list(world.action_order),
                "event_log": events,
                "event_log_total": event_total,
                "messages": messages,
            },
            "gm": {
                "scheduled_events": scheduled,
                "random_events": list(getattr(gm, "random_events", [])),
                "use_llm": bool(getattr(gm, "use_llm", False)),
                "llm_prompt": (getattr(gm, "llm_prompt", "") or "")[:400],
                "tools": gm.registry.get_action_names() if getattr(gm, "registry", None) else [],
            },
            "tools": {
                "agent": self.registry.get_action_names() if self.registry else [],
            },
            "source_icons": SOURCE_ICONS,
            "action_meta": {
                **(self.registry.get_display_meta() if self.registry else {}),
                **(gm.registry.get_display_meta() if getattr(gm, "registry", None) else {}),
            },
            "config_sections": [
                {"title": title, "body": body} for title, body in config_sections
            ],
        }


# --------------------------------------------------------------------------- #
# 应用
# --------------------------------------------------------------------------- #
app = FastAPI(title="LLM 社会模拟引擎 WebUI", docs_url=None, redoc_url=None)
session = SimSession()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/scenes")
async def api_scenes() -> dict:
    scenes = list_available_scenes()
    default = (session.config or {}).get("scene", "tavern")
    return {"scenes": scenes, "default": default if default in scenes else "tavern"}


@app.get("/api/saves")
async def api_saves() -> dict:
    saves = []
    if SAVES_DIR.exists():
        for f in sorted(SAVES_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            st = f.stat()
            saves.append({
                "name": f.name,
                "path": f.name,
                "size": st.st_size,
                "mtime": st.st_mtime,
                "modified": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
    return {"saves": saves}


@app.get("/api/state")
async def api_state() -> dict:
    return session.snapshot()


@app.post("/api/start")
async def api_start(req: StartRequest) -> dict:
    try:
        session.start(req.scene, req.mode, req.max_ticks, req.load)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    snap = session.snapshot()
    session._broadcast("reset", {})
    if req.mode == "auto" and session.ready:
        asyncio.create_task(session.start_auto())
    return snap


@app.post("/api/next")
async def api_next() -> dict:
    if not session.ready:
        return JSONResponse({"ok": False, "error": "尚未开始会话"}, status_code=400)
    async with session._lock:
        try:
            await session._run_full_tick()
        except Exception as e:  # noqa: BLE001
            session.status = "error"
            session.error = f"{e}\n{traceback.format_exc()}"
            session._broadcast("sim_error", {"message": str(e)})
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    if session.next_tick > session.end_tick:
        session.status = "done"
    else:
        session.status = "ready"
    session._broadcast("update", {"type": "state"})
    return session.snapshot()


@app.post("/api/step")
async def api_step() -> dict:
    if not session.ready:
        return JSONResponse({"ok": False, "error": "尚未开始会话"}, status_code=400)
    async with session._lock:
        try:
            await session.step_agent()
        except Exception as e:  # noqa: BLE001
            session.status = "error"
            session.error = f"{e}\n{traceback.format_exc()}"
            session._broadcast("sim_error", {"message": str(e)})
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    if session.next_tick > session.end_tick:
        session.status = "done"
    else:
        session.status = "ready"
    session._broadcast("update", {"type": "state"})
    return session.snapshot()


@app.post("/api/auto")
async def api_auto() -> dict:
    if not session.ready:
        return JSONResponse({"ok": False, "error": "尚未开始会话"}, status_code=400)
    await session.start_auto()
    return session.snapshot()


@app.post("/api/stop")
async def api_stop() -> dict:
    session.request_stop()
    if not session.auto_running and session.status in ("running", "stopping"):
        session.status = "ready" if session.next_tick <= session.end_tick else "done"
    return session.snapshot()


@app.post("/api/save")
async def api_save(req: SaveRequest) -> dict:
    try:
        path = session.save(req.path)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    return {"ok": True, "path": path}


@app.get("/api/stream")
async def api_stream(request: Request) -> StreamingResponse:
    async def gen():
        q = session.subscribe()
        try:
            yield "event: hello\ndata: {}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                event = payload["event"]
                data = json.dumps(payload["data"], ensure_ascii=False)
                yield f"event: {event}\ndata: {data}\n\n"
        finally:
            session.unsubscribe(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM 社会模拟引擎 WebUI")
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    import uvicorn

    print(f"\n  LLM 社会模拟引擎 WebUI\n  地址: http://127.0.0.1:{args.port}\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
