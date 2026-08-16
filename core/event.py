"""结构化时间线事件。"""

from __future__ import annotations

from dataclasses import dataclass

SOURCE_GM = "gm"
SOURCE_NPC = "npc"
SOURCE_AGENT = "agent"
SOURCE_RULE = "rule"


@dataclass
class TimelineEvent:
    tick: int
    text: str
    source: str = "GM"
    source_type: str = SOURCE_GM
    meta: dict | None = None

    def to_dict(self) -> dict:
        return {
            "tick": self.tick,
            "text": self.text,
            "source": self.source,
            "source_type": self.source_type,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TimelineEvent":
        return cls(
            tick=data["tick"],
            text=data["text"],
            source=data.get("source", "GM"),
            source_type=data.get("source_type", SOURCE_GM),
            meta=data.get("meta"),
        )
