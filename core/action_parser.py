"""text_parse 模式下的 Action 文本解析器。"""

from __future__ import annotations

import json
import re

from core.action import Action


_ACTION_RE = re.compile(r"\[ACTION\](.*?)\[/ACTION\]", re.DOTALL)
_TARGET_RE = re.compile(r"\[TARGET\](.*?)\[/TARGET\]", re.DOTALL)
_CONTENT_RE = re.compile(r"\[CONTENT\](.*?)\[/CONTENT\]", re.DOTALL)
_STATE_RE = re.compile(r"\[STATE\](.*?)\[/STATE\]", re.DOTALL)
_THOUGHT_RE = re.compile(r"\[THOUGHT\](.*?)\[/THOUGHT\]", re.DOTALL)


def parse_action_text(text: str) -> Action | None:
    """从文本解析 Action。

    缺少 [ACTION] 标签时返回 None（视为无法解析，交由调用方重试/兜底），
    避免把任意文本静默当成 speak。
    """
    action_match = _ACTION_RE.search(text)
    if action_match is None:
        return None

    target_match = _TARGET_RE.search(text)
    content_match = _CONTENT_RE.search(text)
    state_match = _STATE_RE.search(text)
    thought_match = _THOUGHT_RE.search(text)

    action_type = action_match.group(1).strip()
    content = content_match.group(1).strip() if content_match else ""
    target = target_match.group(1).strip() if target_match else None
    internal_monologue = thought_match.group(1).strip() if thought_match else ""

    state_update = None
    if state_match:
        try:
            state_update = json.loads(state_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    return Action(
        action_type=action_type,
        target=target,
        content=content,
        internal_monologue=internal_monologue,
        state_update=state_update,
    )
