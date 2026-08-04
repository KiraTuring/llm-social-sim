"""手动控制 Agent：从 JSON 文件读取行动，不调用 LLM。"""

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from core.agent import Agent

if TYPE_CHECKING:
    from core.action import ActionRegistry

_DEFAULT_MANUAL_FILE = Path(__file__).parent.parent / "manual_actions.json"
_WILDCARD_TICK = "*"
_log = logging.getLogger("simulation")


class ManualAgent(Agent):
    """手动控制的 Agent，行动从 JSON 文件读取。

    JSON 结构（每个 tick 一条行动，可为空对象）：
    {
        "角色名": {
            "1": {"action_type": "speak", "target": "目标", "content": "内容",
                  "internal_monologue": "内心独白"},
            "*": {"action_type": "observe", "content": "通配 tick，未单独配置时重复执行"}
        }
    }

    文件缺失或格式错误会在启动时直接报错；单个行动非法（未知 action_type、
    target 不可达等）时记 warning 并回退为 observe，与 LLM 路径行为一致。
    """

    def __init__(self, **kwargs):
        file_path = kwargs.pop("file_path", None)
        self._manual_file = Path(file_path) if file_path else _DEFAULT_MANUAL_FILE
        self._manual_plan = self._load_plan()
        super().__init__(**kwargs)

    def _load_plan(self) -> dict:
        """加载并校验手动控制计划，文件缺失/格式错误直接抛错。"""
        if not self._manual_file.exists():
            raise FileNotFoundError(
                f"手动控制文件不存在: {self._manual_file}。"
                f"请创建该文件或使用 --manual-file 指定，可参考 manual_actions.example.json"
            )
        try:
            data = json.loads(self._manual_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(
                f"手动控制文件 JSON 解析失败: {self._manual_file}: {e}"
            ) from e

        if not isinstance(data, dict):
            raise ValueError(
                f"手动控制文件格式错误（应为 {{角色名: {{tick: 行动}}}}）: {self._manual_file}"
            )
        for name, plan in data.items():
            if not isinstance(plan, dict):
                raise ValueError(
                    f"角色 '{name}' 的手动计划应为对象: {self._manual_file}"
                )
            for tick, entry in plan.items():
                if not isinstance(entry, dict):
                    raise ValueError(
                        f"角色 '{name}' tick '{tick}' 的行动应为对象: {self._manual_file}"
                    )
                if not entry.get("action_type"):
                    raise ValueError(
                        f"角色 '{name}' tick '{tick}' 缺少 action_type: {self._manual_file}"
                    )
        return data

    async def think(
        self,
        llm,
        registry: "ActionRegistry",
        context: str,
        tick: int = 0,
        validation_context: dict | None = None,
    ):
        from core.action import Action

        action = self._read_action(tick, registry, validation_context)
        if action:
            return action

        return Action(action_type="observe", content="等待指令", internal_monologue="...")

    def _read_action(
        self,
        tick: int,
        registry: "ActionRegistry",
        validation_context: dict | None = None,
    ):
        """读取指定 tick 的行动；行动非法时记 warning 并返回 None（回退 observe）。"""
        plan = self._manual_plan.get(self.name, {})
        entry = plan.get(str(tick))
        if entry is None:
            entry = plan.get(_WILDCARD_TICK)
        if not entry:
            return None

        entry = dict(entry)
        entry.setdefault("action_type", "observe")
        entry.setdefault("content", "")
        entry.setdefault("target", "")
        entry.setdefault("internal_monologue", "")

        action_type = entry["action_type"]
        spec = registry.get(action_type)
        if spec is None:
            _log.warning(
                f"[ManualAgent] {self.name} tick {tick}: 未知行动类型 "
                f"'{action_type}'，回退为 observe"
            )
            return None

        params = {
            "target": entry.get("target"),
            "content": entry.get("content"),
            **entry.get("params", {}),
        }
        if validation_context is not None:
            error = spec.validate_params(params, validation_context)
            if error:
                _log.warning(
                    f"[ManualAgent] {self.name} tick {tick}: 行动 '{action_type}' "
                    f"不合法（{error}），回退为 observe"
                )
                return None

        return registry.parse_text(
            f"[ACTION]{action_type}[/ACTION]\n"
            f"[TARGET]{entry['target']}[/TARGET]\n"
            f"[CONTENT]{entry['content']}[/CONTENT]\n"
            f"[THOUGHT]{entry['internal_monologue']}[/THOUGHT]"
        )
