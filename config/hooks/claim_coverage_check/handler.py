from __future__ import annotations

from typing import Any


def handle(event_type: str, context: dict[str, Any]) -> dict[str, Any]:
    findings = context.get("findings")
    if not isinstance(findings, list):
        findings = []
    required_groups = [
        "identity",
        "product",
        "social",
        "contract",
        "funding",
    ]
    missing = [
        group
        for group in required_groups
        if not any(group in str(finding).lower() for finding in findings)
    ]
    return {
        "status": "needs_followup" if missing else "ok",
        "event_type": event_type,
        "missing_claim_groups": missing,
    }
