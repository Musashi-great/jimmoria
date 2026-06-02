from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from .time import utc_now


class MessageType(str, Enum):
    REQUEST = "REQUEST"
    RESPONSE = "RESPONSE"
    HANDOFF = "HANDOFF"
    COMMENT = "COMMENT"
    UPDATE = "UPDATE"


@dataclass(slots=True)
class AgentMessage:
    room_id: str
    from_agent: str
    to_agent: str
    type: MessageType
    message_id: str = field(default_factory=lambda: f"msg_{uuid4().hex[:12]}")
    priority: str = "normal"
    task: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    status: str = "created"
    reply_to: str | None = None
    notes: list[str] = field(default_factory=list)
    confidence: float | None = None
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "room_id": self.room_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "type": self.type.value,
            "priority": self.priority,
            "task": self.task,
            "context": self.context,
            "result": self.result,
            "status": self.status,
            "reply_to": self.reply_to,
            "notes": self.notes,
            "confidence": self.confidence,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentMessage":
        return cls(
            message_id=data["message_id"],
            room_id=data["room_id"],
            from_agent=data["from_agent"],
            to_agent=data["to_agent"],
            type=MessageType(data["type"]),
            priority=data.get("priority", "normal"),
            task=data.get("task", {}),
            context=data.get("context", {}),
            result=data.get("result", {}),
            status=data.get("status", "created"),
            reply_to=data.get("reply_to"),
            notes=data.get("notes", []),
            confidence=data.get("confidence"),
            created_at=data.get("created_at", utc_now()),
        )
