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
            "inbox_limit": 5,
        },
        "gm": {
            "prompt_format": "text",
            "chat_history_max_messages": 40,
            "use_llm": False,
            "random_event_chance": 0.0,
            "llm_event_chance": 0.0,
            "message_limit": 5,
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
    plan_path.write_text("{}", encoding="utf-8")  # 空计划 → 所有角色 observe
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
        agents = {a["agent"] for a in tick_log["actions"]}
        assert agents == set(TAVERN_AGENTS)
        for action in tick_log["actions"]:
            assert action["action_type"] == "observe"  # ManualAgent 空计划兜底

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
