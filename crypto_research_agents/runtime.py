from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
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
from crypto_research_agents.core.concurrency import ConcurrencyPolicy, load_concurrency_policy
from crypto_research_agents.core.hooks import HookEngine
from crypto_research_agents.core.memory import FindingRecord, SharedMemory
from crypto_research_agents.core.model_gateway import ModelGateway
from crypto_research_agents.core.process_spec import load_process_spec
from crypto_research_agents.core.room import ResearchRoom
from crypto_research_agents.core.runtime_state import RuntimeState
from crypto_research_agents.core.time import utc_now
from crypto_research_agents.core.tool_gateway import PolicyEngine, ToolGateway
from crypto_research_agents.core.usage import aggregate_llm_usage
from crypto_research_agents.storage.json_store import save_memory
from crypto_research_agents.storage.paths import resolve_project_path
from crypto_research_agents.storage.run_store import save_run_snapshot
from crypto_research_agents.tools.registry import load_tool_registry


DEFAULT_AGENTS = [
    "supervisor_agent",
    "ingestion_agent",
    "social_kol_agent",
    "narrative_agent",
    "discovery_agent",
    "contract_onchain_agent",
    "product_tech_agent",
    "funding_token_agent",
    "report_agent",
    "obsidian_curator_agent",
]

COUNCIL_AGENTS = [
    "ingestion_agent",
    "social_kol_agent",
    "narrative_agent",
    "discovery_agent",
    "contract_onchain_agent",
    "product_tech_agent",
    "funding_token_agent",
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
        process_spec_dir: str | Path = "config/processes",
        concurrency_policy_path: str | Path = "config/concurrency.yaml",
    ) -> None:
        self.memory = memory or SharedMemory()
        self.bus = CollaborationBus()
        self.hooks = HookEngine()
        self.model_gateway = ModelGateway()
        self.agent_specs = AgentSpecRegistry.load_dir(resolve_project_path(agent_spec_dir))
        self.process_spec_dir = resolve_project_path(process_spec_dir)
        self.concurrency_policy: ConcurrencyPolicy = load_concurrency_policy(concurrency_policy_path)
        self.tool_gateway = ToolGateway(default_policy(self.agent_specs))
        register_default_connectors(self.tool_gateway)
        self.event_log: list[dict[str, Any]] = []
        self._room_started_at: dict[str, float] = {}
        self._room_llm_start_index: dict[str, int] = {}
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
        intake_decision: dict[str, Any] | None = None,
    ) -> ResearchRunResult:
        company_settings = load_company_settings(company_settings_path_for(memory_path))
        process = load_process_spec("project_research_room", self.process_spec_dir)
        room = ResearchRoom(
            topic=title,
            goals=process.goals,
            agents=process.agent_ids,
        )
        self._mark_room_started(room)
        room.set_status(RuntimeState.ASSIGNED)
        self._emit(
            "room_created",
            room_id=room.room_id,
            topic=room.topic,
            goals=room.goals,
            agents=room.agents,
            process=process.event_payload(),
            concurrency=self.concurrency_policy.event_payload(),
        )

        try:
            self._run_agent(
                "supervisor_agent",
                room,
                goals=room.goals,
                company_settings=company_settings.to_dict(),
                intake_decision=intake_decision,
                process=process.event_payload(),
            )
            room.set_status(RuntimeState.RUNNING)
            self._run_agent("ingestion_agent", room, title=title, content=content, url=url, source_type="article")
            self._run_agent("social_kol_agent", room, seed_mode=True)
            self._run_agent("narrative_agent", room)
            self._run_agent("discovery_agent", room)
            room.set_status(RuntimeState.WAITING_FOR_TOOL)
            self._run_agent("social_kol_agent", room)
            self._run_agent("contract_onchain_agent", room)
            self._run_agent("product_tech_agent", room)
            self._run_agent("funding_token_agent", room)
            room.set_status(RuntimeState.DELIBERATING)
            self._run_agent_council(room)
            room.set_status(RuntimeState.READY_FOR_REPORT)
            room.set_status(RuntimeState.WRITING_REPORT)
            evidence_packet_dir = (
                Path(memory_path).parent / "evidence_packets"
                if memory_path is not None
                else Path("data/evidence_packets")
            )
            self._run_agent(
                "report_agent",
                room,
                reports_dir=reports_dir,
                evidence_packet_dir=evidence_packet_dir,
                company_settings=company_settings,
            )
            room.set_status(RuntimeState.SUPERVISOR_REVIEWING)
            self._run_supervisor_final_review(room, company_settings=company_settings)
            room.set_status(RuntimeState.OBSIDIAN_SYNCING)
            self._run_agent("obsidian_curator_agent", room, vault_dir=vault_dir)
        except Exception as exc:
            room.set_status(RuntimeState.FAILED)
            room.project_card["runtime_metrics"] = self._room_metrics(room.room_id)
            self._emit(
                "room_failed",
                room_id=room.room_id,
                topic=room.topic,
                status=room.status,
                summary=str(exc),
                duration_ms=self._room_duration_ms(room.room_id),
                llm_usage=self._room_llm_usage(room.room_id),
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
        intake_decision: dict[str, Any] | None = None,
    ) -> ResearchRunResult:
        company_settings = load_company_settings(company_settings_path_for(memory_path))
        process = load_process_spec("source_ingestion_room", self.process_spec_dir)
        room = ResearchRoom(
            topic=title,
            goals=process.goals,
            agents=process.agent_ids,
        )
        self._mark_room_started(room)
        room.set_status(RuntimeState.ASSIGNED)
        self._emit(
            "room_created",
            room_id=room.room_id,
            topic=room.topic,
            goals=room.goals,
            agents=room.agents,
            process=process.event_payload(),
            concurrency=self.concurrency_policy.event_payload(),
        )

        try:
            self._run_agent(
                "supervisor_agent",
                room,
                goals=room.goals,
                company_settings=company_settings.to_dict(),
                intake_decision=intake_decision,
                process=process.event_payload(),
            )
            room.set_status(RuntimeState.RUNNING)
            self._run_agent("ingestion_agent", room, title=title, content=content, url=url, source_type="article")
            room.set_status(RuntimeState.OBSIDIAN_SYNCING)
            self._run_agent("obsidian_curator_agent", room, vault_dir=vault_dir)
        except Exception as exc:
            room.set_status(RuntimeState.FAILED)
            room.project_card["runtime_metrics"] = self._room_metrics(room.room_id)
            self._emit(
                "room_failed",
                room_id=room.room_id,
                topic=room.topic,
                status=room.status,
                summary=str(exc),
                duration_ms=self._room_duration_ms(room.room_id),
                llm_usage=self._room_llm_usage(room.room_id),
            )
            self._save_run(room, memory_path)
            raise

        room.close()
        self._emit_room_completed(room)
        self._save_run(room, memory_path)
        return ResearchRunResult(room=room, memory=self.memory, bus=self.bus)

    def _run_agent(self, agent_id: str, room: ResearchRoom, **kwargs: object) -> None:
        agent = self.agents[agent_id]
        started = perf_counter()
        llm_start_index = len(self.model_gateway.call_log)
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
                duration_ms=_elapsed_ms(started),
                llm_usage=aggregate_llm_usage(self.model_gateway.call_log[llm_start_index:]),
            )
            raise
        finally:
            self.hooks.run("after_run", agent_id=agent_id, room_id=room.room_id)
        self._emit_orchestration_plan_event(room, result)
        self._emit(
            "agent_done",
            room_id=room.room_id,
            agent_id=agent_id,
            agent_name=agent.name,
            task_type=agent.task_type,
            summary=result.summary,
            messages=len(self.bus.messages),
            findings=len(self.memory.get_room_findings(room.room_id)),
            duration_ms=_elapsed_ms(started),
            llm_usage=aggregate_llm_usage(self.model_gateway.call_log[llm_start_index:]),
        )
        self._emit_output_events(room, result)

    def _run_agent_council(self, room: ResearchRoom) -> None:
        participants = [agent_id for agent_id in COUNCIL_AGENTS if agent_id in room.agents]
        self._emit(
            "deliberation_start",
            room_id=room.room_id,
            participants=participants,
            summary="Specialist agents compare findings and produce a consensus for the report agent.",
        )
        findings = self.memory.get_room_findings(room.room_id)
        statements: list[dict[str, Any]] = []
        for agent_id in participants:
            agent_findings = [finding for finding in findings if finding.agent_id == agent_id]
            if agent_findings:
                latest = agent_findings[-1]
                statement = {
                    "agent_id": agent_id,
                    "finding_id": latest.finding_id,
                    "summary": latest.summary,
                    "confidence": latest.confidence,
                    "finding_type": latest.finding_type,
                }
            else:
                statement = {
                    "agent_id": agent_id,
                    "finding_id": None,
                    "summary": "No finding submitted before council.",
                    "confidence": 0.0,
                    "finding_type": "missing",
                }
            statements.append(statement)
            self.bus.update(
                room_id=room.room_id,
                from_agent=agent_id,
                to_agent="agent_council",
                summary=statement["summary"],
                payload=statement,
            )

        consensus = _build_council_consensus(room, statements)
        council_finding = self.memory.add_finding(
            FindingRecord(
                room_id=room.room_id,
                agent_id="agent_council",
                finding_type="agent_council_consensus",
                summary=consensus["summary"],
                data=consensus,
                confidence=consensus["confidence"],
            )
        )
        room.add_finding(council_finding.finding_id)
        room.project_card["agent_council"] = consensus
        self.bus.handoff(
            room_id=room.room_id,
            from_agent="agent_council",
            to_agent="report_agent",
            summary=consensus["summary"],
            payload={"finding_id": council_finding.finding_id, "consensus": consensus},
        )
        self._emit(
            "deliberation_done",
            room_id=room.room_id,
            participants=participants,
            finding_id=council_finding.finding_id,
            summary=consensus["summary"],
            decision=consensus["decision"],
            messages=len(self.bus.messages),
            findings=len(self.memory.get_room_findings(room.room_id)),
        )

    def _run_supervisor_final_review(self, room: ResearchRoom, *, company_settings: Any) -> None:
        self._emit(
            "final_review_start",
            room_id=room.room_id,
            agent_id="supervisor_agent",
            summary="Supervisor reviews council consensus, quality gate, and report draft before delivery.",
        )
        review = _build_supervisor_final_review(room)
        review_finding = self.memory.add_finding(
            FindingRecord(
                room_id=room.room_id,
                agent_id="supervisor_agent",
                finding_type="final_supervisor_review",
                summary=review["summary"],
                data=review,
                confidence=review["confidence"],
            )
        )
        room.add_finding(review_finding.finding_id)
        room.project_card["supervisor_final_review"] = review
        self.bus.update(
            room_id=room.room_id,
            from_agent="supervisor_agent",
            to_agent="all",
            summary=review["summary"],
            payload={"finding_id": review_finding.finding_id, "review": review},
        )
        self._emit(
            "final_review_done",
            room_id=room.room_id,
            agent_id="supervisor_agent",
            finding_id=review_finding.finding_id,
            summary=review["summary"],
            delivery_mode=review["delivery_mode"],
            approved=review["approved_for_delivery"],
            messages=len(self.bus.messages),
            findings=len(self.memory.get_room_findings(room.room_id)),
        )

    def _append_supervisor_review_to_report(self, room: ResearchRoom, review: dict[str, Any], *, company_settings: Any) -> None:
        report_path = room.output_paths.get("report")
        if not report_path:
            return
        path = Path(report_path)
        if not path.exists():
            return
        report_language = getattr(company_settings, "report_language", "en")
        korean = report_language == "ko"
        if korean:
            section = [
                "",
                "## 9. Supervisor Final Review",
                f"- 전달 모드: `{review['delivery_mode']}`",
                f"- 전달 승인: `{str(review['approved_for_delivery']).lower()}`",
                f"- 최종 판단: {review['summary']}",
            ]
        else:
            section = [
                "",
                "## 9. Supervisor Final Review",
                f"- Delivery mode: `{review['delivery_mode']}`",
                f"- Approved for delivery: `{str(review['approved_for_delivery']).lower()}`",
                f"- Final judgment: {review['summary']}",
            ]
        if review.get("required_followups"):
            section.append("- Required follow-ups:")
            section.extend(f"  - {item}" for item in review["required_followups"])
        updated = path.read_text(encoding="utf-8").rstrip() + "\n" + "\n".join(section) + "\n"
        path.write_text(updated, encoding="utf-8")
        room.report_draft = updated

    def _emit(self, event_type: str, **payload: Any) -> None:
        event = {
            "seq": len(self.event_log) + 1,
            "type": event_type,
            "timestamp": utc_now(),
            **payload,
        }
        self.event_log.append(event)
        if self.event_handler is not None:
            self.event_handler(event)

    def _emit_room_completed(self, room: ResearchRoom) -> None:
        quality = room.project_card.get("research_quality") if isinstance(room.project_card, dict) else {}
        if not isinstance(quality, dict):
            quality = {}
        runtime_metrics = self._room_metrics(room.room_id)
        room.project_card["runtime_metrics"] = runtime_metrics
        self._emit(
            "room_completed",
            room_id=room.room_id,
            topic=room.topic,
            status=room.status,
            research_quality_status=quality.get("status"),
            research_quality=quality,
            output_paths=room.output_paths,
            messages=len(self.bus.messages),
            findings=len(self.memory.get_room_findings(room.room_id)),
            duration_ms=runtime_metrics["duration_ms"],
            llm_usage=runtime_metrics["llm_usage"],
        )

    def _mark_room_started(self, room: ResearchRoom) -> None:
        self._room_started_at[room.room_id] = perf_counter()
        self._room_llm_start_index[room.room_id] = len(self.model_gateway.call_log)

    def _room_duration_ms(self, room_id: str) -> int:
        started = self._room_started_at.get(room_id)
        if started is None:
            return 0
        return _elapsed_ms(started)

    def _room_llm_usage(self, room_id: str) -> dict[str, Any]:
        start_index = self._room_llm_start_index.get(room_id, 0)
        return aggregate_llm_usage(self.model_gateway.call_log[start_index:])

    def _room_metrics(self, room_id: str) -> dict[str, Any]:
        return {
            "duration_ms": self._room_duration_ms(room_id),
            "llm_usage": self._room_llm_usage(room_id),
        }

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
                quality_status=data.get("quality_status"),
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

    def _emit_orchestration_plan_event(self, room: ResearchRoom, result: Any) -> None:
        data = result.data if isinstance(getattr(result, "data", None), dict) else {}
        orchestration_plan = data.get("orchestration_plan")
        if isinstance(orchestration_plan, dict):
            self._emit(
                "orchestration_plan",
                room_id=room.room_id,
                agent_id=result.agent_id,
                mode=orchestration_plan.get("mode"),
                delegated_count=orchestration_plan.get("delegated_count"),
                checkpoints=orchestration_plan.get("coordination_checkpoints", []),
                summary="Supervisor set the orchestration plan and coordination checkpoints.",
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


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)


