"""sim_bridge.py 离线协议测试：子进程驱动，ManualAgent 空计划，不触网。

覆盖：命令全集（list_scenes/start/step/state/inject_event/act_as/query_agent/
save/load/quit）、stdout 严格 JSONL、错误健壮性（未知命令/坏 JSON/非法行动
回退 observe）、存档往返后 tick 续跑。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scenarios.tavern import TavernScene

REPO_ROOT = Path(__file__).parent.parent
BRIDGE_SCRIPT = REPO_ROOT / "scripts" / "sim_bridge.py"

TAVERN_AGENTS = [c["name"] for c in TavernScene().agents]


def make_config(log_file: str) -> dict:
    """离线配置（api_key=test_key，不触网）。"""
    return {
        "llm": {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "test_key",
            "response_mode": "tool_call",
        },
        "agent": {
            "prompt_format": "text",
            "memory_short_limit": 10,
            "memory_compress_threshold": 30,
            "content_max_length": 200,
        },
        "gm": {
            "prompt_format": "text",
            "chat_history_max_messages": 40,
            "use_llm": False,
            "random_event_chance": 0.0,
            "llm_event_chance": 0.0,
            "event_tick_window": 3,
        },
        "simulation": {
            "max_ticks": 20,
            "mode": "auto",
            "auto_delay": 0,
            "rotate_order": False,
            "manual_agents": [],
            "manual_file": None,
            "show_full_inbox": True,
            "show_full_monologue": False,
        },
        "logging": {"file": log_file, "level": "WARNING"},
    }


class BridgeProc:
    """一个 sim_bridge.py 子进程 + JSONL 客户端。"""

    def __init__(self, config_path: Path, plan_path: Path):
        self.config_path = config_path
        self.plan_path = plan_path
        self.proc = subprocess.Popen(
            [sys.executable, str(BRIDGE_SCRIPT)],
            cwd=str(REPO_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        )

    def send(self, req: dict) -> dict:
        """发送一个请求并读取对应响应；stdout 每一行都必须是合法 JSON。"""
        self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        assert line, "桥接进程意外退出（stdout 关闭）"
        resp = json.loads(line)  # 非 JSON 直接让测试失败
        assert resp.get("req_id") == req["req_id"], f"req_id 不匹配: {resp}"
        return resp

    def start_tavern(self, req_id: int = 100) -> dict:
        return self.send({
            "req_id": req_id,
            "cmd": "start",
            "scene": "tavern",
            "manual_agents": TAVERN_AGENTS,
            "manual_file": str(self.plan_path),
        })

    def close(self) -> None:
        try:
            self.send({"req_id": "quit", "cmd": "quit"})
        except Exception:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)


@pytest.fixture
def bridge(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(make_config(str(tmp_path / "sim.log")), allow_unicode=True),
        encoding="utf-8",
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text("{}", encoding="utf-8")  # 空计划 → 所有角色空行动（None）
    proc = BridgeProc(config_path, plan_path)
    yield proc
    proc.close()


def test_list_scenes(bridge):
    resp = bridge.send({"req_id": 1, "cmd": "list_scenes"})
    assert resp["ok"] is True
    scenes = resp["data"]["scenes"]
    assert "tavern" in scenes and "murder" in scenes and "spaceship" in scenes


def test_start_step_state(bridge):
    resp = bridge.start_tavern(1)
    assert resp["ok"] is True
    assert resp["data"]["scene"] == "破釜酒馆"
    assert resp["data"]["tick"] == 0
    assert {a["name"] for a in resp["data"]["agents"]} == set(TAVERN_AGENTS)

    resp = bridge.send({"req_id": 2, "cmd": "step", "ticks": 2})
    assert resp["ok"] is True
    assert resp["data"]["tick"] == 2
    assert len(resp["data"]["log"]) == 2
    for tick_log in resp["data"]["log"]:
        # 空计划 → 所有角色无行动，actions 列表为空（不再兜底 observe）
        assert tick_log["actions"] == [], tick_log["actions"]

    resp = bridge.send({"req_id": 3, "cmd": "state"})
    assert resp["ok"] is True
    assert resp["data"]["tick"] == 2
    assert resp["data"]["scene"] == "破釜酒馆"


def test_inject_event_and_act_as(bridge):
    bridge.start_tavern(1)
    resp = bridge.send({"req_id": 2, "cmd": "inject_event", "content": "窗外闪过一道黑影"})
    assert resp["ok"] is True
    resp = bridge.send({
        "req_id": 3,
        "cmd": "act_as",
        "agent": "艾莉娅",
        "action_type": "speak",
        "target": "老巴克",
        "content": "你看见刚才那道黑影了吗？",
    })
    assert resp["ok"] is True and resp["data"]["queued"] is True

    resp = bridge.send({"req_id": 4, "cmd": "step", "ticks": 1})
    assert resp["ok"] is True
    tick_log = resp["data"]["log"][0]
    by_agent = {a["agent"]: a for a in tick_log["actions"]}
    assert by_agent["艾莉娅"]["action_type"] == "speak"
    assert by_agent["艾莉娅"]["target"] == "老巴克"
    assert any(
        m["sender"] == "艾莉娅" and m["msg_type"] == "speech"
        for m in by_agent["艾莉娅"]["messages"]
    )
    # 事件进入世界与消息总线
    state = bridge.send({"req_id": 5, "cmd": "state"})["data"]
    assert any("窗外闪过一道黑影" in m["content"] for m in state["recent_messages"])


def test_act_as_invalid_falls_back_to_observe(bridge):
    bridge.start_tavern(1)
    resp = bridge.send({
        "req_id": 2,
        "cmd": "act_as",
        "agent": "艾莉娅",
        "action_type": "no_such_action",
        "content": "x",
    })
    assert resp["ok"] is True  # 排队成功，执行时回退
    resp = bridge.send({"req_id": 3, "cmd": "step", "ticks": 1})
    by_agent = {a["agent"]: a for a in resp["data"]["log"][0]["actions"]}
    assert by_agent["艾莉娅"]["action_type"] == "observe"


def test_query_agent(bridge):
    bridge.start_tavern(1)
    resp = bridge.send({"req_id": 2, "cmd": "query_agent", "agent": "老巴克"})
    assert resp["ok"] is True
    data = resp["data"]
    assert data["name"] == "老巴克"
    assert data["role"] == "酒馆老板"
    assert data["personality"]
    assert data["goal"]
    assert "艾莉娅" in data["relationships"]


def test_list_actions_and_snapshot_actions(bridge):
    bridge.start_tavern(1)
    resp = bridge.send({"req_id": 2, "cmd": "list_actions"})
    assert resp["ok"] is True
    actions = resp["data"]["actions"]
    assert "speak" in actions and "move" in actions

    state = bridge.send({"req_id": 3, "cmd": "state"})["data"]
    assert "speak" in state["available_actions"]


def test_save_load_roundtrip_continues_tick(bridge, tmp_path):
    bridge.start_tavern(1)
    bridge.send({"req_id": 2, "cmd": "step", "ticks": 1})
    save_path = tmp_path / "save.json"
    resp = bridge.send({"req_id": 3, "cmd": "save", "path": str(save_path)})
    assert resp["ok"] is True
    assert save_path.exists()

    resp = bridge.send({"req_id": 4, "cmd": "load", "path": str(save_path)})
    assert resp["ok"] is True
    assert resp["data"]["tick"] == 1
    assert resp["data"]["scene"] == "破釜酒馆"

    resp = bridge.send({"req_id": 5, "cmd": "step", "ticks": 1})
    assert resp["ok"] is True
    assert resp["data"]["tick"] == 2  # 存档后从 tick 2 续跑


def test_unknown_command_and_bad_json_keep_process_alive(bridge):
    resp = bridge.send({"req_id": 1, "cmd": "no_such_cmd"})
    assert resp["ok"] is False
    assert "未知命令" in resp["error"]

    # 直接喂一行坏 JSON：桥接应回显原文作为关联键，且进程存活
    bridge.proc.stdin.write("这不是 JSON\n")
    bridge.proc.stdin.flush()
    line = bridge.proc.stdout.readline()
    resp = json.loads(line)
    assert resp["ok"] is False

    resp = bridge.send({"req_id": 3, "cmd": "list_scenes"})
    assert resp["ok"] is True  # 进程仍然健康


def test_step_returns_gm_and_agent_events(bridge):
    """P0：sim_step 的 log[].events 应包含 GM 事件与 Player 消息（修复前缺失）。"""
    bridge.start_tavern(1)
    # act_as 让艾莉娅说话 → agent 事件应进入 events
    bridge.send({
        "req_id": 2,
        "cmd": "act_as",
        "agent": "艾莉娅",
        "action_type": "speak",
        "target": "雷恩",
        "content": "雷恩，你信那斗篷客的话吗？",
    })
    # act_as 让老巴克观察 → 验证 observe 的 result 仍随 actions 返回（不再由空计划兜底）
    bridge.send({
        "req_id": 3,
        "cmd": "act_as",
        "agent": "老巴克",
        "action_type": "observe",
    })
    resp = bridge.send({"req_id": 4, "cmd": "step", "ticks": 1})
    assert resp["ok"] is True
    tick1 = resp["data"]["log"][0]
    assert "events" in tick1
    agent_events = [e for e in tick1["events"] if e["kind"] == "agent"]
    assert any(
        e["sender"] == "艾莉娅" and e["msg_type"] == "speech" and e["target"] == "雷恩"
        for e in agent_events
    )
    # actions 应携带 result（如 observe 的观察结果），修复前缺失
    for action in tick1["actions"]:
        assert "result" in action
    observe_actions = [a for a in tick1["actions"] if a["action_type"] == "observe"]
    assert observe_actions and all(a["result"] for a in observe_actions)

    # 推进到 tick 3：tavern 计划事件（GM system_event）应进入 events
    resp = bridge.send({"req_id": 5, "cmd": "step", "ticks": 2})
    assert resp["ok"] is True
    assert resp["data"]["tick"] == 3
    tick3 = resp["data"]["log"][1]
    gm_events = [e for e in tick3["events"] if e["kind"] == "gm"]
    assert any("闷雷" in e["content"] for e in gm_events)


def test_step_narrative_view(bridge):
    """P0：view=narrative 时 cmd_step 返回可读剧情文本。"""
    bridge.start_tavern(1)
    bridge.send({
        "req_id": 2,
        "cmd": "act_as",
        "agent": "艾莉娅",
        "action_type": "speak",
        "target": "雷恩",
        "content": "雷恩，你信那斗篷客的话吗？",
    })
    # act_as 让老巴克观察 → 渲染 observe 结果（空计划不再兜底 observe）
    bridge.send({
        "req_id": 3,
        "cmd": "act_as",
        "agent": "老巴克",
        "action_type": "observe",
    })
    resp = bridge.send({"req_id": 4, "cmd": "step", "ticks": 1, "view": "narrative"})
    assert resp["ok"] is True
    narrative = resp["data"]["narrative"]
    assert isinstance(narrative, str)
    assert "Tick 1" in narrative
    assert "艾莉娅" in narrative
    assert "你信那斗篷客的话吗？" in narrative
    # observe 的 result 应渲染进 narrative（如 "📊 观察: ..."）
    assert "📊 观察" in narrative

    # raw 视图（默认）不生成 narrative 字段
    resp = bridge.send({"req_id": 5, "cmd": "step", "ticks": 1})
    assert resp["ok"] is True
    assert "narrative" not in resp["data"]

    # 非法 view 报错
    resp = bridge.send({"req_id": 6, "cmd": "step", "ticks": 1, "view": "bogus"})
    assert resp["ok"] is False


def test_extract_state_events_pure_function(monkeypatch):
    """state 事件提取纯函数：只挑纯状态条目，过滤旁白/NPC 台词/裸随机事件。"""
    monkeypatch.syspath_prepend(str(REPO_ROOT / "scripts"))
    import sim_bridge

    delta = [
        "[tick 5] 新 NPC 出现: 披斗篷的陌生人（游方旅人）在角落",
        "[tick 5] NPC 老游吟诗人: 这鬼天气，雪下得比往年都早。",
        "[tick 5] 旁白: 壁炉里的火噼啪作响",
        "[tick 5] 环境变更: 主厅.喧闹程度 → 紧张",
        "[tick 5] 角色状态: 雷恩.精力 → 80",
        "[tick 5] NPC 阿福 离开了",
        "[tick 5] 屋外传来一声闷雷，似乎要下雨了",
    ]
    out = sim_bridge._extract_state_events(delta, 5)
    contents = [e["content"] for e in out]
    assert "新 NPC 出现: 披斗篷的陌生人（游方旅人）在角落" in contents
    assert "环境变更: 主厅.喧闹程度 → 紧张" in contents
    assert "角色状态: 雷恩.精力 → 80" in contents
    assert "NPC 阿福 离开了" in contents
    # 双写/NPC 台词/裸事件应被过滤（它们在 message_bus 里已有，避免重复）
    assert not any("老游吟诗人" in c for c in contents)
    assert not any("旁白" in c for c in contents)
    assert not any("闷雷" in c for c in contents)
    for e in out:
        assert e["kind"] == "state"
        assert e["tick"] == 5
        assert e["sender"] == "GM"
        assert e["msg_type"] == "system_event"


def test_tavern_scene_prompt_and_states():
    """tavern 场景配置：instruction 强调 state_update，且不设精力。"""
    scene = TavernScene()
    assert "state_update" in scene.instruction
    assert "情绪" in scene.instruction
    assert "情绪" in scene.states
    assert "精力" not in scene.states
    assert scene.writable_states == ["情绪"]


def test_act_as_state_update_applies_writable_only(bridge):
    """state_update 应用：只写 writable_states（情绪），忽略不可写字段（金钱）。"""
    bridge.start_tavern(1)
    bridge.send({
        "req_id": 2,
        "cmd": "act_as",
        "agent": "艾莉娅",
        "action_type": "interact",
        "content": "抱紧木盒",
        "params": {"state_update": {"情绪": "紧张"}},
    })
    resp = bridge.send({"req_id": 3, "cmd": "step", "ticks": 1})
    assert resp["ok"] is True
    state = bridge.send({"req_id": 4, "cmd": "state"})["data"]
    a = next(x for x in state["agents"] if x["name"] == "艾莉娅")
    assert a["states"]["情绪"] == "紧张"

    # 不可写字段（inventory 钱包，private 且不在 writable_states）应被忽略
    bridge.send({
        "req_id": 5,
        "cmd": "act_as",
        "agent": "艾莉娅",
        "action_type": "interact",
        "content": "数了数钱",
        "params": {"state_update": {"inventory": {"金钱": 999}}},
    })
    resp = bridge.send({"req_id": 6, "cmd": "step", "ticks": 1})
    assert resp["ok"] is True
    state = bridge.send({"req_id": 7, "cmd": "state"})["data"]
    a = next(x for x in state["agents"] if x["name"] == "艾莉娅")
    assert a["states"]["inventory"]["金钱"] == 30
