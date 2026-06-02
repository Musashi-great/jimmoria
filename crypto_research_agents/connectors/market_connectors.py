from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from crypto_research_agents.connectors.base import failed, missing_input, success


def dexscreener_search_pairs(query: str | None = None, *, limit: int = 10) -> dict[str, Any]:
    if not query:
        return missing_input("dexscreener_search_pairs", "query is required")
    response = _fetch_json(f"https://api.dexscreener.com/latest/dex/search?q={quote_plus(query)}")
    if response.get("status") != "success":
        response["tool"] = "dexscreener_search_pairs"
        return response
    pairs = response["data"].get("pairs") or []
    simplified = [_dex_pair_summary(pair) for pair in pairs[:limit] if isinstance(pair, dict)]
    return success("dexscreener_search_pairs", {"query": query, "pairs": simplified}, "DEX pairs searched")


def coingecko_coin_metadata(
    query: str | None = None,
    *,
    coin_id: str | None = None,
    include_detail: bool = False,
) -> dict[str, Any]:
    if coin_id:
        detail = _fetch_json(f"https://api.coingecko.com/api/v3/coins/{quote_plus(coin_id)}")
        if detail.get("status") != "success":
            detail["tool"] = "coingecko_coin_metadata"
            return detail
        return success("coingecko_coin_metadata", _coin_detail_summary(detail["data"]), "CoinGecko coin metadata fetched")
    if not query:
        return missing_input("coingecko_coin_metadata", "query or coin_id is required")

    search = _fetch_json(f"https://api.coingecko.com/api/v3/search?query={quote_plus(query)}")
    if search.get("status") != "success":
        search["tool"] = "coingecko_coin_metadata"
        return search
    coins = search["data"].get("coins", [])
    simplified = [
        {
            "id": coin.get("id"),
            "name": coin.get("name"),
            "symbol": coin.get("symbol"),
            "market_cap_rank": coin.get("market_cap_rank"),
            "thumb": coin.get("thumb"),
        }
        for coin in coins[:10]
        if isinstance(coin, dict)
    ]
    data: dict[str, Any] = {"query": query, "coins": simplified}
    if include_detail and simplified:
        first_id = simplified[0].get("id")
        if first_id:
            detail = coingecko_coin_metadata(coin_id=str(first_id))
            data["top_detail"] = detail.get("data")
    return success("coingecko_coin_metadata", data, "CoinGecko searched")


def _fetch_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "jimmoria-cli", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return failed("http_json", f"request failed: {exc}", {"url": url})
    try:
        return success("http_json", json.loads(raw.decode("utf-8")), "json response")
    except json.JSONDecodeError as exc:
        return failed("http_json", f"invalid JSON: {exc}", {"url": url})


def _dex_pair_summary(pair: dict[str, Any]) -> dict[str, Any]:
    return {
        "chain": pair.get("chainId"),
        "dex": pair.get("dexId"),
        "pair_address": pair.get("pairAddress"),
        "url": pair.get("url"),
        "base_token": pair.get("baseToken"),
        "quote_token": pair.get("quoteToken"),
        "price_usd": pair.get("priceUsd"),
        "liquidity_usd": (pair.get("liquidity") or {}).get("usd") if isinstance(pair.get("liquidity"), dict) else None,
        "volume_24h": (pair.get("volume") or {}).get("h24") if isinstance(pair.get("volume"), dict) else None,
        "fdv": pair.get("fdv"),
        "pair_created_at": pair.get("pairCreatedAt"),
    }


def _coin_detail_summary(data: dict[str, Any]) -> dict[str, Any]:
    links = data.get("links") if isinstance(data.get("links"), dict) else {}
    return {
        "id": data.get("id"),
        "symbol": data.get("symbol"),
        "name": data.get("name"),
        "asset_platform_id": data.get("asset_platform_id"),
        "contract_address": data.get("contract_address"),
        "categories": data.get("categories", []),
        "description": (data.get("description") or {}).get("en", "")[:1000] if isinstance(data.get("description"), dict) else "",
        "homepage": links.get("homepage", []) if isinstance(links, dict) else [],
        "repos_url": links.get("repos_url", {}) if isinstance(links, dict) else {},
        "twitter_screen_name": links.get("twitter_screen_name") if isinstance(links, dict) else None,
    }