def default_policy(agent_specs: AgentSpecRegistry | None = None) -> PolicyEngine:
    policy = PolicyEngine()
    tool_registry = load_tool_registry()
    for agent_id in DEFAULT_AGENTS:
        policy.allow(agent_id, "source_cache_write")
        policy.allow(agent_id, "url_safety_check")
        policy.allow(agent_id, "source_relevance_filter")
        policy.allow(agent_id, "tool_call_guardrail")
    if agent_specs is not None:
        for agent_id, spec in agent_specs.specs.items():
            allowed_tools = tool_registry.tools_for_agent_policy(
                spec.tools.allow,
                spec.tools.allowed_toolsets,
            )
            for tool in allowed_tools:
                if not tool_registry.is_tool_allowed_for_research(tool):
                    continue
                policy.allow(agent_id, tool)
    for tool in [
        "x_search_posts",
        "x_get_user_timeline",
        "x_build_kol_list",
    ]:
        policy.allow("social_kol_agent", tool)
    for tool in ["fetch_url", "parse_html", "archive_source_snapshot"]:
        policy.allow("ingestion_agent", tool)
    for tool in [
        "web_search",
        "github_search_repos",
        "coingecko_coin_metadata",
        "dexscreener_search_pairs",
        "rootdata_search_projects",
        "rootdata_get_hot_projects",
    ]:
        policy.allow("discovery_agent", tool)
    for tool in ["web_search", "crawl_website"]:
        policy.allow("social_kol_agent", tool)
    for tool in [
        "get_contract_address",
        "explorer_lookup",
        "explorer_get_contract_source",
        "explorer_get_token_supply",
        "explorer_get_token_holders",
        "rpc_read_contract",
        "get_dex_pair",
        "get_token_metadata",
    ]:
        policy.allow("contract_onchain_agent", tool)
    for tool in ["coingecko_coin_metadata", "dexscreener_search_pairs"]:
        policy.allow("contract_onchain_agent", tool)
    for tool in ["crawl_docs", "read_github_repo", "crawl_website"]:
        policy.allow("product_tech_agent", tool)
    for tool in ["check_airdrop_points", "crawl_funding_news", "rootdata_get_project", "rootdata_get_investors"]:
        policy.allow("funding_token_agent", tool)
    return policy


