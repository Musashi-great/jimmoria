from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from jimmoria.connectors.base import failed, missing_input, missing_secret, success


ROOTDATA_API = "https://api.rootdata.com/open"


def rootdata_search_projects(query: str | None = None, *, precise_x_search: bool = False, language: str = "en") -> dict[str, Any]:
    if not query:
        return missing_input("rootdata_search_projects", "query is required")
    response = _rootdata_post(
        "rootdata_search_projects",
        "/ser_inv",
        {"query": query, "precise_x_search": precise_x_search},
        language=language,
    )
    if response.get("status") != "success":
        return response
    data = response.get("data", {})
    rows = data.get("data", []) if isinstance(data, dict) else []
    projects = [_search_summary(row) for row in rows if isinstance(row, dict)]
    return success("rootdata_search_projects", {"query": query, "projects": projects, "raw_result": data.get("result")}, "RootData projects searched")


def rootdata_get_project(
    project_id: int | str | None = None,
    *,
    contract_address: str | None = None,
    include_team: bool = True,
    include_investors: bool = True,
    language: str = "en",
) -> dict[str, Any]:
    if project_id is None and not contract_address:
        return missing_input("rootdata_get_project", "project_id or contract_address is required")
    payload: dict[str, Any] = {
        "include_team": include_team,
        "include_investors": include_investors,
    }
    if project_id is not None:
        payload["project_id"] = int(project_id)
    if contract_address:
        payload["contract_address"] = contract_address
    response = _rootdata_post("rootdata_get_project", "/get_item", payload, language=language)
    if response.get("status") != "success":
        return response
    data = response.get("data", {})
    item = data.get("data") if isinstance(data, dict) else {}
    return success("rootdata_get_project", {"project": _project_summary(item if isinstance(item, dict) else {}), "raw": item}, "RootData project fetched")


def rootdata_get_investors(page: int = 1, page_size: int = 10, *, language: str = "en") -> dict[str, Any]:
    response = _rootdata_post(
        "rootdata_get_investors",
        "/get_invest",
        {"page": max(1, int(page)), "page_size": max(1, min(int(page_size), 100))},
        language=language,
    )
    if response.get("status") != "success":
        return response
    data = response.get("data", {})
    investors = data.get("data", []) if isinstance(data, dict) else []
    return success("rootdata_get_investors", {"investors": investors, "raw_result": data.get("result") if isinstance(data, dict) else None}, "RootData investors fetched")


def rootdata_get_hot_projects(query: str | None = None, *, language: str = "en") -> dict[str, Any]:
    if query:
        return rootdata_search_projects(query, language=language)
    return missing_input(
        "rootdata_get_hot_projects",
        "RootData hot project endpoint depends on plan/API method availability. Provide a query for now or configure a specific endpoint.",
    )


def _rootdata_post(tool: str, path: str, payload: dict[str, Any], *, language: str = "en") -> dict[str, Any]:
    api_key = os.getenv("ROOTDATA_API_KEY") or ""
    if not api_key:
        return missing_secret(tool, "ROOTDATA_API_KEY", "Set ROOTDATA_API_KEY to use RootData API.")
    raw = json.dumps(payload).encode("utf-8")
    request = Request(
        f"{ROOTDATA_API}{path}",
        data=raw,
        headers={
            "apikey": api_key,
            "language": language,
            "Content-Type": "application/json",
            "User-Agent": "jimmoria-cli",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=25) as response:
            body = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return failed(tool, f"RootData request failed: HTTP {exc.code}", {"path": path, "detail": detail[:1000]})
    except (URLError, TimeoutError, OSError) as exc:
        return failed(tool, f"RootData request failed: {exc}", {"path": path})
    try:
        data = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        return failed(tool, f"RootData returned invalid JSON: {exc}", {"path": path})
    if data.get("result") not in {200, "200"}:
        return failed(tool, str(data.get("message") or "RootData returned non-200 result"), {"path": path, "response": data})
    return success(tool, data, "RootData API response")


def _search_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "type": row.get("type"),
        "name": row.get("name"),
        "introduce": row.get("introduce"),
        "active": row.get("active"),
        "rootdata_url": row.get("rootdataurl"),
        "logo": row.get("logo"),
    }


def _project_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": item.get("project_id"),
        "project_name": item.get("project_name"),
        "token_symbol": item.get("token_symbol"),
        "one_liner": item.get("one_liner"),
        "description": item.get("description"),
        "active": item.get("active"),
        "total_funding": item.get("total_funding"),
        "tags": item.get("tags"),
        "rootdata_url": item.get("rootdataurl"),
        "social_media": item.get("social_media"),
        "investors": item.get("investors"),
        "contracts": item.get("contracts"),
        "similar_project": item.get("similar_project"),
    }
