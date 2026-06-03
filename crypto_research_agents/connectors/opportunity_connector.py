from __future__ import annotations

from typing import Any

from crypto_research_agents.connectors.base import missing_input, success
from crypto_research_agents.connectors.web_search import web_search


def check_airdrop_points(project_name: str | None = None, *, limit: int = 6) -> dict[str, Any]:
    if not project_name:
        return missing_input("check_airdrop_points", "project_name is required")
    query = f"{project_name} points airdrop rewards testnet official"
    response = web_search(query=query, limit=limit)
    if response.get("status") != "success":
        response["tool"] = "check_airdrop_points"
        return response
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    results = data.get("results", []) if isinstance(data.get("results"), list) else []
    hints = [_hint_from_result(result) for result in results if isinstance(result, dict)]
    hints = [hint for hint in hints if hint["signals"]]
    return success(
        "check_airdrop_points",
        {
            "project_name": project_name,
            "query": query,
            "hints": hints,
            "classification": "hint_found" if hints else "unknown",
            "note": "Hints are source leads only; confirm against official project sources before treating as real programs.",
        },
        "Airdrop/points hints searched",
    )


def _hint_from_result(result: dict[str, Any]) -> dict[str, Any]:
    text = " ".join([str(result.get("title", "")), str(result.get("snippet", "")), str(result.get("url", ""))]).lower()
    signals = [
        keyword
        for keyword in ["points", "airdrop", "rewards", "quest", "testnet", "allowlist", "waitlist"]
        if keyword in text
    ]
    return {
        "title": result.get("title"),
        "url": result.get("url"),
        "snippet": result.get("snippet"),
        "signals": signals,
    }
