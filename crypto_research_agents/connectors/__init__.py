from __future__ import annotations

from crypto_research_agents.connectors.github_connector import github_search_repos, read_github_repo
from crypto_research_agents.connectors.market_connectors import coingecko_coin_metadata, dexscreener_search_pairs
from crypto_research_agents.connectors.url_fetcher import (
    archive_source_snapshot,
    crawl_docs,
    crawl_website,
    fetch_url,
    parse_html,
)
from crypto_research_agents.connectors.web_search import web_search
from crypto_research_agents.core.tool_gateway import ToolGateway


def register_default_connectors(tool_gateway: ToolGateway) -> None:
    """Attach low-cost public research connectors to the ToolGateway."""

    tool_gateway.register("fetch_url", fetch_url)
    tool_gateway.register("parse_html", parse_html)
    tool_gateway.register("archive_source_snapshot", archive_source_snapshot)
    tool_gateway.register("crawl_website", crawl_website)
    tool_gateway.register("crawl_docs", crawl_docs)
    tool_gateway.register("web_search", web_search)
    tool_gateway.register("github_search_repos", github_search_repos)
    tool_gateway.register("read_github_repo", read_github_repo)
    tool_gateway.register("dexscreener_search_pairs", dexscreener_search_pairs)
    tool_gateway.register("coingecko_coin_metadata", coingecko_coin_metadata)


__all__ = ["register_default_connectors"]
