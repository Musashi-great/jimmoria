from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from crypto_research_agents.agents import (
    ContractOnchainAgent,
    DiscoveryAgent,
    FundingTokenAgent,
    IngestionAgent,
    NarrativeAgent,
    ObsidianCuratorAgent,
    ProductTechAgent,
    ReportAgent,
    SocialKOLAgent,
    SupervisorAgent,
)
from crypto_research_agents.connectors import register_default_connectors
from crypto_research_agents.core.bus import CollaborationBus
from crypto_research_agents.core.agent_spec import AgentSpecRegistry
from crypto_research_agents.core.company_settings import company_settings_path_for, load_company_settings
from crypto_research_agents.core.hooks import HookEngine
from crypto_research_agents.core.memory import SharedMemory
from crypto_research_agents.core.model_gateway import ModelGateway
from crypto_research_agents.core.room import ResearchRoom
from crypto_research_agents.core.runtime_state import RuntimeState
from crypto_research_agents.core.tool_gateway import PolicyEngine, ToolGateway
from crypto_research_agents.storage.json_store import save_memory
from crypto_research_agents.storage.run_store import save_run_snapshot


DEFAULT_AGENTS = [
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
]


@dataclass(slots=True)
class ResearchRunResult:
    room: ResearchRoom
    memory: SharedMemory
    bus: CollaborationBus


