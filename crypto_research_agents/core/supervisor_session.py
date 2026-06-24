from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from crypto_research_agents.core.time import utc_now


def supervisor_session_path_for(memory_path: str | Path | None = None) -> Path:
    env_path = os.getenv("JIMMORIA_SUPERVISOR_SESSION_PATH")
    if env_path:
        return Path(env_path)
    if memory_path is not None:
        return Path(memory_path).parent / "supervisor_session.json"
    return Path("data/supervisor_session.json")


@dataclass(slots=True)
class SupervisorSessionMessage:
    role: str
    content: str
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SupervisorSessionMessage":
        known = {field_name for field_name in cls.__dataclass_fields__}
        filtered = {key: value for key, value in data.items() if key in known}
        return cls(**filtered)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SupervisorSessionStore:
    """Persistent conversation state for Hermes Agent; class name stays for compatibility."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        session_id: str | None = None,
        messages: list[SupervisorSessionMessage] | None = None,
        summaries: list[str] | None = None,
        last_room_id: str = "",
        last_topic: str = "",
    ) -> None:
        self.path = Path(path) if path is not None else supervisor_session_path_for()
        self.session_id = session_id or f"supervisor_{uuid4().hex[:10]}"
        self.messages = messages or []
        self.summaries = summaries or []
        self.last_room_id = last_room_id
        self.last_topic = last_topic

    @classmethod
    def load(cls, path: str | Path | None = None) -> "SupervisorSessionStore":
        session_path = Path(path) if path is not None else supervisor_session_path_for()
        if not session_path.exists():
            return cls(session_path)
        try:
            data = json.loads(session_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls(session_path)
        if not isinstance(data, dict):
            return cls(session_path)
        raw_messages = data.get("messages", [])
        messages = [
            SupervisorSessionMessage.from_dict(item)
            for item in raw_messages
            if isinstance(item, dict)
        ]
        raw_summaries = data.get("summaries", [])
        summaries = [str(item) for item in raw_summaries if str(item).strip()] if isinstance(raw_summaries, list) else []
        return cls(
            session_path,
            session_id=str(data.get("session_id") or ""),
            messages=messages,
            summaries=summaries,
            last_room_id=str(data.get("last_room_id") or ""),
            last_topic=str(data.get("last_topic") or ""),
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "jimmoria.supervisor_session.v1",
            "session_id": self.session_id,
            "updated_at": utc_now(),
            "last_room_id": self.last_room_id,
            "last_topic": self.last_topic,
            "summaries": self.summaries[-12:],
            "messages": [message.to_dict() for message in self.messages],
        }

    def append(self, role: str, content: str, *, metadata: dict[str, Any] | None = None) -> None:
        cleaned = content.strip()
        if not cleaned:
            return
        self.messages.append(SupervisorSessionMessage(role=role, content=cleaned, metadata=metadata or {}))
        self.compact_if_needed()

    def record_turn(
        self,
        *,
        user_message: str,
        supervisor_reply: str,
        decision: dict[str, Any] | None = None,
    ) -> None:
        self.append("user", user_message, metadata={"decision": decision or {}})
        self.append("supervisor", supervisor_reply, metadata={"decision": decision or {}})

    def recent_dialogue(self, *, limit: int = 16) -> list[dict[str, str]]:
        recent = self.messages[-limit:]
        return [{"role": item.role, "content": item.content} for item in recent]

    def memory_summary_lines(self) -> list[str]:
        lines = list(self.summaries[-4:])
        if self.last_room_id:
            lines.append(f"Last Research Room: {self.last_room_id} ({self.last_topic or 'unknown topic'})")
        return lines

    def set_last_room(self, room_id: str, topic: str) -> None:
        self.last_room_id = room_id
        self.last_topic = topic

    def compact_if_needed(self, *, max_messages: int = 80, keep_messages: int = 40) -> None:
        if len(self.messages) <= max_messages:
            return
        old = self.messages[: len(self.messages) - keep_messages]
        self.messages = self.messages[-keep_messages:]
        user_count = sum(1 for item in old if item.role == "user")
        supervisor_count = sum(1 for item in old if item.role == "supervisor")
        first_user = next((item.content for item in old if item.role == "user"), "")
        last_user = next((item.content for item in reversed(old) if item.role == "user"), "")
        summary = (
            f"Compacted {len(old)} older Hermes messages "
            f"({user_count} user, {supervisor_count} supervisor). "
            f"First user topic: {first_user[:120]}. Last older user topic: {last_user[:120]}."
        )
        self.summaries.append(summary)
        del self.summaries[:-12]
