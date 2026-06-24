from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

from jimmoria.connectors.base import failed, missing_input, success


def web_search(
    query: str | None = None,
    *,
    limit: int = 8,
) -> dict[str, Any]:
    """Search the public web for project discovery evidence.

    This connector intentionally returns short metadata only. Downstream agents
    should fetch promising URLs through fetch_url/crawl_website before treating
    claims as evidence.
    """

    if not query or not str(query).strip():
        return missing_input("web_search", "query is required")
    if os.getenv("JIMMORIA_SKIP_EXTERNAL_SEARCH", "").strip().lower() in {"1", "true", "yes", "on"}:
        return success(
            "web_search",
            {"query": query, "results": []},
            "web search skipped by JIMMORIA_SKIP_EXTERNAL_SEARCH",
        )

    try:
        from ddgs import DDGS
    except ImportError:
        return failed(
            "web_search",
            "ddgs is not installed; run `python -m pip install -e .[all]`",
            {"query": query},
        )

    results: list[dict[str, Any]] = []
    try:
        timeout = float(os.getenv("JIMMORIA_WEB_SEARCH_TIMEOUT", "8"))
        with DDGS(timeout=timeout) as ddgs:
            for item in ddgs.text(str(query), max_results=max(1, min(limit, 20))):
                url = str(item.get("href") or item.get("url") or "").strip()
                if not url:
                    continue
                results.append(
                    {
                        "title": _clean_text(item.get("title")),
                        "url": url,
                        "snippet": _clean_text(item.get("body") or item.get("snippet")),
                        "host": urlparse(url).netloc.lower(),
                    }
                )
    except Exception as exc:
        return failed("web_search", f"web search failed: {exc}", {"query": query})

    return success(
        "web_search",
        {"query": query, "results": _dedupe_results(results)[:limit]},
        "web searched",
    )


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _dedupe_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for result in results:
        url = str(result.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(result)
    return deduped