class ResearchRuntime:
    def __init__(
        self,
        memory: SharedMemory | None = None,
        *,
        agent_spec_dir: str | Path = "config/agents",
    ) -> None:
        self.memory = memory or SharedMemory()
        self.bus = CollaborationBus()
        self.hooks = HookEngine()
        self.model_gateway = ModelGateway()
        self.agent_specs = AgentSpecRegistry.load_dir(agent_spec_dir)
        self.tool_gateway = ToolGateway(default_policy(self.agent_specs))
        register_default_connectors(self.tool_gateway)
        self.event_log: list[dict[str, Any]] = []
        self.tool_gateway.set_event_callback(self._emit)
        self.agents = {
            "supervisor_agent": SupervisorAgent(model_gateway=self.model_gateway, tool_gateway=self.tool_gateway, spec=self.agent_specs.get("supervisor_agent")),
            "ingestion_agent": IngestionAgent(model_gateway=self.model_gateway, tool_gateway=self.tool_gateway, spec=self.agent_specs.get("ingestion_agent")),
            "narrative_agent": NarrativeAgent(model_gateway=self.model_gateway, tool_gateway=self.tool_gateway, spec=self.agent_specs.get("narrative_agent")),
            "discovery_agent": DiscoveryAgent(model_gateway=self.model_gateway, tool_gateway=self.tool_gateway, spec=self.agent_specs.get("discovery_agent")),
            "social_kol_agent": SocialKOLAgent(model_gateway=self.model_gateway, tool_gateway=self.tool_gateway, spec=self.agent_specs.get("social_kol_agent")),
            "contract_onchain_agent": ContractOnchainAgent(model_gateway=self.model_gateway, tool_gateway=self.tool_gateway, spec=self.agent_specs.get("contract_onchain_agent")),
            "product_tech_agent": ProductTechAgent(model_gateway=self.model_gateway, tool_gateway=self.tool_gateway, spec=self.agent_specs.get("product_tech_agent")),
            "funding_token_agent": FundingTokenAgent(model_gateway=self.model_gateway, tool_gateway=self.tool_gateway, spec=self.agent_specs.get("funding_token_agent")),
            "report_agent": ReportAgent(model_gateway=self.model_gateway, tool_gateway=self.tool_gateway, spec=self.agent_specs.get("report_agent")),
            "obsidian_curator_agent": ObsidianCuratorAgent(model_gateway=self.model_gateway, tool_gateway=self.tool_gateway, spec=self.agent_specs.get("obsidian_curator_agent")),
        }
        self.event_handler: Callable[[dict[str, Any]], None] | None = None

    def run_article_research(
        self,
        *,
        title: str,
        content: str,
        url: str | None = None,
        vault_dir: str | Path = "vault",
        reports_dir: str | Path = "reports",
        memory_path: str | Path | None = "data/memory.json",
    ) -> ResearchRunResult:
        company_settings = load_company_settings(company_settings_path_for(memory_path))
        room = ResearchRoom(
            topic=title,
            goals=[
                "Store and summarize the source.",
                "Extract narratives.",
                "Discover similar early project candidates.",
                "Create a candidate dossier and Obsidian notes.",
            ],
            agents=list(DEFAULT_AGENTS),
        )
        room.set_status(RuntimeState.ASSIGNED)
        self._emit(
            "room_created",
            room_id=room.room_id,
            topic=room.topic,
            goals=room.goals,
            agents=room.agents,
        )

        try:
            self._run_agent("supervisor_agent", room, goals=room.goals, company_settings=company_settings.to_dict())
            room.set_status(RuntimeState.RUNNING)
            self._run_agent("ingestion_agent", room, title=title, content=content, url=url, source_type="article")
            self._run_agent("narrative_agent", room)
            self._run_agent("discovery_agent", room)
            room.set_status(RuntimeState.WAITING_FOR_TOOL)
            self._run_agent("social_kol_agent", room)
            self._run_agent("contract_onchain_agent", room)
            self._run_agent("product_tech_agent", room)
            self._run_agent("funding_token_agent", room)
            room.set_status(RuntimeState.READY_FOR_REPORT)
            room.set_status(RuntimeState.WRITING_REPORT)
            self._run_agent("report_agent", room, reports_dir=reports_dir, company_settings=company_settings)
            room.set_status(RuntimeState.OBSIDIAN_SYNCING)
            self._run_agent("obsidian_curator_agent", room, vault_dir=vault_dir)
        except Exception as exc:
            room.set_status(RuntimeState.FAILED)
            self._emit(
                "room_failed",
                room_id=room.room_id,
                topic=room.topic,
                status=room.status,
                summary=str(exc),
            )
            self._save_run(room, memory_path)
            raise

        room.close()
        self._emit_room_completed(room)
        self._save_run(room, memory_path)
        return ResearchRunResult(room=room, memory=self.memory, bus=self.bus)

    def run_source_ingestion(
        self,
        *,
        title: str,
        content: str,
        url: str | None = None,
        vault_dir: str | Path = "vault",
        memory_path: str | Path | None = "data/memory.json",
    ) -> ResearchRunResult:
        company_settings = load_company_settings(company_settings_path_for(memory_path))
        room = ResearchRoom(
            topic=title,
            goals=[
                "Store the source.",
                "Extract metadata.",
                "Create an Obsidian Source Note.",
            ],
            agents=[
                "supervisor_agent",
                "ingestion_agent",
                "obsidian_curator_agent",
            ],
        )
        room.set_status(RuntimeState.ASSIGNED)
        self._emit(
            "room_created",
            room_id=room.room_id,
            topic=room.topic,
            goals=room.goals,
            agents=room.agents,
        )

        try:
            self._run_agent("supervisor_agent", room, goals=room.goals, company_settings=company_settings.to_dict())
            room.set_status(RuntimeState.RUNNING)
            self._run_agent("ingestion_agent", room, title=title, content=content, url=url, source_type="article")
            room.set_status(RuntimeState.OBSIDIAN_SYNCING)
            self._run_agent("obsidian_curator_agent", room, vault_dir=vault_dir)
        except Exception as exc:
            room.set_status(RuntimeState.FAILED)
            self._emit(
                "room_failed",
                room_id=room.room_id,
                topic=room.topic,
                status=room.status,
                summary=str(exc),
            )
            self._save_run(room, memory_path)
            raise

        room.close()
        self._emit_room_completed(room)
        self._save_run(room, memory_path)
        return ResearchRunResult(room=room, memory=self.memory, bus=self.bus)

    def _run_agent(self, agent_id: str, room: ResearchRoom, **kwargs: object) -> None:
        agent = self.agents[agent_id]
        self._emit(
            "agent_start",
            room_id=room.room_id,
            agent_id=agent_id,
            agent_name=agent.name,
            task_type=agent.task_type,
        )
        self.hooks.run("before_run", agent_id=agent_id, room_id=room.room_id)
        try:
            result = agent.run(room, self.memory, self.bus, **kwargs)
        except Exception as exc:
            self._emit(
                "agent_failed",
                room_id=room.room_id,
                agent_id=agent_id,
                agent_name=agent.name,
                task_type=agent.task_type,
                error=str(exc),
            )
            raise
        finally:
            self.hooks.run("after_run", agent_id=agent_id, room_id=room.room_id)
        self._emit(
            "agent_done",
            room_id=room.room_id,
            agent_id=agent_id,
            agent_name=agent.name,
            task_type=agent.task_type,
            summary=result.summary,
            messages=len(self.bus.messages),
            findings=len(self.memory.get_room_findings(room.room_id)),
        )
        self._emit_output_events(room, result)

    def _emit(self, event_type: str, **payload: Any) -> None:
        event = {"type": event_type, **payload}
        self.event_log.append(event)
        if self.event_handler is not None:
            self.event_handler(event)

    def _emit_room_completed(self, room: ResearchRoom) -> None:
        self._emit(
            "room_completed",
            room_id=room.room_id,
            topic=room.topic,
            status=room.status,
            output_paths=room.output_paths,
            messages=len(self.bus.messages),
            findings=len(self.memory.get_room_findings(room.room_id)),
        )

    def _emit_output_events(self, room: ResearchRoom, result: Any) -> None:
        data = result.data if isinstance(getattr(result, "data", None), dict) else {}
        finding_id = data.get("finding_id")
        if finding_id:
            self._emit(
                "finding_saved",
                room_id=room.room_id,
                agent_id=result.agent_id,
                finding_id=finding_id,
                summary=result.summary,
            )
        source_id = data.get("source_id")
        if source_id:
            self._emit(
                "source_saved",
                room_id=room.room_id,
                agent_id=result.agent_id,
                source_id=source_id,
                summary=result.summary,
            )
        report_path = data.get("report_path")
        if report_path:
            self._emit(
                "report_written",
                room_id=room.room_id,
                agent_id=result.agent_id,
                report_path=report_path,
                summary=result.summary,
            )
        paths = data.get("paths")
        if isinstance(paths, list):
            for path in paths:
                self._emit(
                    "note_written",
                    room_id=room.room_id,
                    agent_id=result.agent_id,
                    path=path,
                    summary=f"Wrote note: {path}",
                )

    def _save_run(self, room: ResearchRoom, memory_path: str | Path | None) -> None:
        if memory_path is None:
            return
        save_memory(self.memory, memory_path)
        save_run_snapshot(
            room=room,
            bus=self.bus,
            audit_log=self.tool_gateway.audit_log,
            llm_call_log=self.model_gateway.call_log,
            event_log=self.event_log,
            root_dir=Path(memory_path).parent / "runs",
        )


