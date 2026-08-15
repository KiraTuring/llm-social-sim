#!/usr/bin/env python3
"""模拟桥接进程：DSH 动态插件通过 stdin/stdout JSONL 控制本仓库模拟引擎。

协议（NDJSON）:
  请求:  {"req_id": <任意 JSON>, "cmd": "start|step|state|...", ...}
  响应:  {"req_id": <原样回显>, "ok": true, "data": {...}}
        或 {"req_id": <原样回显>, "ok": false, "error": "..."}

约束:
  - stdout 只输出 JSONL 响应；引擎内部的 print 噪声（[LLM]/[角色名] 状态行）
    在命令执行期间重定向到 stderr，写响应前恢复。
  - 命令严格串行处理（引擎本身是异步单线程）。
  - 任何异常都转为 {"ok": false} 并保持进程存活（quit 除外）。

复用 run.py 的装配逻辑（load_config/_prepare_world/_setup_services），
保证与 CLI/TUI 的世界初始化、Action 注册、GM 构建完全一致。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from run import (  # noqa: E402  (run.main 有 __main__ 守卫，导入安全)
    _load_world,
    _prepare_world,
    _setup_services,
    load_config,
)
from core.action import Action  # noqa: E402
from core.engine import SimulationEngine  # noqa: E402
from core.scene_loader import list_available_scenes  # noqa: E402


def _log(line: str) -> None:
    """诊断日志一律走 stderr，绝不污染 stdout JSONL 通道。"""
    print(f"[sim_bridge] {line}", file=sys.stderr, flush=True)


class SimBridge:
    """持有一个模拟运行上下文，串行响应命令。"""

    def __init__(self):
        self.world = None
        self.scene = None
        self.gm = None
        self.registry = None
        self.engine = None
        self.llm = None
        self.rule_engine = None
        self.logger = None
        self.config = None
        self._next_tick = 1
        self._pending_actions: dict[str, dict] = {}

    # ---------- 命令实现 ----------

    def cmd_list_scenes(self, args: dict) -> dict:
        return {"scenes": list_available_scenes()}

    def cmd_list_actions(self, args: dict) -> dict:
        """列出当前场景注册给角色的全部行动类型（供 sim_act_as 选用）。"""
        self._require_world()
        return {"actions": self.registry.get_action_names()}

    def cmd_start(self, args: dict) -> dict:
        scene_name = args.get("scene")
        if not scene_name:
            raise ValueError("缺少 scene 参数")
        config_path = args.get("config_path") or str(REPO_ROOT / "config.yaml")
        config = load_config(config_path)

        manual_agents = args.get("manual_agents")
        manual_file = args.get("manual_file")
        if manual_agents is not None:
            config.setdefault("simulation", {})["manual_agents"] = manual_agents
        if manual_file is not None:
            config.setdefault("simulation", {})["manual_file"] = manual_file

        # 手动文件提前校验：_init_world 对坏文件会 sys.exit(1) 杀进程
        mf = config.get("simulation", {}).get("manual_file")
        if config.get("simulation", {}).get("manual_agents") and mf:
            path = Path(mf)
            if not path.exists():
                raise FileNotFoundError(f"手动控制文件不存在: {mf}")
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise ValueError(f"手动控制文件 JSON 解析失败: {mf}: {e}")

        world, scene, gm, registry, start_tick, _ = _prepare_world(
            config, scene_name, None, None, None
        )
        return self._adopt(config, world, scene, gm, registry, start_tick, was_load=False)

    def cmd_load(self, args: dict) -> dict:
        path = args.get("path")
        if not path:
            raise ValueError("缺少 path 参数")
        config = load_config(args.get("config_path") or str(REPO_ROOT / "config.yaml"))
        world, scene, gm, registry, start_tick = _load_world(path, config, None)
        return self._adopt(config, world, scene, gm, registry, start_tick, was_load=True)

    async def cmd_step(self, args: dict) -> dict:
        self._require_world()
        ticks = int(args.get("ticks", 1))
        if ticks < 1:
            raise ValueError("ticks 必须 >= 1")
        log = []
        for _ in range(ticks):
            tick_log = await self._run_one_tick()
            log.append(tick_log)
        return {"ticks_run": len(log), "tick": self.world.tick, "log": log}

    def cmd_state(self, args: dict) -> dict:
        self._require_world()
        return self._snapshot()

    def cmd_inject_event(self, args: dict) -> dict:
        self._require_world()
        content = args.get("content")
        if not content:
            raise ValueError("缺少 content 参数")
        env = args.get("environment") or {}
        if env:
            loc = env.get("location")
            key = env.get("key")
            value = env.get("value")
            if not (loc and key):
                raise ValueError("environment 需要 location 和 key")
            err = self.world.update_environment(loc, key, value)
            if err:
                raise ValueError(err)
        self.world.add_event(content)
        self.gm._broadcast_event(content, self.world)
        _log(f"GM 事件注入: {content}")
        return {"ok": True, "tick": self.world.tick}

    def cmd_act_as(self, args: dict) -> dict:
        self._require_world()
        agent = args.get("agent")
        action_type = args.get("action_type")
        if not agent or not action_type:
            raise ValueError("需要 agent 和 action_type")
        if agent not in self.world.agents:
            raise ValueError(f"'{agent}' 不是可操控的 Player（可选: {', '.join(self.world.agents)}）")
        entry = {
            "action_type": action_type,
            "target": args.get("target"),
            "content": args.get("content", ""),
            "internal_monologue": args.get("internal_monologue", ""),
            "params": dict(args.get("params") or {}),
        }
        self._pending_actions[agent] = entry
        return {"queued": True, "agent": agent, "tick": self.world.tick}

    def cmd_query_agent(self, args: dict) -> dict:
        self._require_world()
        agent = args.get("agent")
        if not agent:
            raise ValueError("缺少 agent 参数")
        a = self.world.agents.get(agent)
        if a is None:
            raise ValueError(f"角色 '{agent}' 不存在（可用: {', '.join(self.world.agents)}）")
        return {
            "name": a.name,
            "role": a.role,
            "personality": a.personality,
            "goal": a.goal,
            "location": a.location,
            "relationships": dict(a.relationships or {}),
            "states": dict(a.states),
            "recent_memories": a.recent_memories(5),
            "last_observed_result": a.last_observed_result,
        }

    def cmd_save(self, args: dict) -> dict:
        self._require_world()
        from core.save_load import save_simulation_state

        path = args.get("path") or str(REPO_ROOT / "saves" / "bridge_run.json")
        scene_module = self.scene.__class__.__module__.split(".")[-1]
        save_simulation_state(self.world, self.gm, scene_module, self.scene.name, path)
        return {"ok": True, "path": path, "tick": self.world.tick}

    def cmd_quit(self, args: dict) -> dict:
        if self.logger is not None:
            try:
                self.logger.close()
            except Exception:
                pass
        return {"ok": True}

    # ---------- 内部 ----------

    def _adopt(self, config, world, scene, gm, registry, start_tick, *, was_load) -> dict:
        # 替换旧运行：关闭旧 logger，避免句柄泄漏
        if self.logger is not None:
            try:
                self.logger.close()
            except Exception:
                pass
        logger, llm, rule_engine = _setup_services(config, scene, gm, world)
        engine = SimulationEngine(world, gm, llm, rule_engine, logger, config)

        self.config = config
        self.world = world
        self.scene = scene
        self.gm = gm
        self.registry = registry
        self.engine = engine
        self.llm = llm
        self.rule_engine = rule_engine
        self.logger = logger
        self._next_tick = start_tick
        self._pending_actions = {}
        _log(
            f"{'载入存档' if was_load else '启动场景'} [{scene.name}] "
            f"tick={world.tick} 下一tick={start_tick} 角色={list(world.agents)}"
        )
        return self._snapshot()

    def _require_world(self) -> None:
        if self.engine is None:
            raise RuntimeError("尚未启动模拟：请先调用 start 或 load")

    def _snapshot(self) -> dict:
        world = self.world
        locs: dict[str, list[str]] = {}
        for name, ch in world.characters.items():
            locs.setdefault(ch.location, []).append(name)
        agents = [
            {
                "name": name,
                "role": a.role,
                "location": a.location,
                "states": dict(a.states),
            }
            for name, a in world.agents.items()
        ]
        npcs = [
            {"name": name, "location": world.npcs[name].location}
            for name in world.npcs
        ]
        recent = [
            {
                "tick": m.tick,
                "sender": m.sender,
                "target": m.target,
                "content": m.content,
                "msg_type": m.msg_type,
            }
            for m in world.message_bus.get_recent(20)
        ]
        return {
            "tick": world.tick,
            "scene": self.scene.name,
            "locations": list(world.locations),
            "characters_by_location": locs,
            "agents": agents,
            "npcs": npcs,
            "recent_messages": recent,
            "event_log_tail": world.event_log[-10:],
            "action_order": list(world.action_order),
            "available_actions": self.registry.get_action_names(),
            "pending_actions": list(self._pending_actions.keys()),
        }

    async def _run_one_tick(self) -> dict:
        """手动驱动 begin_tick → step_agent → end_tick，支持 act_as 强制行动。"""
        tick = self._next_tick
        engine = self.engine
        self._steps: list = []

        await engine.begin_tick(tick)
        while True:
            agent_name = engine.next_agent
            if agent_name is None:
                break
            if agent_name in self._pending_actions:
                self._install_forced_think(agent_name)
            step = await engine.step_agent()
            if step is None:
                break
            self._steps.append(step)
        await engine.end_tick()
        self._next_tick += 1

        actions = []
        for step in self._steps:
            action = step.action
            if action is None:
                continue
            actions.append({
                "agent": step.agent_name,
                "action_type": action.action_type,
                "target": action.target,
                "content": action.content,
                "internal_monologue": action.internal_monologue,
                "messages": [
                    {
                        "sender": m.sender,
                        "recipients": list(m.recipients) if m.recipients else [],
                        "target": m.target,
                        "content": m.content,
                        "msg_type": m.msg_type,
                        "tick": m.tick,
                    }
                    for m in step.messages
                ],
            })
        return {"tick": tick, "actions": actions}

    def _install_forced_think(self, agent_name: str) -> None:
        """给指定 agent 装一次性 think 覆盖：返回 act_as 排队行动（校验后）。

        步进完成后自动还原原 think。行动非法时回退 observe（与 ManualAgent 一致）。
        """
        agent = self.world.agents[agent_name]
        entry = self._pending_actions.pop(agent_name, None)
        if entry is None:
            return
        original = agent.think

        async def forced_think(llm, context, tick=0, validation_context=None):
            agent.think = original  # 一次性，用后还原
            vctx = validation_context or self.world.build_validation_context(agent_name)
            action = Action(
                action_type=entry["action_type"],
                target=entry.get("target") or None,
                content=entry.get("content", ""),
                internal_monologue=entry.get("internal_monologue", ""),
                params=dict(entry.get("params") or {}),
            )
            spec = self.registry.get(action.action_type)
            error = None
            if spec is None:
                error = f"行动类型 '{action.action_type}' 不存在，可用: {', '.join(self.registry.get_action_names())}"
            else:
                params = {"target": action.target, "content": action.content, **action.params}
                error = spec.validate_params(params, vctx)
            if error:
                _log(f"[act_as] {agent_name} 行动不合法（{error}），回退 observe")
                return Action(action_type="observe", content="观察四周", internal_monologue="...")
            _log(f"[act_as] {agent_name} tick {tick}: {action.action_type}")
            return action

        agent.think = forced_think


def _parse_request(line: str) -> dict:
    data = json.loads(line)
    if not isinstance(data, dict):
        raise ValueError("请求必须是 JSON 对象")
    if "cmd" not in data:
        raise ValueError("缺少 cmd 字段")
    return data


async def _main_loop() -> None:
    bridge = SimBridge()

    def read_line():
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        return line.decode("utf-8", errors="replace")

    while True:
        line = await asyncio.to_thread(read_line)
        if line is None:
            _log("stdin 关闭，退出")
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = _parse_request(line)
        except (json.JSONDecodeError, ValueError) as e:
            # 无法解析 req_id，回显原始行作为关联键
            print(json.dumps({"req_id": line[:64], "ok": False, "error": f"请求解析失败: {e}"}, ensure_ascii=False), flush=True)
            continue

        req_id = req.get("req_id")
        cmd = req["cmd"]
        handler = getattr(bridge, f"cmd_{cmd}", None)
        if handler is None:
            print(json.dumps({"req_id": req_id, "ok": False, "error": f"未知命令: {cmd}"}, ensure_ascii=False), flush=True)
            continue

        try:
            # 执行期间把 print 噪声重定向到 stderr，保证 stdout 纯净 JSONL
            with contextlib.redirect_stdout(sys.stderr):
                data = handler(req)
                if asyncio.iscoroutine(data):
                    data = await data
            print(json.dumps({"req_id": req_id, "ok": True, "data": data}, ensure_ascii=False), flush=True)
        except SystemExit as e:
            print(json.dumps({"req_id": req_id, "ok": False, "error": f"内部退出(exit {e.code})"}, ensure_ascii=False), flush=True)
        except Exception as e:
            _log(f"命令 {cmd} 失败: {e!r}")
            print(json.dumps({"req_id": req_id, "ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False), flush=True)

        if cmd == "quit":
            break


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(_main_loop())


if __name__ == "__main__":
    main()
