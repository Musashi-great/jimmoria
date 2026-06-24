from __future__ import annotations

import os
from typing import Any

from crypto_research_agents.connectors.base import failed, missing_input, success
from crypto_research_agents.connectors.operator_bridge import browser_click, browser_snapshot
from crypto_research_agents.connectors.web_search import web_search


def honcho_memory_search(query: str | None = None, *, limit: int = 8) -> dict[str, Any]:
    if not query:
        return missing_input("honcho_memory_search", "query is required")
    if not os.getenv("HONCHO_API_KEY"):
        return failed(
            "honcho_memory_search",
            "Honcho is not configured; set HONCHO_API_KEY before using behavioral long-term memory.",
            {"query": query, "limit": limit, "status": "external_connector_required"},
        )
    return failed(
        "honcho_memory_search",
        "Honcho connector boundary is declared, but live API transport is not implemented in this local runtime yet.",
        {"query": query, "limit": limit, "status": "external_connector_required"},
    )


def honcho_observation_write(observation: str | None = None, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    if not observation:
        return missing_input("honcho_observation_write", "observation is required")
    if not os.getenv("HONCHO_API_KEY"):
        return failed(
            "honcho_observation_write",
            "Honcho is not configured; set HONCHO_API_KEY before writing behavioral observations.",
            {"status": "external_connector_required"},
        )
    return failed(
        "honcho_observation_write",
        "Honcho connector boundary is declared, but live API transport is not implemented in this local runtime yet.",
        {"observation_preview": observation[:240], "metadata": metadata or {}, "status": "external_connector_required"},
    )


def qmd_text_search(query: str | None = None, *, limit: int = 12) -> dict[str, Any]:
    if not query:
        return missing_input("qmd_text_search", "query is required")
    if not os.getenv("QMD_ENDPOINT"):
        return failed(
            "qmd_text_search",
            "QMD endpoint is not configured; set QMD_ENDPOINT for device text search.",
            {"query": query, "limit": limit, "status": "external_connector_required"},
        )
    return failed(
        "qmd_text_search",
        "QMD connector boundary is declared, but live device-search transport is not implemented in this local runtime yet.",
        {"query": query, "limit": limit, "endpoint": os.getenv("QMD_ENDPOINT"), "status": "external_connector_required"},
    )


def qmd_vector_search(query: str | None = None, *, limit: int = 12) -> dict[str, Any]:
    if not query:
        return missing_input("qmd_vector_search", "query is required")
    if not os.getenv("QMD_ENDPOINT"):
        return failed(
            "qmd_vector_search",
            "QMD endpoint is not configured; set QMD_ENDPOINT for device vector search.",
            {"query": query, "limit": limit, "status": "external_connector_required"},
        )
    return failed(
        "qmd_vector_search",
        "QMD vector connector boundary is declared, but live vector transport is not implemented in this local runtime yet.",
        {"query": query, "limit": limit, "endpoint": os.getenv("QMD_ENDPOINT"), "status": "external_connector_required"},
    )


def qmd_vector_upsert(document_id: str | None = None, *, text: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    if not document_id:
        return missing_input("qmd_vector_upsert", "document_id is required")
    if not text:
        return missing_input("qmd_vector_upsert", "text is required")
    if not os.getenv("QMD_ENDPOINT"):
        return failed(
            "qmd_vector_upsert",
            "QMD endpoint is not configured; set QMD_ENDPOINT before vector upsert.",
            {"document_id": document_id, "status": "external_connector_required"},
        )
    return failed(
        "qmd_vector_upsert",
        "QMD upsert boundary is declared, but live vector transport is not implemented in this local runtime yet.",
        {"document_id": document_id, "metadata": metadata or {}, "status": "external_connector_required"},
    )


def tavily_search(query: str | None = None, *, limit: int = 8) -> dict[str, Any]:
    if not query:
        return missing_input("tavily_search", "query is required")
    if not os.getenv("TAVILY_API_KEY"):
        fallback = web_search(query, limit=limit)
        return failed(
            "tavily_search",
            "Tavily is not configured; set TAVILY_API_KEY. Public web_search fallback is included for continuity.",
            {"query": query, "fallback": fallback.get("data"), "status": "missing_secret"},
        )
    return failed(
        "tavily_search",
        "Tavily connector boundary is declared, but live API transport is not implemented in this local runtime yet.",
        {"query": query, "limit": limit, "status": "external_connector_required"},
    )


def browser_cdp_navigate(url: str | None = None, *, timeout: int = 20) -> dict[str, Any]:
    if not url:
        return missing_input("browser_cdp_navigate", "url is required")
    if not os.getenv("BROWSER_CDP_ENDPOINT"):
        snapshot = browser_snapshot(url, max_chars=6000)
        return failed(
            "browser_cdp_navigate",
            "CDP browser harness is not configured; set BROWSER_CDP_ENDPOINT. Stateless snapshot fallback is included.",
            {"url": url, "fallback": snapshot.get("data"), "status": "external_connector_required"},
        )
    return failed(
        "browser_cdp_navigate",
        "CDP browser harness boundary is declared, but live CDP transport is not implemented in this local runtime yet.",
        {"url": url, "timeout": timeout, "endpoint": os.getenv("BROWSER_CDP_ENDPOINT"), "status": "external_connector_required"},
    )


def browser_cdp_snapshot(url: str | None = None, *, max_chars: int = 6000) -> dict[str, Any]:
    if not os.getenv("BROWSER_CDP_ENDPOINT"):
        return browser_snapshot(url, max_chars=max_chars)
    return browser_cdp_navigate(url, timeout=20)


def browser_cdp_click(url: str | None = None, *, link_url: str | None = None, max_chars: int = 6000) -> dict[str, Any]:
    if not os.getenv("BROWSER_CDP_ENDPOINT"):
        return browser_click(url, link_url=link_url, max_chars=max_chars)
    target = link_url or url
    return browser_cdp_navigate(target, timeout=20)
