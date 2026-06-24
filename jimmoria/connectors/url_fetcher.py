from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

from jimmoria import __version__
from jimmoria.connectors.base import failed, missing_input, success


MAX_FETCH_BYTES = 1_500_000
MAX_TEXT_CHARS = 40_000


def fetch_url(url: str | None = None, *, timeout: int = 20, max_bytes: int = MAX_FETCH_BYTES) -> dict[str, Any]:
    if not url:
        return missing_input("fetch_url", "url is required")

    request = Request(str(url), headers={"User-Agent": f"jimmoria-cli/{__version__}"})
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
            charset = response.headers.get_content_charset() or "utf-8"
            final_url = response.geturl()
            content_type = response.headers.get("content-type", "")
            status_code = getattr(response, "status", 200)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return failed("fetch_url", f"failed to fetch {url}: {exc}", {"url": url})

    truncated = len(raw) > max_bytes
    raw = raw[:max_bytes]
    text = raw.decode(charset, errors="replace")
    parsed = _parse_html_content(text, final_url)
    data = {
        "url": url,
        "final_url": final_url,
        "canonical_url": canonicalize_url(final_url),
        "status_code": status_code,
        "content_type": content_type,
        "charset": charset,
        "truncated": truncated,
        "content_hash": content_hash(text),
        "title": parsed["title"],
        "meta_description": parsed["meta_description"],
        "text": parsed["text"][:MAX_TEXT_CHARS],
        "links": parsed["links"],
        "official_links": parsed["official_links"],
        "signals": extract_product_signals(parsed["text"]),
    }
    return success("fetch_url", data, f"fetched {final_url}")


def parse_html(html: str | None = None, *, base_url: str | None = None) -> dict[str, Any]:
    if not html:
        return missing_input("parse_html", "html is required")
    parsed = _parse_html_content(html, base_url)
    return success(
        "parse_html",
        {
            **parsed,
            "content_hash": content_hash(html),
            "signals": extract_product_signals(parsed["text"]),
        },
        "parsed html",
    )


