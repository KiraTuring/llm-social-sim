"""手动控制 Agent：从 JSON 文件读取行动，不调用 LLM。"""

import json
from pathlib import Path
from typing import TYPE_CHECKING

from core.agent import Agent

if TYPE_CHECKING:
    from core.action import ActionRegistry

MANUAL_FILE = Path(__file__).parent.parent / "manual_actions.json"


class ManualAgent(Agent):
    """手动控制的 Agent，行动从 manual_actions.json 读取"""

    async def think(self, llm, registry: "ActionRegistry", context: str, tick: int = 0):
        from core.action import Action

        action = self._read_action(tick, registry)
        if action:
            return action

        return Action(action_type="observe", content="等待指令", internal_monologue="...")

    def _read_action(self, tick: int, registry: "ActionRegistry"):
        """从 JSON 文件读取指定 tick 的行动"""
        if not MANUAL_FILE.exists():
            return None

        try:
            data = json.loads(MANUAL_FILE.read_text())
        except (json.JSONDecodeError, Exception):
            return None

        agent_actions = data.get(self.name, {})
        entry = agent_actions.get(str(tick))
        if not entry:
            return None

        entry.setdefault("action_type", "observe")
        entry.setdefault("content", "")

        action = registry.parse_text(
            f"[ACTION]{entry['action_type']}[/ACTION]\n"
            f"[TARGET]{entry.get('target', '')}[/TARGET]\n"
            f"[CONTENT]{entry['content']}[/CONTENT]\n"
            f"[THOUGHT]{entry.get('internal_monologue', '')}[/THOUGHT]"
        )
        return action