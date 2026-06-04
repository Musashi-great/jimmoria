from __future__ import annotations

from typing import Any


def handle(event_type: str, context: dict[str, Any]) -> dict[str, Any]:
    text = str(context.get("report_text") or context.get("summary") or "")
    lowered = text.lower()
    blocked_terms = ["buy now", "sell now", "price target", "guaranteed"]
    hits = [term for term in blocked_terms if term in lowered]
    return {
        "status": "needs_rewrite" if hits else "ok",
        "event_type": event_type,
        "blocked_terms": hits,
    }
