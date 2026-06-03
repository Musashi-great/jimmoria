from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from crypto_research_agents.connectors.base import failed, missing_input, missing_secret, success


TELEGRAM_API = "https://api.telegram.org"


def telegram_read_channel(
    channel: str | None = None,
    *,
    chat_id: str | int | None = None,
    limit: int = 25,
    offset: int | None = None,
) -> dict[str, Any]:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or ""
    if not token:
        return missing_secret("telegram_read_channel", "TELEGRAM_BOT_TOKEN", "Set TELEGRAM_BOT_TOKEN to read bot-visible Telegram updates.")
    target = str(chat_id or channel or os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not target:
        return missing_input(
            "telegram_read_channel",
            "channel/chat_id is required. Bot API can only return updates visible to the bot.",
        )
    params: dict[str, str] = {"limit": str(max(1, min(int(limit or 25), 100)))}
    if offset is not None:
        params["offset"] = str(offset)
    response = _telegram_get(token, "getUpdates", params)
    if response.get("status") != "success":
        response["tool"] = "telegram_read_channel"
        return response
    raw_updates = response.get("data", {}).get("result") if isinstance(response.get("data"), dict) else []
    messages = [_message_from_update(update) for update in raw_updates if isinstance(update, dict)]
    filtered = [message for message in messages if _matches_target(message, target)]
    return success(
        "telegram_read_channel",
        {
            "target": target,
            "messages": filtered[-limit:],
            "updates_seen": len(messages),
            "note": "Bot API only exposes recent updates/channels visible to the bot; it cannot scrape arbitrary public channel history.",
        },
        "Telegram bot-visible channel updates read",
    )


def telegram_search_public_channels(query: str | None = None, *, limit: int = 10) -> dict[str, Any]:
    if not query:
        return missing_input("telegram_search_public_channels", "query is required")
    return missing_input(
        "telegram_search_public_channels",
        "Telegram Bot API does not provide public channel search. Use web_search or a configured Telegram client API session.",
        {"query": query, "limit": limit},
    )


def _telegram_get(token: str, method: str, params: dict[str, str]) -> dict[str, Any]:
    url = f"{TELEGRAM_API}/bot{token}/{method}?{urlencode(params)}"
    request = Request(url, headers={"User-Agent": "jimmoria-cli"})
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return failed("telegram_api", f"Telegram API request failed: HTTP {exc.code}", {"method": method, "detail": detail[:1000]})
    except (URLError, TimeoutError, OSError) as exc:
        return failed("telegram_api", f"Telegram API request failed: {exc}", {"method": method})
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        return failed("telegram_api", f"Telegram API returned invalid JSON: {exc}", {"method": method})
    if not data.get("ok"):
        return failed("telegram_api", str(data.get("description") or "Telegram API returned ok=false"), {"method": method, "response": data})
    return success("telegram_api", data, "Telegram API response")


def _message_from_update(update: dict[str, Any]) -> dict[str, Any]:
    message = (
        update.get("channel_post")
        or update.get("edited_channel_post")
        or update.get("message")
        or update.get("edited_message")
        or {}
    )
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    return {
        "update_id": update.get("update_id"),
        "message_id": message.get("message_id"),
        "date": message.get("date"),
        "text": message.get("text") or message.get("caption") or "",
        "chat_id": chat.get("id"),
        "chat_username": chat.get("username"),
        "chat_title": chat.get("title"),
        "chat_type": chat.get("type"),
    }


def _matches_target(message: dict[str, Any], target: str) -> bool:
    normalized = target.strip().lstrip("@").lower()
    if not normalized:
        return False
    chat_id = str(message.get("chat_id") or "").lower()
    username = str(message.get("chat_username") or "").lower()
    title = str(message.get("chat_title") or "").lower()
    return normalized in {chat_id, username} or normalized in title
