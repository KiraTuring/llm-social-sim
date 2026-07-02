"""手动控制 Agent：从 JSON 文件读取行动，不调用 LLM。"""

import json
from pathlib import Path
from typing import TYPE_CHECKING

from core.agent import Agent

if TYPE_CHECKING:
    from core.action import ActionRegistry

_DEFAULT_MANUAL_FILE = Path(__file__).parent.parent / "manual_actions.json"


class ManualAgent(Agent):
    """手动控制的 Agent，行动从 manual_actions.json 读取"""

    def __init__(self, **kwargs):
        self._manual_file = Path(kwargs.pop("file_path", None)) if kwargs.get("file_path") else _DEFAULT_MANUAL_FILE
        super().__init__(**kwargs)

    async def think(self, llm, registry: "ActionRegistry", context: str, tick: int = 0, validation_context: dict | None = None):
        from core.action import Action

        action = self._read_action(tick, registry)
        if action:
            return action

        return Action(action_type="observe", content="等待指令", internal_monologue="...")

    def _read_action(self, tick: int, registry: "ActionRegistry"):
        """从 JSON 文件读取指定 tick 的行动"""
        if not self._manual_file.exists():
            return None

        try:
            data = json.loads(self._manual_file.read_text())
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