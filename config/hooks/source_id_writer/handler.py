from __future__ import annotations

from typing import Any


def handle(event_type: str, context: dict[str, Any]) -> dict[str, Any]:
    source_id = context.get("source_id") or context.get("finding_id")
    return {
        "status": "attached" if source_id else "no_source_id",
        "event_type": event_type,
        "source_id": source_id,
    }
