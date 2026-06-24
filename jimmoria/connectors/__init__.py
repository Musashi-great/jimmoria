from __future__ import annotations

from jimmoria.connectors.defillama_connector import defillama_protocol_search, defillama_tvl_snapshot
from jimmoria.connectors.explorer_connector import (
    explorer_get_contract_source,
    explorer_get_token_holders,
    explorer_get_token_supply,
    explorer_lookup,
    get_contract_address,
    rpc_read_contract,
)
from jimmoria.connectors.github_connector import github_get_repo_activity, github_search_repos, read_github_repo
from jimmoria.connectors.market_connectors import (
    coingecko_coin_metadata,
    dexscreener_search_pairs,
    get_dex_pair,
    get_token_metadata,
)
from jimmoria.connectors.opportunity_connector import check_airdrop_points
from jimmoria.connectors.operator_bridge import (
    browser_click,
    browser_console,
    browser_navigate,
    browser_scroll,
    browser_snapshot,
    browser_vision,
    cronjob,
    delegate_task,
    execute_code,
    multi_tool_parallel,
    read_file,
    search_files,
    send_message,
    skill_view,
    terminal,
    vision_analyze,
    write_file,
)
from jimmoria.connectors.personal_stack import (
    browser_cdp_click,
    browser_cdp_navigate,
    browser_cdp_snapshot,
    honcho_memory_search,
    honcho_observation_write,
    qmd_text_search,
    qmd_vector_search,
    qmd_vector_upsert,
    tavily_search,
)
from jimmoria.connectors.research_guardrails import (
    source_relevance_filter,
    tool_call_guardrail,
    url_safety_check,
)
from jimmoria.connectors.rss_connector import rss_monitor_feed
from jimmoria.connectors.rootdata_connector import (
    rootdata_get_hot_projects,
    rootdata_get_investors,
    rootdata_get_project,
    rootdata_search_projects,
)
from jimmoria.connectors.snapshot_connector import snapshot_get_proposals
from jimmoria.connectors.supervisor_tools import (
    agent_handoff,
    assign_task,
    create_research_room,
    create_task,
    read_agent_status,
    task_cancel,
    task_retry,
    update_task_status,
)
from jimmoria.connectors.url_fetcher import (
    archive_source_snapshot,
    crawl_docs,
    crawl_website,
    fetch_url,
    parse_html,
)
from jimmoria.connectors.web_search import web_search
from jimmoria.connectors.x_social import x_build_kol_list, x_get_user_timeline, x_search_posts
from jimmoria.core.tool_gateway import ToolGateway


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
    tool_gateway.register("delegate_task", delegate_task)
    tool_gateway.register("fetch_url", fetch_url)
    tool_gateway.register("skill_view", skill_view)
    tool_gateway.register("read_file", read_file)
    tool_gateway.register("search_files", search_files)
    tool_gateway.register("write_file", write_file)
    tool_gateway.register("execute_code", execute_code)
    tool_gateway.register("terminal", terminal)
    tool_gateway.register("browser_navigate", browser_navigate)
    tool_gateway.register("browser_console", browser_console)
    tool_gateway.register("browser_snapshot", browser_snapshot)
    tool_gateway.register("browser_scroll", browser_scroll)
    tool_gateway.register("browser_click", browser_click)
    tool_gateway.register("browser_cdp_navigate", browser_cdp_navigate)
    tool_gateway.register("browser_cdp_snapshot", browser_cdp_snapshot)
    tool_gateway.register("browser_cdp_click", browser_cdp_click)
    tool_gateway.register("browser_vision", browser_vision)
    tool_gateway.register("vision_analyze", vision_analyze)
    tool_gateway.register("cronjob", cronjob)
    tool_gateway.register("send_message", send_message)
    tool_gateway.register("multi_tool_use.parallel", multi_tool_parallel)
    tool_gateway.register("parse_html", parse_html)
    tool_gateway.register("archive_source_snapshot", archive_source_snapshot)
    tool_gateway.register("crawl_website", crawl_website)
    tool_gateway.register("crawl_docs", crawl_docs)
    tool_gateway.register("web_search", web_search)
    tool_gateway.register("tavily_search", tavily_search)
    tool_gateway.register("honcho_memory_search", honcho_memory_search)
    tool_gateway.register("honcho_observation_write", honcho_observation_write)
    tool_gateway.register("qmd_text_search", qmd_text_search)
    tool_gateway.register("qmd_vector_search", qmd_vector_search)
    tool_gateway.register("qmd_vector_upsert", qmd_vector_upsert)
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
    tool_gateway.register("url_safety_check", url_safety_check)
    tool_gateway.register("source_relevance_filter", source_relevance_filter)
    tool_gateway.register("tool_call_guardrail", tool_call_guardrail)


__all__ = ["register_default_connectors"]
