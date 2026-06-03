from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent_spec import AgentSpecRegistry
from .concurrency import load_concurrency_policy
from .llm_provider import codex_sdk_available, provider_from_env
from .model_gateway import ModelGateway
from .tool_gateway import ToolGateway
from .profile import WorkerProfileRegistry
from .scheduler import CronRegistry
from crypto_research_agents.tools.registry import load_tool_registry


@dataclass(slots=True)
class CapabilityStatus:
    name: str
    status: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


def collect_capabilities(
    tool_gateway: ToolGateway | None = None,
    *,
    agent_spec_dir: str | Path = "config/agents",
    memory_path: str | Path = "data/memory.json",
    runs_dir: str | Path = "data/runs",
    vault_dir: str | Path = "vault",
    reports_dir: str | Path = "reports",
) -> list[CapabilityStatus]:
    gateway = tool_gateway or ToolGateway()
    if tool_gateway is None:
        from crypto_research_agents.connectors import register_default_connectors

        register_default_connectors(gateway)
    provider = provider_from_env()
    agent_spec_status = _agent_spec_status(agent_spec_dir)
    tool_registry = load_tool_registry()
    concurrency_policy = load_concurrency_policy()
    cron_registry = CronRegistry.load()
    profile_registry = WorkerProfileRegistry.load()
    capabilities = [
        CapabilityStatus(
            "Runtime scaffold",
            "configured",
            "ResearchRoom, Agent Bus, Shared Memory, Tool Gateway, and Model Gateway are available",
        ),
        agent_spec_status,
        CapabilityStatus(
            "Shared memory JSON",
            "configured",
            _path_detail(memory_path, "created on first run"),
        ),
        CapabilityStatus(
            "Run snapshots",
            "configured",
            _path_detail(runs_dir, "created after each run"),
        ),
        CapabilityStatus(
            "Report writer",
            "configured",
            _path_detail(reports_dir, "created when report_agent writes a report"),
        ),
        CapabilityStatus(
            "Obsidian vault writer",
            "configured",
            _path_detail(vault_dir, "created when obsidian_curator_agent syncs notes"),
        ),
        CapabilityStatus(
            "LLM provider",
            "configured" if provider.provider_name != "offline_fallback" else "fallback",
            provider.provider_name,
        ),
        _agent_llm_routing_status(provider),
        CapabilityStatus(
            "Codex SDK package",
            "configured" if codex_sdk_available() else "missing",
            "openai-codex installed" if codex_sdk_available() else "install with: pip install openai-codex",
        ),
        CapabilityStatus(
            "Codex CLI",
            "configured" if shutil.which("codex") else "missing",
            "codex command found on PATH" if shutil.which("codex") else "install Codex CLI or use the SDK package runtime",
        ),
        CapabilityStatus(
            "Tool registry",
            "configured" if tool_registry.definitions else "missing",
            f"{len(tool_registry.definitions)} tools, {len(tool_registry.toolsets)} toolsets",
        ),
        CapabilityStatus(
            "Concurrency phase",
            "configured",
            (
                f"Phase {concurrency_policy.active.phase}: {concurrency_policy.active.name} "
                f"({concurrency_policy.active.mode}, max_parallel={concurrency_policy.active.max_parallel})"
            ),
        ),
        CapabilityStatus(
            "Scheduled jobs",
            "configured" if cron_registry.jobs else "missing",
            f"{len(cron_registry.jobs)} jobs configured",
        ),
        CapabilityStatus(
            "Worker profiles",
            "configured" if profile_registry.profiles else "missing",
            f"{len(profile_registry.profiles)} profiles configured",
        ),
        _writable_directory_status(Path(runs_dir), "Artifact directory"),
    ]

    tool_specs = [
        ("Supervisor room opener", "create_research_room"),
        ("Supervisor task creator", "create_task"),
        ("Supervisor task assignment", "assign_task"),
        ("Supervisor handoff", "agent_handoff"),
        ("Supervisor task status", "update_task_status"),
        ("Public web search", "web_search"),
        ("X/Twitter search", "x_search_posts"),
        ("X/KOL timeline", "x_get_user_timeline"),
        ("X/KOL list builder", "x_build_kol_list"),
        ("RSS feed monitor", "rss_monitor_feed"),
        ("RootData project directory", "rootdata_search_projects"),
        ("CoinGecko metadata", "coingecko_coin_metadata"),
        ("DefiLlama protocol data", "defillama_protocol_search"),
        ("Explorer contract lookup", "get_contract_address"),
        ("Explorer metadata", "explorer_lookup"),
        ("DEX pair lookup", "get_dex_pair"),
        ("DEX Screener pair search", "dexscreener_search_pairs"),
        ("Docs crawler", "crawl_docs"),
        ("GitHub reader", "read_github_repo"),
        ("GitHub repo search", "github_search_repos"),
        ("Funding/airdrop checker", "check_airdrop_points"),
        ("Snapshot governance API", "snapshot_get_proposals"),
        ("Dune query execution", "dune_execute_query"),
        ("The Graph subgraph query", "thegraph_query_subgraph"),
    ]
    for label, tool_name in tool_specs:
        availability = tool_registry.availability(tool_name, registered_connectors=gateway.registered_tools)
        capabilities.append(
            CapabilityStatus(
                label,
                availability.status,
                availability.detail,
            )
        )
    capabilities.append(_overall_status(capabilities))
    return capabilities


