"""Agent 记忆管理：短期记忆 + 摘要压缩 + 关系日志。"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.agent import Agent


class AgentMemory:
    """Agent 记忆系统"""

    def __init__(self, name: str, short_limit: int, compress_threshold: int, relation_limit: int = 3):
        self.name = name
        self.short_limit = short_limit
        self.compress_threshold = compress_threshold
        self.relation_limit = relation_limit

        self._short_term: list[dict] = []
        self._summary: str = ""
        self._relations: dict[str, list[str]] = {}

    def to_dict(self) -> dict:
        """序列化为可保存的 dict"""
        return {
            "short_term": self._short_term,
            "summary": self._summary,
            "relations": self._relations,
        }

    @classmethod
    def from_dict(cls, data: dict, name: str, short_limit: int, compress_threshold: int, relation_limit: int = 3) -> "AgentMemory":
        """从 dict 恢复 AgentMemory"""
        memory = cls(name=name, short_limit=short_limit, compress_threshold=compress_threshold, relation_limit=relation_limit)
        memory._short_term = data.get("short_term", [])
        memory._summary = data.get("summary", "")
        memory._relations = data.get("relations", {})
        return memory

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
                    rel_parts.append(f"{other}: {'; '.join(events[-self.relation_limit:])}")
            if rel_parts:
                parts.append(f"【你对其他人的印象】\n" + "\n".join(rel_parts))

        return "\n\n".join(parts)

    async def compress(self, llm_client: "LLMClient"):
        """压缩短期记忆为摘要"""
        # TODO: 记忆压缩尚未实现，暂不调用 LLM
        pass