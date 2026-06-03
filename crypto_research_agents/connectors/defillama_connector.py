from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from crypto_research_agents.connectors.base import failed, missing_input, success


DEFILLAMA_API = "https://api.llama.fi"


def defillama_protocol_search(query: str | None = None, *, limit: int = 10) -> dict[str, Any]:
    if not query:
        return missing_input("defillama_protocol_search", "query is required")
    response = _fetch_json(f"{DEFILLAMA_API}/protocols")
    if response.get("status") != "success":
        response["tool"] = "defillama_protocol_search"
        return response

    query_lower = query.lower()
    protocols = [
        _protocol_summary(item)
        for item in response.get("data", [])
        if isinstance(item, dict) and _matches_protocol(item, query_lower)
    ]
    protocols.sort(key=lambda item: _protocol_score(item, query_lower), reverse=True)
    return success(
        "defillama_protocol_search",
        {"query": query, "protocols": protocols[: max(1, min(limit, 50))]},
        "DefiLlama protocols searched",
    )


def defillama_tvl_snapshot(
    protocol_slug: str | None = None,
    *,
    project_name: str | None = None,
) -> dict[str, Any]:
    slug = protocol_slug
    if not slug and project_name:
        search = defillama_protocol_search(project_name, limit=1)
        protocols = search.get("data", {}).get("protocols", []) if isinstance(search.get("data"), dict) else []
        if protocols:
            slug = str(protocols[0].get("slug") or "")
    if not slug:
        return missing_input("defillama_tvl_snapshot", "protocol_slug or project_name is required")

    response = _fetch_json(f"{DEFILLAMA_API}/protocol/{quote_plus(slug)}")
    if response.get("status") != "success":
        response["tool"] = "defillama_tvl_snapshot"
        return response
    data = response.get("data", {})
    return success(
        "defillama_tvl_snapshot",
        {
            "slug": slug,
            "name": data.get("name"),
            "category": data.get("category"),
            "chains": data.get("chains", []),
            "tvl": data.get("tvl"),
            "chain_tvls": data.get("chainTvls", {}),
            "token_breakdowns": data.get("tokens", [])[:10] if isinstance(data.get("tokens"), list) else [],
            "twitter": data.get("twitter"),
            "url": data.get("url"),
        },
        "DefiLlama TVL snapshot read",
    )


def _fetch_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "jimmoria-cli", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read(3_000_000)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return failed("defillama_api", f"DefiLlama request failed: {exc}", {"url": url})
    try:
        return success("defillama_api", json.loads(raw.decode("utf-8")), "DefiLlama response")
    except json.JSONDecodeError as exc:
        return failed("defillama_api", f"DefiLlama returned invalid JSON: {exc}", {"url": url})


def _matches_protocol(item: dict[str, Any], query_lower: str) -> bool:
    fields = " ".join(
        str(item.get(key) or "")
        for key in ["name", "slug", "symbol", "category", "description", "url"]
    ).lower()
    return query_lower in fields or all(token in fields for token in query_lower.split())


def _protocol_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": item.get("name"),
        "slug": item.get("slug"),
        "symbol": item.get("symbol"),
        "category": item.get("category"),
        "chains": item.get("chains", []),
        "tvl": item.get("tvl"),
        "url": item.get("url"),
        "twitter": item.get("twitter"),
        "listed_at": item.get("listedAt"),
    }


def _protocol_score(item: dict[str, Any], query_lower: str) -> float:
    name = str(item.get("name") or "").lower()
    slug = str(item.get("slug") or "").lower()
    score = 0.0
    if name == query_lower or slug == query_lower:
        score += 100
    if query_lower in name or query_lower in slug:
        score += 30
    if item.get("tvl"):
        score += min(float(item.get("tvl") or 0) / 1_000_000_000, 10)
    return score
