from __future__ import annotations

from email.utils import parsedate_to_datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from jimmoria.connectors.base import failed, missing_input, success


def rss_monitor_feed(feed_url: str | None = None, *, limit: int = 20) -> dict[str, Any]:
    if not feed_url:
        return missing_input("rss_monitor_feed", "feed_url is required")

    try:
        from feedparser import parse as parse_feed  # type: ignore
    except ImportError:
        return _rss_monitor_feed_stdlib(feed_url, limit=limit)

    try:
        parsed = parse_feed(feed_url)
    except Exception as exc:
        return failed("rss_monitor_feed", f"feedparser failed: {exc}", {"feed_url": feed_url})
    entries = [
        {
            "title": str(getattr(entry, "title", "") or ""),
            "url": str(getattr(entry, "link", "") or ""),
            "published": str(getattr(entry, "published", "") or getattr(entry, "updated", "") or ""),
            "summary": _compact(str(getattr(entry, "summary", "") or "")),
        }
        for entry in list(getattr(parsed, "entries", []) or [])[: max(1, min(limit, 50))]
    ]
    return success(
        "rss_monitor_feed",
        {
            "feed_url": feed_url,
            "title": str((getattr(parsed, "feed", {}) or {}).get("title", "")),
            "entries": entries,
        },
        "rss feed monitored",
    )


def _rss_monitor_feed_stdlib(feed_url: str, *, limit: int) -> dict[str, Any]:
    request = Request(feed_url, headers={"User-Agent": "jimmoria-cli"})
    try:
        with urlopen(request, timeout=12) as response:
            raw = response.read(1_500_000)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return failed("rss_monitor_feed", f"feed request failed: {exc}", {"feed_url": feed_url})
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        return failed("rss_monitor_feed", f"invalid feed XML: {exc}", {"feed_url": feed_url})

    channel = root.find("channel")
    if channel is not None:
        items = channel.findall("item")
        entries = [_rss_item(item) for item in items[: max(1, min(limit, 50))]]
        title = _node_text(channel, "title")
    else:
        items = root.findall("{http://www.w3.org/2005/Atom}entry")
        entries = [_atom_item(item) for item in items[: max(1, min(limit, 50))]]
        title = _node_text(root, "{http://www.w3.org/2005/Atom}title")
    return success("rss_monitor_feed", {"feed_url": feed_url, "title": title, "entries": entries}, "rss feed monitored")


def _rss_item(item: ElementTree.Element) -> dict[str, Any]:
    return {
        "title": _node_text(item, "title"),
        "url": _node_text(item, "link"),
        "published": _normalize_date(_node_text(item, "pubDate")),
        "summary": _compact(_node_text(item, "description")),
    }


def _atom_item(item: ElementTree.Element) -> dict[str, Any]:
    link = ""
    for node in item.findall("{http://www.w3.org/2005/Atom}link"):
        if node.attrib.get("href"):
            link = node.attrib["href"]
            break
    return {
        "title": _node_text(item, "{http://www.w3.org/2005/Atom}title"),
        "url": link,
        "published": _node_text(item, "{http://www.w3.org/2005/Atom}updated")
        or _node_text(item, "{http://www.w3.org/2005/Atom}published"),
        "summary": _compact(_node_text(item, "{http://www.w3.org/2005/Atom}summary")),
    }


def _node_text(parent: ElementTree.Element, name: str) -> str:
    node = parent.find(name)
    return " ".join((node.text or "").split()) if node is not None else ""


def _normalize_date(value: str) -> str:
    if not value:
        return ""
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError):
        return value


def _compact(value: str, *, limit: int = 500) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."