def _build_council_consensus(room: ResearchRoom, statements: list[dict[str, Any]]) -> dict[str, Any]:
    missing = [item["agent_id"] for item in statements if not item.get("finding_id")]
    low_confidence = [
        item["agent_id"]
        for item in statements
        if float(item.get("confidence") or 0.0) < 0.35
    ]
    blockers: list[str] = []
    for item in statements:
        summary = str(item.get("summary") or "")
        lowered = summary.lower()
        if "unconfigured" in lowered or "placeholder" in lowered or "insufficient" in lowered:
            blockers.append(f"{item['agent_id']}: {summary}")
    if missing:
        blockers.append("Missing council statements: " + ", ".join(missing))

    decision = "write_diagnostic_memo" if blockers or low_confidence else "write_candidate_dossier"
    if decision == "write_diagnostic_memo":
        summary = (
            "Agent council reached a guarded consensus: write a diagnostic memo and label evidence gaps before delivery."
        )
        confidence = 0.45
    else:
        summary = "Agent council reached consensus: enough evidence exists to draft a candidate dossier."
        confidence = 0.75
    return {
        "room_id": room.room_id,
        "topic": room.topic,
        "decision": decision,
        "summary": summary,
        "participants": [item["agent_id"] for item in statements],
        "statements": statements,
        "blockers": blockers[:8],
        "low_confidence_agents": low_confidence,
        "confidence": confidence,
    }