def _agent_llm_routing_status(provider: Any) -> CapabilityStatus:
    gateway = ModelGateway(provider=provider)
    reasoning = gateway.select(agent_id="discovery_agent", task_type="candidate_discovery").selected_model
    writing = gateway.select(agent_id="report_agent", task_type="final_synthesis").selected_model
    source = gateway.select(agent_id="ingestion_agent", task_type="source_ingestion").selected_model
    status = "configured" if provider.provider_name != "offline_fallback" else "fallback"
    return CapabilityStatus(
        "Agent LLM routing",
        status,
        (
            "10 core agents call ModelGateway; "
            f"source={source}, reasoning={reasoning}, writing={writing}"
        ),
    )


def _agent_spec_status(agent_spec_dir: str | Path) -> CapabilityStatus:
    required_agents = {
        "supervisor_agent",
        "ingestion_agent",
        "narrative_agent",
        "discovery_agent",
        "social_kol_agent",
        "contract_onchain_agent",
        "product_tech_agent",
        "funding_token_agent",
        "report_agent",
        "obsidian_curator_agent",
    }
    try:
        registry = AgentSpecRegistry.load_dir(agent_spec_dir)
    except Exception as exc:
        return CapabilityStatus("Agent specs/personas", "missing", f"failed to load: {exc}")

    missing = sorted(required_agents - set(registry.specs))
    if missing:
        return CapabilityStatus(
            "Agent specs/personas",
            "missing",
            f"missing specs: {', '.join(missing)}",
        )
    return CapabilityStatus(
        "Agent specs/personas",
        "configured",
        f"{len(registry.specs)} specs loaded from {Path(agent_spec_dir)}",
    )


def _path_detail(path: str | Path, missing_note: str) -> str:
    target = Path(path)
    if target.exists():
        return f"{target} exists"
    return f"{target} not found yet; {missing_note}"


def _writable_directory_status(path: Path, name: str) -> CapabilityStatus:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".jimmoria_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        return CapabilityStatus(name, "missing", f"not writable: {exc}")
    return CapabilityStatus(name, "configured", f"{path} is writable")


def _overall_status(capabilities: list[CapabilityStatus]) -> CapabilityStatus:
    placeholders = [
        item.name
        for item in capabilities
        if item.status in {"placeholder", "missing_secret", "missing_connector"}
        and item.name
        not in {
            "Overall",
        }
    ]
    if placeholders:
        return CapabilityStatus(
            "Overall",
            "placeholder",
            "Core runtime and low-cost connectors run; some live research connectors need API secrets or are still placeholders.",
        )
    return CapabilityStatus(
        "Overall",
        "configured",
        "Core runtime and live connector checks are configured.",
    )
