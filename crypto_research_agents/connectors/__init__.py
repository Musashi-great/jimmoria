from __future__ import annotations

from crypto_research_agents.connectors.defillama_connector import defillama_protocol_search, defillama_tvl_snapshot
from crypto_research_agents.connectors.explorer_connector import (
    explorer_get_contract_source,
    explorer_get_token_holders,
    explorer_get_token_supply,
    explorer_lookup,
    get_contract_address,
    rpc_read_contract,
)
from crypto_research_agents.connectors.github_connector import github_get_repo_activity, github_search_repos, read_github_repo
from crypto_research_agents.connectors.market_connectors import (
    coingecko_coin_metadata,
    dexscreener_search_pairs,
    get_dex_pair,
    get_token_metadata,
)
from crypto_research_agents.connectors.opportunity_connector import check_airdrop_points
from crypto_research_agents.connectors.rss_connector import rss_monitor_feed
from crypto_research_agents.connectors.rootdata_connector import (
    rootdata_get_hot_projects,
    rootdata_get_investors,
    rootdata_get_project,
    rootdata_search_projects,
)
from crypto_research_agents.connectors.snapshot_connector import snapshot_get_proposals
from crypto_research_agents.connectors.supervisor_tools import (
    agent_handoff,
    assign_task,
    create_research_room,
    create_task,
    read_agent_status,
    task_cancel,
    task_retry,
    update_task_status,
)
from crypto_research_agents.connectors.url_fetcher import (
    archive_source_snapshot,
    crawl_docs,
    crawl_website,
    fetch_url,
    parse_html,
)
from crypto_research_agents.connectors.web_search import web_search
from crypto_research_agents.connectors.x_social import x_build_kol_list, x_get_user_timeline, x_search_posts
from crypto_research_agents.core.tool_gateway import ToolGateway


def register_default_connectors(tool_gateway: ToolGateway) -> None:
    """Attach low-cost public research connectors to the ToolGateway."""

    tool_gateway.register("create_research_room", create_research_room)
    tool_gateway.register("create_task", create_task)
    tool_gateway.register("assign_task", assign_task)
    tool_gateway.register("agent_handoff", agent_handoff)
    tool_gateway.register("update_task_status", update_task_status)
    tool_gateway.register("read_agent_status", read_agent_status)
    tool_gateway.register("task_retry", task_retry)
    tool_gateway.register("task_cancel", task_cancel)
    tool_gateway.register("fetch_url", fetch_url)
    tool_gateway.register("parse_html", parse_html)
    tool_gateway.register("archive_source_snapshot", archive_source_snapshot)
    tool_gateway.register("crawl_website", crawl_website)
    tool_gateway.register("crawl_docs", crawl_docs)
    tool_gateway.register("web_search", web_search)
    tool_gateway.register("github_search_repos", github_search_repos)
    tool_gateway.register("read_github_repo", read_github_repo)
    tool_gateway.register("github_get_repo_activity", github_get_repo_activity)
    tool_gateway.register("dexscreener_search_pairs", dexscreener_search_pairs)
    tool_gateway.register("coingecko_coin_metadata", coingecko_coin_metadata)
    tool_gateway.register("get_dex_pair", get_dex_pair)
    tool_gateway.register("get_token_metadata", get_token_metadata)
    tool_gateway.register("defillama_protocol_search", defillama_protocol_search)
    tool_gateway.register("defillama_tvl_snapshot", defillama_tvl_snapshot)
    tool_gateway.register("snapshot_get_proposals", snapshot_get_proposals)
    tool_gateway.register("rss_monitor_feed", rss_monitor_feed)
    tool_gateway.register("x_search_posts", x_search_posts)
    tool_gateway.register("x_get_user_timeline", x_get_user_timeline)
    tool_gateway.register("x_build_kol_list", x_build_kol_list)
    tool_gateway.register("rootdata_search_projects", rootdata_search_projects)
    tool_gateway.register("rootdata_get_project", rootdata_get_project)
    tool_gateway.register("rootdata_get_investors", rootdata_get_investors)
    tool_gateway.register("rootdata_get_hot_projects", rootdata_get_hot_projects)
    tool_gateway.register("get_contract_address", get_contract_address)
    tool_gateway.register("explorer_lookup", explorer_lookup)
    tool_gateway.register("explorer_get_contract_source", explorer_get_contract_source)
    tool_gateway.register("explorer_get_token_supply", explorer_get_token_supply)
    tool_gateway.register("explorer_get_token_holders", explorer_get_token_holders)
    tool_gateway.register("rpc_read_contract", rpc_read_contract)
    tool_gateway.register("check_airdrop_points", check_airdrop_points)


__all__ = ["register_default_connectors"]
