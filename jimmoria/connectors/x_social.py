from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from jimmoria.connectors.base import failed, missing_input, missing_secret, success


X_API = "https://api.x.com/2"


def x_search_posts(query: str | None = None, *, limit: int = 10, next_token: str | None = None) -> dict[str, Any]:
    if not query:
        return missing_input("x_search_posts", "query is required")
    token = _bearer_token()
    if not token:
        return missing_secret("x_search_posts", "X_BEARER_TOKEN", "Set X_BEARER_TOKEN to use X recent search.")
    max_results = max(10, min(int(limit or 10), 100))
    params: dict[str, str] = {
        "query": query,
        "max_results": str(max_results),
        "tweet.fields": "created_at,public_metrics,author_id,lang,entities",
        "expansions": "author_id",
        "user.fields": "username,name,verified,public_metrics",
    }
    if next_token:
        params["next_token"] = next_token
    response = _x_get(f"{X_API}/tweets/search/recent?{urlencode(params)}", token)
    if response.get("status") != "success":
        response["tool"] = "x_search_posts"
        return response
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    return success(
        "x_search_posts",
        {
            "query": query,
            "posts": _posts(data),
            "users": _users_by_id(data),
            "meta": data.get("meta", {}),
        },
        "X recent posts searched",
    )


def x_get_user_timeline(handle: str | None = None, *, limit: int = 10) -> dict[str, Any]:
    normalized = _normalize_handle(handle)
    if not normalized:
        return missing_input("x_get_user_timeline", "handle is required")
    token = _bearer_token()
    if not token:
        return missing_secret("x_get_user_timeline", "X_BEARER_TOKEN", "Set X_BEARER_TOKEN to read X timelines.")

    user_response = _x_get(
        f"{X_API}/users/by/username/{quote(normalized)}?user.fields=username,name,verified,public_metrics",
        token,
    )
    if user_response.get("status") != "success":
        user_response["tool"] = "x_get_user_timeline"
        return user_response
    user = user_response.get("data", {}).get("data") if isinstance(user_response.get("data"), dict) else {}
    user_id = str(user.get("id") or "")
    if not user_id:
        return failed("x_get_user_timeline", "X user id not found", {"handle": normalized})
    max_results = max(5, min(int(limit or 10), 100))
    params = urlencode(
        {
            "max_results": str(max_results),
            "tweet.fields": "created_at,public_metrics,lang,entities",
        }
    )
    timeline_response = _x_get(f"{X_API}/users/{quote(user_id)}/tweets?{params}", token)
    if timeline_response.get("status") != "success":
        timeline_response["tool"] = "x_get_user_timeline"
        return timeline_response
    data = timeline_response.get("data") if isinstance(timeline_response.get("data"), dict) else {}
    return success(
        "x_get_user_timeline",
        {"handle": normalized, "user": user, "posts": _posts(data), "meta": data.get("meta", {})},
        "X user timeline read",
    )


def x_build_kol_list(query: str | None = None, *, handles: list[str] | None = None, limit: int = 20) -> dict[str, Any]:
    seed_handles = [_normalize_handle(handle) for handle in handles or []]
    seed_handles = [handle for handle in seed_handles if handle]
    if not query and not seed_handles:
        return missing_input("x_build_kol_list", "query or handles are required")
    token = _bearer_token()
    if not token:
        return missing_secret("x_build_kol_list", "X_BEARER_TOKEN", "Set X_BEARER_TOKEN to build KOL lists from X.")
    if seed_handles:
        profiles = []
        for handle in seed_handles[:limit]:
            response = _x_get(
                f"{X_API}/users/by/username/{quote(handle)}?user.fields=username,name,verified,public_metrics",
                token,
            )
            if response.get("status") == "success":
                user = response.get("data", {}).get("data") if isinstance(response.get("data"), dict) else {}
                if isinstance(user, dict) and user:
                    profiles.append(_profile_summary(user))
        return success("x_build_kol_list", {"query": query, "profiles": profiles}, "X KOL profiles resolved")
    search = x_search_posts(query=f"{query} -is:retweet", limit=min(limit, 100))
    if search.get("status") != "success":
        search["tool"] = "x_build_kol_list"
        return search
    users = search.get("data", {}).get("users", {}) if isinstance(search.get("data"), dict) else {}
    profiles = [_profile_summary(user) for user in users.values() if isinstance(user, dict)]
    profiles.sort(key=lambda item: int(item.get("followers", 0) or 0), reverse=True)
    return success("x_build_kol_list", {"query": query, "profiles": profiles[:limit]}, "X KOL list built")


def _x_get(url: str, token: str) -> dict[str, Any]:
    request = Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "jimmoria-cli"})
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return failed("x_api", f"X API request failed: HTTP {exc.code}", {"url": url, "detail": detail[:1000]})
    except (URLError, TimeoutError, OSError) as exc:
        return failed("x_api", f"X API request failed: {exc}", {"url": url})
    try:
        return success("x_api", json.loads(raw.decode("utf-8")), "X API response")
    except json.JSONDecodeError as exc:
        return failed("x_api", f"X API returned invalid JSON: {exc}", {"url": url})


def _bearer_token() -> str:
    return os.getenv("X_BEARER_TOKEN") or os.getenv("TWITTER_BEARER_TOKEN") or ""


def _normalize_handle(handle: str | None) -> str:
    return str(handle or "").strip().lstrip("@")


def _posts(data: dict[str, Any]) -> list[dict[str, Any]]:
    users = _users_by_id(data)
    rows = []
    for item in data.get("data", []) if isinstance(data.get("data"), list) else []:
        if not isinstance(item, dict):
            continue
        author = users.get(str(item.get("author_id") or ""), {})
        metrics = item.get("public_metrics") if isinstance(item.get("public_metrics"), dict) else {}
        rows.append(
            {
                "id": item.get("id"),
                "text": item.get("text"),
                "created_at": item.get("created_at"),
                "author_id": item.get("author_id"),
                "author_username": author.get("username"),
                "public_metrics": metrics,
                "url": f"https://x.com/{author.get('username')}/status/{item.get('id')}" if author.get("username") and item.get("id") else None,
            }
        )
    return rows


def _users_by_id(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    includes = data.get("includes") if isinstance(data.get("includes"), dict) else {}
    users = includes.get("users") if isinstance(includes.get("users"), list) else []
    return {str(user.get("id")): user for user in users if isinstance(user, dict) and user.get("id")}


def _profile_summary(user: dict[str, Any]) -> dict[str, Any]:
    metrics = user.get("public_metrics") if isinstance(user.get("public_metrics"), dict) else {}
    return {
        "id": user.get("id"),
        "username": user.get("username"),
        "name": user.get("name"),
        "verified": user.get("verified"),
        "followers": metrics.get("followers_count"),
        "following": metrics.get("following_count"),
        "url": f"https://x.com/{user.get('username')}" if user.get("username") else None,
    }
