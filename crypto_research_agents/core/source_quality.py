from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse


GENERIC_PROJECT_WORDS = {
    "app",
    "chain",
    "coin",
    "crypto",
    "finance",
    "foundation",
    "labs",
    "lab",
    "network",
    "http",
    "https",
    "www",
    "com",
    "org",
    "net",
    "xyz",
    "io",
    "app",
    "docs",
    "protocol",
    "project",
    "research",
    "token",
}

GENERIC_PLATFORM_HOSTS = {
    "docs.github.com",
    "github.blog",
    "github.com",
    "x.com",
    "twitter.com",
    "t.me",
    "telegram.org",
    "discord.com",
    "discord.gg",
    "reddit.com",
    "www.reddit.com",
    "coinmarketcap.com",
    "www.coinmarketcap.com",
    "coingecko.com",
    "www.coingecko.com",
}

GENERIC_GITHUB_PREFIXES = (
    "/features",
    "/marketplace",
    "/pricing",
    "/security",
    "/enterprise",
    "/login",
    "/signup",
    "/about",
    "/events",
    "/collections",
    "/topics",
    "/docs",
)

LOW_SIGNAL_PATH_MARKERS = (
    "cookie-policy",
    "privacy-policy",
    "/privacy",
    "/terms",
    "/legal",
    "/careers",
    "/jobs",
    "/login",
    "/sign-in",
    "/signup",
    "/sign-up",
    "/contact",
    "/support",
)


def project_tokens(*values: object) -> list[str]:
    tokens: list[str] = []
    for value in values:
        text = str(value or "").lower()
        for token in re.findall(r"[a-z0-9]{3,}", text):
            if token in GENERIC_PROJECT_WORDS:
                continue
            if token not in tokens:
                tokens.append(token)
    return tokens[:8]


def project_tokens_from_project(project: Any) -> list[str]:
    metadata = getattr(project, "metadata", {}) if isinstance(getattr(project, "metadata", {}), dict) else {}
    return project_tokens(
        getattr(project, "name", ""),
        getattr(project, "website", ""),
        metadata.get("project_query", ""),
    )


def normalized_host(url: str | None) -> str:
    host = urlparse(str(url or "")).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def is_http_url(url: str | None) -> bool:
    return str(url or "").startswith(("http://", "https://"))


def is_pdf_url(url: str | None) -> bool:
    return urlparse(str(url or "")).path.lower().endswith(".pdf")


def is_generic_platform_url(url: str | None) -> bool:
    if not is_http_url(url):
        return True
    parsed = urlparse(str(url))
    host = normalized_host(str(url))
    path = parsed.path.lower()
    if host == "github.com" and path.startswith(GENERIC_GITHUB_PREFIXES):
        return True
    if host in GENERIC_PLATFORM_HOSTS:
        return True
    return False


def is_low_signal_url(url: str | None) -> bool:
    lowered = str(url or "").lower()
    return any(marker in lowered for marker in LOW_SIGNAL_PATH_MARKERS)


def url_mentions_project(url: str | None, tokens: list[str]) -> bool:
    lowered = str(url or "").lower()
    return any(token and token in lowered for token in tokens)


def result_mentions_project(result: dict[str, Any], tokens: list[str]) -> bool:
    text = " ".join(
        str(result.get(key, ""))
        for key in ["title", "snippet", "description", "url", "host"]
    ).lower()
    return any(token and token in text for token in tokens)


def is_primary_project_site(project_query: str, url: str | None, context: str = "") -> bool:
    if not is_http_url(url) or is_pdf_url(url) or is_low_signal_url(url):
        return False
    host = normalized_host(url)
    if is_generic_platform_url(url):
        return False
    tokens = project_tokens(project_query)
    haystack = f"{host} {url} {context}".lower()
    return any(token in haystack for token in tokens)


def score_official_url(project_query: str, result: dict[str, Any]) -> int:
    url = str(result.get("url") or "")
    if not is_http_url(url):
        return -100

    host = normalized_host(url)
    path = urlparse(url).path.lower()
    text = f"{result.get('title', '')} {result.get('snippet', '')} {host} {path}".lower()
    tokens = project_tokens(project_query)
    score = 0
    if result.get("source") == "identity_hint":
        score += 35
    if any(token in host for token in tokens):
        score += 24
    if any(token in text for token in tokens):
        score += 12
    if any(word in text for word in ["official", "homepage", "website"]):
        score += 8
    if any(word in text for word in ["docs", "documentation", "whitepaper"]):
        score += 5
    if path in {"", "/"}:
        score += 8
    if is_pdf_url(url):
        score -= 10
    if host == "github.com":
        score -= 18
    if is_generic_platform_url(url):
        score -= 24
    if is_low_signal_url(url):
        score -= 30
    return score


def select_best_official_site(project_query: str, web_results: list[Any]) -> str | None:
    scored: list[tuple[int, str]] = []
    for result in web_results:
        if not isinstance(result, dict):
            continue
        url = str(result.get("url") or "")
        context = f"{result.get('title', '')} {result.get('snippet', '')}"
        if not is_primary_project_site(project_query, url, context):
            continue
        scored.append((score_official_url(project_query, result), url))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1] if scored and scored[0][0] > 0 else None


def is_project_github_url(project: Any, url: str | None) -> bool:
    if not is_http_url(url):
        return False
    parsed = urlparse(str(url))
    if normalized_host(str(url)) != "github.com":
        return False
    path = parsed.path.lower()
    if not path or path == "/" or path.startswith(GENERIC_GITHUB_PREFIXES):
        return False
    tokens = project_tokens_from_project(project)
    return url_mentions_project(path, tokens)


def is_relevant_source_url(project: Any, url: str | None, *, label: str = "") -> bool:
    if not is_http_url(url) or is_low_signal_url(url):
        return False
    if is_project_github_url(project, url):
        return True
    if normalized_host(url) == "github.com":
        return False
    tokens = project_tokens_from_project(project)
    if not tokens:
        return True
    host = normalized_host(url)
    text = f"{url} {label}".lower()
    if any(token in host for token in tokens):
        return True
    if any(token in text for token in tokens):
        return True
    return False


def is_project_social_url(project: Any, url: str | None, *, trusted_official_link: bool = False) -> bool:
    if not is_http_url(url):
        return False
    host = normalized_host(url)
    if host not in {"x.com", "twitter.com"}:
        return False
    if trusted_official_link:
        return True
    return url_mentions_project(url, project_tokens_from_project(project))
