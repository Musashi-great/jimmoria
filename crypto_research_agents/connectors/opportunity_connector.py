from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

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
    tokens = _distinctive_tokens(project_name)
    if tokens:
        hints = [hint for hint in hints if _mentions_project(hint, tokens)]
    hints = [hint for hint in hints if _is_high_signal_hint(hint, tokens)]
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


def _distinctive_tokens(project_name: str) -> list[str]:
    generic = {"protocol", "project", "network", "labs", "lab", "crypto", "chain", "finance"}
    tokens = []
    for token in project_name.lower().replace("-", " ").replace("_", " ").split():
        cleaned = "".join(char for char in token if char.isalnum())
        if len(cleaned) >= 4 and cleaned not in generic:
            tokens.append(cleaned)
    return tokens[:3]


def _mentions_project(hint: dict[str, Any], tokens: list[str]) -> bool:
    text = " ".join(
        str(hint.get(key, ""))
        for key in ["title", "url", "snippet"]
    ).lower()
    return any(token in text for token in tokens)


def _is_high_signal_hint(hint: dict[str, Any], tokens: list[str]) -> bool:
    url = str(hint.get("url") or "")
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    low_trust_hosts = {
        "facebook.com",
        "m.facebook.com",
        "youtube.com",
        "youtu.be",
        "tiktok.com",
        "reddit.com",
        "www.reddit.com",
    }
    if host in low_trust_hosts:
        return False
    if any(token and token in host for token in tokens):
        return True
    if host.startswith(("docs.", "blog.", "mirror.xyz")):
        return True
    text = " ".join(str(hint.get(key, "")) for key in ["title", "snippet", "url"]).lower()
    return "official" in text and any(token in text for token in tokens)
