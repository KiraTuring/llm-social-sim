"""Agent 记忆管理：短期记忆 + 摘要压缩 + 关系日志。"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.agent import Agent


class AgentMemory:
    """Agent 记忆系统"""

    def __init__(self, name: str, short_limit: int, compress_threshold: int):
        self.name = name
        self.short_limit = short_limit
        self.compress_threshold = compress_threshold

        self._short_term: list[dict] = []
        self._summary: str = ""
        self._relations: dict[str, list[str]] = {}

    def add(self, event: str, agent_name: str | None = None):
        """添加记忆事件"""
        if agent_name:
            if agent_name not in self._relations:
                self._relations[agent_name] = []
            self._relations[agent_name].append(event)
        else:
            self._short_term.append({"tick": 0, "event": event})

        if len(self._short_term) >= self.compress_threshold:
            self._compress_needed = True

    def get_context(self) -> str:
        """获取记忆上下文用于 prompt"""
        parts = []

        if self._summary:
            parts.append(f"【你的过去】\n{self._summary}")

        if self._short_term:
            recent = "\n".join([f"- {e['event']}" for e in self._short_term[-self.short_limit :]])
            parts.append(f"【你最近记得的事】\n{recent}")

        if self._relations:
            rel_parts = []
            for other, events in self._relations.items():
                if events:
                    rel_parts.append(f"{other}: {'; '.join(events[-3:])}")
            if rel_parts:
                parts.append(f"【你对其他人的印象】\n" + "\n".join(rel_parts))

        return "\n\n".join(parts)

    async def compress(self, llm_client: "LLMClient"):
        """压缩短期记忆为摘要"""
        if not self._short_term:
            return

        events_text = "\n".join([e["event"] for e in self._short_term])

        prompt = f"""以下是 {self.name} 最近的记忆事件，请用 2-3 句话总结关键信息：

{events_text}

输出简洁的摘要，不要包含格式标记。"""

        messages = [{"role": "user", "content": prompt}]

        try:
            summary, _ = await llm_client.call(
                system_prompt="你是记忆助手，负责总结记忆。",
                messages=messages,
                action_registry=None,
            )
            if summary:
                if self._summary:
                    self._summary = f"{self._summary}\n\n{summary}"
                else:
                    self._summary = summary
                self._short_term = []
        except Exception as e:
            print(f"[{self.name}] 记忆压缩失败: {e}")