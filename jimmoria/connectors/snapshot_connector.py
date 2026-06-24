from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from jimmoria.connectors.base import failed, missing_input, success


SNAPSHOT_GRAPHQL = "https://hub.snapshot.org/graphql"


def snapshot_get_proposals(
    space: str | None = None,
    *,
    query: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    if not space and not query:
        return missing_input("snapshot_get_proposals", "space or query is required")
    if not space:
        spaces = _snapshot_spaces(query or "", limit=1)
        if spaces.get("status") != "success":
            spaces["tool"] = "snapshot_get_proposals"
            return spaces
        matches = spaces.get("data", {}).get("spaces", []) if isinstance(spaces.get("data"), dict) else []
        if not matches:
            return success("snapshot_get_proposals", {"query": query, "space": None, "proposals": []}, "no Snapshot space matched")
        space = str(matches[0].get("id") or "")
    if not space:
        return missing_input("snapshot_get_proposals", "space could not be resolved")

    gql = """
    query Proposals($space: String!, $first: Int!) {
      proposals(first: $first, skip: 0, where: {space: $space}, orderBy: "created", orderDirection: desc) {
        id
        title
        state
        created
        start
        end
        link
        scores_total
        space { id name }
      }
    }
    """
    response = _post_graphql(gql, {"space": space, "first": max(1, min(limit, 50))})
    if response.get("status") != "success":
        response["tool"] = "snapshot_get_proposals"
        return response
    proposals = response.get("data", {}).get("proposals", []) if isinstance(response.get("data"), dict) else []
    return success(
        "snapshot_get_proposals",
        {"space": space, "query": query, "proposals": [_proposal_summary(item) for item in proposals]},
        "Snapshot proposals read",
    )


def _snapshot_spaces(query: str, *, limit: int) -> dict[str, Any]:
    gql = """
    query Spaces($query: String!, $first: Int!) {
      spaces(first: $first, where: {id_contains: $query}) {
        id
        name
        followersCount
        verified
      }
    }
    """
    response = _post_graphql(gql, {"query": query.lower().strip(), "first": max(1, min(limit, 20))})
    if response.get("status") != "success":
        return response
    spaces = response.get("data", {}).get("spaces", []) if isinstance(response.get("data"), dict) else []
    return success("snapshot_spaces", {"query": query, "spaces": spaces}, "Snapshot spaces searched")


def _post_graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = Request(
        SNAPSHOT_GRAPHQL,
        data=payload,
        headers={"User-Agent": "jimmoria-cli", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read(2_000_000)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return failed("snapshot_graphql", f"Snapshot request failed: {exc}", {"variables": variables})
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        return failed("snapshot_graphql", f"Snapshot returned invalid JSON: {exc}", {"variables": variables})
    if parsed.get("errors"):
        return failed("snapshot_graphql", "Snapshot GraphQL returned errors", {"errors": parsed.get("errors")})
    return success("snapshot_graphql", parsed.get("data", {}), "Snapshot GraphQL response")


def _proposal_summary(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    space = item.get("space") if isinstance(item.get("space"), dict) else {}
    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "state": item.get("state"),
        "created": item.get("created"),
        "start": item.get("start"),
        "end": item.get("end"),
        "link": item.get("link"),
        "scores_total": item.get("scores_total"),
        "space": {"id": space.get("id"), "name": space.get("name")},
    }
