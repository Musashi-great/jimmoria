from __future__ import annotations

import base64
import json
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen

from crypto_research_agents.connectors.base import failed, missing_input, success


GITHUB_API = "https://api.github.com"


def github_search_repos(query: str | None = None, *, limit: int = 5) -> dict[str, Any]:
    if not query:
        return missing_input("github_search_repos", "query is required")
    url = f"{GITHUB_API}/search/repositories?q={quote_plus(query)}&sort=updated&order=desc&per_page={limit}"
    response = _fetch_json(url)
    if response.get("status") != "success":
        response["tool"] = "github_search_repos"
        return response
    items = response["data"].get("items", [])
    repos = [_repo_summary(item) for item in items if isinstance(item, dict)]
    return success("github_search_repos", {"query": query, "repos": repos}, "github repositories searched")


def read_github_repo(
    repo_url: str | None = None,
    *,
    full_name: str | None = None,
) -> dict[str, Any]:
    repo_name = full_name or _repo_full_name_from_url(repo_url)
    if not repo_name:
        return missing_input("read_github_repo", "repo_url or full_name is required")

    repo_response = _fetch_json(f"{GITHUB_API}/repos/{repo_name}")
    if repo_response.get("status") != "success":
        repo_response["tool"] = "read_github_repo"
        return repo_response

    languages_response = _fetch_json(f"{GITHUB_API}/repos/{repo_name}/languages")
    readme_response = _fetch_json(f"{GITHUB_API}/repos/{repo_name}/readme")
    repo = repo_response["data"]
    readme_text = ""
    if readme_response.get("status") == "success":
        encoded = str(readme_response["data"].get("content", ""))
        try:
            readme_text = base64.b64decode(encoded).decode("utf-8", errors="replace")
        except ValueError:
            readme_text = ""

    data = {
        "repo": _repo_summary(repo),
        "languages": languages_response.get("data", {}) if languages_response.get("status") == "success" else {},
        "readme_excerpt": readme_text[:4000],
        "contract_mentions": _extract_contract_mentions(readme_text),
        "points_mentions": _extract_points_mentions(readme_text),
        "api_mentions": _extract_api_mentions(readme_text),
    }
    return success("read_github_repo", data, "github repository read")


def _fetch_json(url: str) -> dict[str, Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "jimmoria-cli",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return failed("github_api", f"GitHub request failed: {exc}", {"url": url})
    try:
        return success("github_api", json.loads(raw.decode("utf-8")), "github api response")
    except json.JSONDecodeError as exc:
        return failed("github_api", f"GitHub returned invalid JSON: {exc}", {"url": url})


def _repo_full_name_from_url(repo_url: str | None) -> str | None:
    if not repo_url:
        return None
    parsed = urlparse(repo_url)
    if "github.com" not in parsed.netloc.lower():
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    return f"{parts[0]}/{parts[1]}"


def _repo_summary(repo: dict[str, Any]) -> dict[str, Any]:
    return {
        "full_name": repo.get("full_name"),
        "html_url": repo.get("html_url"),
        "description": repo.get("description"),
        "stars": repo.get("stargazers_count"),
        "forks": repo.get("forks_count"),
        "open_issues": repo.get("open_issues_count"),
        "default_branch": repo.get("default_branch"),
        "pushed_at": repo.get("pushed_at"),
        "updated_at": repo.get("updated_at"),
        "archived": repo.get("archived"),
        "fork": repo.get("fork"),
    }


def _extract_contract_mentions(text: str) -> list[str]:
    return sorted(set(re.findall(r"0x[a-fA-F0-9]{40}", text)))[:20]


def _extract_points_mentions(text: str) -> list[str]:
    lowered = text.lower()
    return [keyword for keyword in ["points", "airdrop", "rewards", "quest"] if keyword in lowered]


def _extract_api_mentions(text: str) -> list[str]:
    lowered = text.lower()
    return [keyword for keyword in ["api", "sdk", "graphql", "rest", "webhook"] if keyword in lowered]
