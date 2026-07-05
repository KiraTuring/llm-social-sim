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
        self._compress_needed = False

    def to_dict(self) -> dict:
        """序列化为可保存的 dict"""
        return {
            "short_term": self._short_term,
            "summary": self._summary,
            "relations": self._relations,
            "compress_needed": self._compress_needed,
        }

    @classmethod
    def from_dict(cls, data: dict, name: str, short_limit: int, compress_threshold: int, relation_limit: int = 3) -> "AgentMemory":
        """从 dict 恢复 AgentMemory"""
        memory = cls(name=name, short_limit=short_limit, compress_threshold=compress_threshold, relation_limit=relation_limit)
        memory._short_term = data.get("short_term", [])
        memory._summary = data.get("summary", "")
        memory._relations = data.get("relations", {})
        memory._compress_needed = data.get("compress_needed", False)
        return memory

    def add(self, event: str, agent_name: str | None = None, tick: int = 0):
        """添加记忆事件"""
        if agent_name:
            if agent_name not in self._relations:
                self._relations[agent_name] = []
            self._relations[agent_name].append(event)
        else:
            self._short_term.append({"tick": tick, "event": event})

        if len(self._short_term) >= self.compress_threshold:
            self._compress_needed = True

    def get_context(self) -> str:
        """获取记忆上下文用于 prompt"""
        parts = []

        if self._summary:
            parts.append(f"【你的过去】\n{self._summary}")

        if self._short_term:
            recent = "\n".join([f"- {e['event']}" for e in self._short_term])
            parts.append(f"【你最近记得的事】\n{recent}")

        if self._relations:
            rel_parts = []
            for other, events in self._relations.items():
                if events:
                    rel_parts.append(f"{other}: {'; '.join(events[-self.relation_limit:])}")
            if rel_parts:
                parts.append(f"【你对其他人的印象】\n" + "\n".join(rel_parts))

        return "\n\n".join(parts)

    async def compress(self, llm_client: "LLMClient", relationships: dict | None = None) -> dict | None:
        """压缩短期记忆为摘要，可选推断关系变化。返回关系更新 dict 或 None"""

        if len(self._short_term) < self.compress_threshold:
            return None

        if llm_client is None:
            return None

        to_compress = self._short_term[:-self.short_limit]
        if not to_compress:
            return None

        events_text = "\n".join(f"- {e['event']}" for e in to_compress)

        rel_text = ""
        if relationships:
            lines = []
            for name, rel in relationships.items():
                lines.append(f"- {name}: trust={rel.get('trust', 0)}, 印象=\"{rel.get('impression', '')}\"")
            rel_text = "\n当前关系：\n" + "\n".join(lines)

        system_prompt = (
            f"你正在为角色{self.name}整理记忆摘要。\n"
            f"请用3-5句话概括以下经历，保留关键人物和重要事件。\n"
            f"经历中的\"你\"指代角色{self.name}，请用第二人称视角（\"你\"）展开描述。\n"
            f"如果有关系变化（信任增减、印象改变），也在 JSON 中返回。\n"
            f"没有明显变化则 omit relations。\n\n"
            f"请严格以 JSON 格式回复，不要添加任何额外内容或格式标记：\n"
            f'{{"summary": "摘要内容", "relations": {{"角色名": {{"trust_change": 整数, "impression": "新印象（可选）"}}}}}}'
        )
        user_content = ""
        if self._summary:
            user_content += f"已有摘要：{self._summary}\n\n"
        user_content += rel_text + "\n" if rel_text else ""
        user_content += f"需要概括的经历：\n{events_text}"

        try:
            import litellm
            response = await litellm.acompletion(
                model=llm_client._model_str,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.3,
                api_key=llm_client.api_key,
                api_base=llm_client.base_url,
                drop_params=True,
            )
            raw = response.choices[0].message.content.strip()
            if not raw:
                if llm_client.logger:
                    llm_client.logger.warning(f"记忆压缩返回空: {self.name}")
                return None

            # Parse JSON, with regex recovery
            import json, re
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                match = re.search(r'\{.*\}', raw, re.DOTALL)
                data = json.loads(match.group()) if match else {}

            new_summary = data.get("summary", raw)
            if not new_summary:
                if llm_client.logger:
                    llm_client.logger.warning(f"记忆压缩摘要为空: {self.name}")
                return None

            self._summary = new_summary
            self._short_term = self._short_term[-self.short_limit:]
            self._compress_needed = False

            if llm_client.logger:
                llm_client.logger.info(
                    f"记忆压缩: {self.name} | {len(to_compress)} 条 → 摘要"
                )
                llm_client.logger.debug(f"记忆压缩摘要: {self.name} | {new_summary}")

            return data.get("relations")

        except Exception as e:
            if llm_client.logger:
                llm_client.logger.warning(f"记忆压缩失败: {self.name} | {e}")
            return None
