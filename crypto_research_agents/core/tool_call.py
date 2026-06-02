from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4

from .time import utc_now


@dataclass(slots=True)
class ToolCallRecord:
    agent_id: str
    tool_name: str
    input: dict[str, Any]
    status: str
    room_id: str | None = None
    result_ref: str | None = None
    source_id: str | None = None
    latency_ms: int | None = None
    cost_usd: float | None = None
    result: dict[str, Any] | None = None
    tool_call_id: str = field(default_factory=lambda: f"toolcall_{uuid4().hex[:12]}")
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