def default_policy(agent_specs: AgentSpecRegistry | None = None) -> PolicyEngine:
    policy = PolicyEngine()
    for agent_id in DEFAULT_AGENTS:
        policy.allow(agent_id, "source_cache_write")
    if agent_specs is not None:
        for agent_id, spec in agent_specs.specs.items():
            for tool in spec.tools.allow:
                policy.allow(agent_id, tool)
    for tool in ["x_search_posts", "telegram_read_channel", "discord_read_channel"]:
        policy.allow("social_kol_agent", tool)
    for tool in ["fetch_url", "parse_html", "archive_source_snapshot"]:
        policy.allow("ingestion_agent", tool)
    for tool in ["web_search", "github_search_repos", "coingecko_coin_metadata", "dexscreener_search_pairs"]:
        policy.allow("discovery_agent", tool)
    for tool in ["web_search", "crawl_website"]:
        policy.allow("social_kol_agent", tool)
    for tool in ["get_contract_address", "get_dex_pair", "get_token_metadata"]:
        policy.allow("contract_onchain_agent", tool)
    for tool in ["coingecko_coin_metadata", "dexscreener_search_pairs"]:
        policy.allow("contract_onchain_agent", tool)
    for tool in ["crawl_docs", "read_github_repo", "crawl_website"]:
        policy.allow("product_tech_agent", tool)
    for tool in ["check_airdrop_points", "crawl_funding_news"]:
        policy.allow("funding_token_agent", tool)
    return policy
