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
from crypto_research_agents.core.bus import CollaborationBus
from crypto_research_agents.core.agent_spec import AgentSpecRegistry
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
        self.event_log: list[dict[str, Any]] = []
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

        self._run_agent("supervisor_agent", room, goals=room.goals)
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
        self._run_agent("report_agent", room, reports_dir=reports_dir)
        room.set_status(RuntimeState.OBSIDIAN_SYNCING)
        self._run_agent("obsidian_curator_agent", room, vault_dir=vault_dir)

        room.close()
        self._emit(
            "room_completed",
            room_id=room.room_id,
            topic=room.topic,
            status=room.status,
            output_paths=room.output_paths,
            messages=len(self.bus.messages),
            findings=len(self.memory.get_room_findings(room.room_id)),
        )
        if memory_path is not None:
            save_memory(self.memory, memory_path)
            save_run_snapshot(
                room=room,
                bus=self.bus,
                audit_log=self.tool_gateway.audit_log,
                llm_call_log=self.model_gateway.call_log,
                event_log=self.event_log,
                root_dir=Path(memory_path).parent / "runs",
            )
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

        self._run_agent("supervisor_agent", room, goals=room.goals)
        room.set_status(RuntimeState.RUNNING)
        self._run_agent("ingestion_agent", room, title=title, content=content, url=url, source_type="article")
        room.set_status(RuntimeState.OBSIDIAN_SYNCING)
        self._run_agent("obsidian_curator_agent", room, vault_dir=vault_dir)

        room.close()
        self._emit(
            "room_completed",
            room_id=room.room_id,
            topic=room.topic,
            status=room.status,
            output_paths=room.output_paths,
            messages=len(self.bus.messages),
            findings=len(self.memory.get_room_findings(room.room_id)),
        )
        if memory_path is not None:
            save_memory(self.memory, memory_path)
            save_run_snapshot(
                room=room,
                bus=self.bus,
                audit_log=self.tool_gateway.audit_log,
                llm_call_log=self.model_gateway.call_log,
                event_log=self.event_log,
                root_dir=Path(memory_path).parent / "runs",
            )
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
        result = agent.run(room, self.memory, self.bus, **kwargs)
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

    def _emit(self, event_type: str, **payload: Any) -> None:
        event = {"type": event_type, **payload}
        self.event_log.append(event)
        if self.event_handler is not None:
            self.event_handler(event)


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
    for tool in ["get_contract_address", "get_dex_pair", "get_token_metadata"]:
        policy.allow("contract_onchain_agent", tool)
    for tool in ["crawl_docs", "read_github_repo", "crawl_website"]:
        policy.allow("product_tech_agent", tool)
    for tool in ["check_airdrop_points", "crawl_funding_news"]:
        policy.allow("funding_token_agent", tool)
    return policy