def crawl_website(
    url: str | None = None,
    *,
    project_name: str | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    if not url:
        return missing_input(
            "crawl_website",
            "url is required to crawl a website",
            {"project_name": project_name},
        )

    result = fetch_url(url, timeout=timeout)
    if result.get("status") != "success":
        result["tool"] = "crawl_website"
        return result

    data = dict(result.get("data") or {})
    official_links = data.get("official_links") if isinstance(data.get("official_links"), dict) else {}
    signals = data.get("signals") if isinstance(data.get("signals"), dict) else {}
    data.update(
        {
            "project_name": project_name,
            "product_status": classify_product_status(data.get("text", ""), official_links),
            "docs_status": "linked" if official_links.get("docs") else "not_found",
            "github_status": "linked" if official_links.get("github") else "not_found",
            "x_status": "linked" if official_links.get("x") else "not_found",
            "points_or_airdrop_hint": bool(signals.get("points_or_airdrop")),
        }
    )
    return success("crawl_website", data, "website crawled")


def crawl_docs(
    url: str | None = None,
    *,
    website_url: str | None = None,
    project_name: str | None = None,
    max_pages: int = 3,
) -> dict[str, Any]:
    candidate_urls: list[str] = []
    if url:
        candidate_urls.append(url)
    elif website_url:
        website = crawl_website(website_url, project_name=project_name)
        website_data = website.get("data") if isinstance(website.get("data"), dict) else {}
        official_links = website_data.get("official_links") if isinstance(website_data.get("official_links"), dict) else {}
        candidate_urls.extend(str(item["url"]) for item in official_links.get("docs", []) if isinstance(item, dict))
        if not candidate_urls:
            return success(
                "crawl_docs",
                {
                    "project_name": project_name,
                    "website_url": website_url,
                    "docs_status": "not_found",
                    "pages": [],
                    "technical_keywords": [],
                    "signals": {},
                },
                "no docs link found on website",
            )
    else:
        return missing_input(
            "crawl_docs",
            "url or website_url is required to crawl docs",
            {"project_name": project_name},
        )

    pages = []
    all_text = []
    for docs_url in candidate_urls[:max_pages]:
        fetched = fetch_url(docs_url)
        fetched_data = fetched.get("data") if isinstance(fetched.get("data"), dict) else {}
        pages.append(
            {
                "url": docs_url,
                "status": fetched.get("status"),
                "title": fetched_data.get("title", ""),
                "meta_description": fetched_data.get("meta_description", ""),
                "content_hash": fetched_data.get("content_hash", ""),
            }
        )
        if fetched.get("status") == "success":
            all_text.append(str(fetched_data.get("text", "")))

    combined_text = "\n".join(all_text)
    data = {
        "project_name": project_name,
        "docs_status": "live" if all_text else "fetch_failed",
        "pages": pages,
        "technical_keywords": extract_technical_keywords(combined_text),
        "signals": extract_product_signals(combined_text),
    }
    return success("crawl_docs", data, "docs crawled")


def extract_official_links(links: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    buckets: dict[str, list[dict[str, str]]] = {
        "docs": [],
        "github": [],
        "x": [],
        "discord": [],
        "telegram": [],
        "app": [],
    }
    for link in links:
        href = link.get("url", "")
        text = f"{link.get('text', '')} {href}".lower()
        host = urlparse(href).netloc.lower()
        if "github.com" in host:
            buckets["github"].append(link)
        if host in {"x.com", "twitter.com"} or "twitter.com" in host:
            buckets["x"].append(link)
        if "discord" in host or "discord" in text:
            buckets["discord"].append(link)
        if "t.me" in host or "telegram" in host or "telegram" in text:
            buckets["telegram"].append(link)
        if any(keyword in text for keyword in ["docs", "documentation", "gitbook", "docusaurus"]):
            buckets["docs"].append(link)
        if any(keyword in text for keyword in ["app.", "launch app", "dashboard", "waitlist", "compute platform", "sign-in", "buy"]):
            buckets["app"].append(link)
    return {key: _dedupe_links(value) for key, value in buckets.items()}


def archive_source_snapshot(
    content: str | None = None,
    *,
    url: str | None = None,
    root_dir: str = "data/source_snapshots",
) -> dict[str, Any]:
    if not content:
        return missing_input("archive_source_snapshot", "content is required")
    digest = content_hash(content)
    target_dir = Path(root_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{digest}.txt"
    target.write_text(content, encoding="utf-8")
    return success(
        "archive_source_snapshot",
        {"raw_path": str(target), "content_hash": digest, "url": url},
        "source snapshot archived",
    )


def canonicalize_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(str(url).strip())
    if not parsed.scheme or not parsed.netloc:
        return str(url).strip()
    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        fragment="",
    )
    value = urlunparse(normalized)
    return value.rstrip("/")


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def classify_product_status(text: str, official_links: dict[str, Any]) -> str:
    lowered = text.lower()
    if "mainnet" in lowered or "launch app" in lowered or official_links.get("app"):
        return "app_live"
    if "testnet" in lowered:
        return "testnet"
    if "waitlist" in lowered:
        return "waitlist"
    if official_links.get("docs"):
        return "docs_live"
    return "unknown"


def extract_product_signals(text: str) -> dict[str, list[str]]:
    lowered = text.lower()
    groups = {
        "stage": ["mainnet", "testnet", "beta", "alpha", "waitlist", "launch app"],
        "points_or_airdrop": ["points", "airdrop", "rewards", "quest", "campaign"],
        "token": ["token", "ticker", "tge", "contract address", "ca:"],
        "technical": ["api", "sdk", "github", "docs", "smart contract", "repository"],
    }
    return {
        name: [keyword for keyword in keywords if keyword in lowered]
        for name, keywords in groups.items()
    }


def extract_technical_keywords(text: str) -> list[str]:
    candidates = [
        "api",
        "sdk",
        "graphql",
        "rest",
        "smart contract",
        "solidity",
        "rust",
        "typescript",
        "wallet",
        "oracle",
        "bridge",
        "indexer",
        "subgraph",
    ]
    lowered = text.lower()
    return [keyword for keyword in candidates if keyword in lowered]


def _parse_html_content(html: str, base_url: str | None = None) -> dict[str, Any]:
    parser = _MetadataHTMLParser(base_url=base_url)
    parser.feed(html)
    text = " ".join(part.strip() for part in parser.text_parts if part.strip())
    links = _dedupe_links(parser.links)
    return {
        "title": parser.title.strip(),
        "meta_description": parser.meta_description.strip(),
        "text": re.sub(r"\s+", " ", text),
        "links": links,
        "official_links": extract_official_links(links),
    }


def _dedupe_links(links: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for link in links:
        href = canonicalize_url(link.get("url")) or ""
        if not href or href in seen:
            continue
        seen.add(href)
        result.append({"url": href, "text": link.get("text", "").strip()})
    return result


class _MetadataHTMLParser(HTMLParser):
    def __init__(self, *, base_url: str | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title = ""
        self.meta_description = ""
        self.links: list[dict[str, str]] = []
        self.text_parts: list[str] = []
        self._in_title = False
        self._current_link: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "title":
            self._in_title = True
        elif tag.lower() == "meta":
            name = (attrs_dict.get("name") or attrs_dict.get("property") or "").lower()
            if name in {"description", "og:description", "twitter:description"}:
                self.meta_description = attrs_dict.get("content", self.meta_description)
        elif tag.lower() == "a":
            href = attrs_dict.get("href", "")
            if href:
                self._current_link = {"url": urljoin(self.base_url or "", href), "text": ""}

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False
        elif tag.lower() == "a" and self._current_link is not None:
            self.links.append(self._current_link)
            self._current_link = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._current_link is not None:
            self._current_link["text"] += data
        if data.strip():
            self.text_parts.append(data)