def _build_supervisor_final_review(room: ResearchRoom) -> dict[str, Any]:
    quality = room.project_card.get("research_quality") if isinstance(room.project_card, dict) else {}
    if not isinstance(quality, dict):
        quality = {}
    council = room.project_card.get("agent_council") if isinstance(room.project_card, dict) else {}
    if not isinstance(council, dict):
        council = {}
    quality_status = str(quality.get("status") or "")
    council_decision = str(council.get("decision") or "")
    insufficient = quality_status == "insufficient_evidence" or council_decision == "write_diagnostic_memo"
    if insufficient:
        delivery_mode = "diagnostic_memo"
        summary = (
            "Supervisor approved delivery as a diagnostic memo, not as a completed research report, because evidence remains insufficient."
        )
        required_followups = [
            "Add source-backed social/KOL evidence.",
            "Verify official website, GitHub, token, and contract identity.",
            "Re-run quality gate after live connectors collect evidence URLs.",
        ]
        confidence = 0.55
    else:
        delivery_mode = "final_research_report"
        summary = "Supervisor approved the report for client delivery after council consensus and quality review."
        required_followups = []
        confidence = 0.8
    return {
        "approved_for_delivery": True,
        "delivery_mode": delivery_mode,
        "summary": summary,
        "quality_status": quality_status,
        "council_decision": council_decision,
        "required_followups": required_followups,
        "confidence": confidence,
    }
