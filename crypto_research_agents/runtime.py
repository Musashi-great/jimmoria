from __future__ import annotations

import threading
import os
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from crypto_research_agents.agents.discovery import extract_project_query, project_identity_hints
from crypto_research_agents.core.bus import CollaborationBus
from crypto_research_agents.core.agent_spec import AgentSpecRegistry
from crypto_research_agents.core.company_settings import company_settings_path_for, load_company_settings
from crypto_research_agents.core.concurrency import ConcurrencyPolicy, load_concurrency_policy
from crypto_research_agents.core.hooks import HookEngine
from crypto_research_agents.core.memory import FindingRecord, ProjectCandidate, SharedMemory, SourceRecord
from crypto_research_agents.core.model_gateway import ModelGateway
from crypto_research_agents.core.project_profile import find_project_profile
from crypto_research_agents.core.process_spec import load_process_spec
from crypto_research_agents.core.room import ResearchRoom
from crypto_research_agents.core.runtime_state import RuntimeState
from crypto_research_agents.core.time import utc_now
from crypto_research_agents.core.tool_gateway import PolicyEngine, ToolGateway
from crypto_research_agents.core.usage import aggregate_llm_usage
from crypto_research_agents.storage.json_store import save_memory
from crypto_research_agents.storage.paths import resolve_project_path
from crypto_research_agents.storage.run_store import append_report_index, delete_run_snapshot, save_run_snapshot
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

RESEARCH_SWARM_AGENTS = [
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
        self._emit_lock = threading.RLock()
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
        retention_policy: str | None = None,
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
            self._seed_parallel_room_context(room, title=title, content=content, url=url)
            self._run_research_swarm(room, title=title, content=content, url=url)
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
        self._apply_retention_policy(room, memory_path, retention_policy)
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
        start_barrier = kwargs.pop("_start_barrier", None)
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
        self._run_agent_spec_hooks(agent_id, "before_run", room_id=room.room_id, task_type=agent.task_type)
        if start_barrier is not None and hasattr(start_barrier, "wait"):
            try:
                start_barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass
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
                llm_usage=self._agent_llm_usage(agent_id, llm_start_index),
            )
            raise
        finally:
            self.hooks.run("after_run", agent_id=agent_id, room_id=room.room_id)
        self._run_agent_spec_hooks(
            agent_id,
            "quality_gate",
            room_id=room.room_id,
            task_type=agent.task_type,
            result_summary=result.summary,
        )
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
            llm_usage=self._agent_llm_usage(agent_id, llm_start_index),
        )
        self._emit_output_events(room, result)
        self._run_agent_spec_hooks(
            agent_id,
            "after_run",
            room_id=room.room_id,
            task_type=agent.task_type,
            result_summary=result.summary,
        )

    def _seed_parallel_room_context(self, room: ResearchRoom, *, title: str, content: str, url: str | None) -> None:
        project_query = extract_project_query(room.topic)
        source = self.memory.add_source(
            SourceRecord(
                title=title,
                content=content,
                source_type="parallel_seed",
                url=url,
                metadata={
                    "seeded_by": "supervisor_agent",
                    "seed_reason": "full_parallel_research_swarm_requires_shared_context_before_workers_start",
                    "project_query": project_query,
                },
            )
        )
        room.add_source(source.source_id)

        if not project_query:
            room.project_card["parallel_seed"] = {
                "source_id": source.source_id,
                "project_query": "",
                "candidate_id": None,
            }
            return

        profile = find_project_profile(project_query)
        identity_hints = project_identity_hints(project_query)
        evidence_urls = _seed_evidence_urls(url, identity_hints)
        candidate = self.memory.upsert_project(
            ProjectCandidate(
                name=_seed_project_name(project_query),
                reason_found="Supervisor seeded the primary candidate before the full parallel research swarm.",
                website=(profile.website if profile else _seed_website(project_query, identity_hints)),
                x_account=(profile.official_x if profile else _seed_x_account(identity_hints)),
                chain=profile.chain if profile else None,
                token_status="unknown",
                narratives=["Unclassified Early Crypto"],
                score=0.5,
                sources=[source.source_id],
                metadata={
                    "discovery_mode": "supervisor_parallel_seed",
                    "candidate_origin": "live_source_backed" if evidence_urls else "supervisor_parallel_seed",
                    "source_backing": "profile_or_identity_seed_evidence" if evidence_urls else "seeded_context_for_parallel_workers",
                    "project_query": project_query,
                    "evidence_urls": evidence_urls,
                    "web_results": identity_hints[:12],
                    "official_x": profile.official_x if profile else _seed_x_account(identity_hints),
                    "search_queries": list(profile.search_queries) if profile else [],
                    "address_registry": dict(profile.address_registry) if profile else {},
                    "funding": dict(profile.funding) if profile else {},
                    "article_notes": list(profile.article_notes) if profile else [],
                },
            )
        )
        room.project_card["parallel_seed"] = {
            "source_id": source.source_id,
            "project_query": project_query,
            "candidate_id": candidate.project_id,
            "candidate_name": candidate.name,
            "evidence_urls": evidence_urls,
        }
        self._seed_specialist_requests(room, candidate.project_id)
        self._emit(
            "parallel_seed_ready",
            room_id=room.room_id,
            source_id=source.source_id,
            candidate_id=candidate.project_id,
            project_query=project_query,
            summary="Supervisor seeded shared source and candidate context before the full parallel research swarm.",
        )

    def _seed_specialist_requests(self, room: ResearchRoom, candidate_id: str) -> None:
        candidate_context = {"candidate_ids": [candidate_id], "seeded_by": "supervisor_agent"}
        self.bus.request(
            room_id=room.room_id,
            from_agent="supervisor_agent",
            to_agent="social_kol_agent",
            objective="Check official X/Twitter, public KOL, and social signal for the seeded candidate.",
            required_output=["official_x", "who_said_what", "community_signal", "sources"],
            context=candidate_context,
            priority="high",
        )
        self.bus.request(
            room_id=room.room_id,
            from_agent="supervisor_agent",
            to_agent="contract_onchain_agent",
            objective="Check chain, token identity, contract, DEX, and explorer evidence for the seeded candidate.",
            required_output=["chain", "token_status", "contract_address", "dex_pair", "sources"],
            context=candidate_context,
            priority="high",
        )
        self.bus.request(
            room_id=room.room_id,
            from_agent="supervisor_agent",
            to_agent="product_tech_agent",
            objective="Check website, docs, GitHub, product state, and live infra for the seeded candidate.",
            required_output=["product_status", "docs_status", "github_status", "live_infra", "sources"],
            context=candidate_context,
            priority="high",
        )
        self.bus.request(
            room_id=room.room_id,
            from_agent="supervisor_agent",
            to_agent="funding_token_agent",
            objective="Check founder, funding, points, airdrop, token mechanics, and value-capture evidence.",
            required_output=["founders", "funding_status", "points_status", "token_value_capture", "sources"],
            context=candidate_context,
            priority="high",
        )

    def _run_research_swarm(self, room: ResearchRoom, *, title: str, content: str, url: str | None) -> None:
        group = self._active_parallel_group("research_swarm")
        agent_ids = [
            agent_id
            for agent_id in (group.agents if group is not None and group.agents else RESEARCH_SWARM_AGENTS)
            if agent_id in room.agents and agent_id in self.agents
        ]
        max_parallel = max(
            1,
            min(
                self.concurrency_policy.active.max_parallel or self.concurrency_policy.default_max_parallel,
                len(agent_ids) or 1,
            ),
        )

        self._emit(
            "parallel_group_start",
            room_id=room.room_id,
            group_id="research_swarm",
            agents=agent_ids,
            max_parallel=max_parallel,
            after_agent="supervisor_agent",
            join_before="agent_council",
            summary=f"Running {len(agent_ids)} research agents in one full parallel swarm.",
        )
        start_barrier = (
            threading.Barrier(len(agent_ids))
            if len(agent_ids) > 1 and max_parallel >= len(agent_ids)
            else None
        )
        failures: list[tuple[str, str]] = []
        with ThreadPoolExecutor(max_workers=max_parallel, thread_name_prefix="jimmoria-swarm") as executor:
            futures = {
                executor.submit(
                    self._run_agent,
                    agent_id,
                    room,
                    **_swarm_agent_kwargs(agent_id, title=title, content=content, url=url),
                    _start_barrier=start_barrier,
                ): agent_id
                for agent_id in agent_ids
            }
            for future in as_completed(futures):
                agent_id = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    failures.append((agent_id, str(exc)))

        if failures:
            self._emit(
                "parallel_group_failed",
                room_id=room.room_id,
                group_id="research_swarm",
                agents=agent_ids,
                failures=[{"agent_id": agent_id, "error": error} for agent_id, error in failures],
                summary=f"{len(failures)} full parallel research swarm agent(s) failed.",
            )
            failed = "; ".join(f"{agent_id}: {error}" for agent_id, error in failures)
            raise RuntimeError(f"Parallel research swarm failed: {failed}")

        self._emit(
            "parallel_group_done",
            room_id=room.room_id,
            group_id="research_swarm",
            agents=agent_ids,
            max_parallel=max_parallel,
            summary=f"Full parallel research swarm completed for {len(agent_ids)} agents.",
            messages=len(self.bus.messages),
            findings=len(self.memory.get_room_findings(room.room_id)),
        )

    def _active_parallel_group(self, group_id: str) -> Any | None:
        for group in self.concurrency_policy.active.parallel_groups:
            if group.group_id == group_id:
                return group
        return None

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
        if event_type == "tool_start":
            self._run_agent_spec_hooks(
                str(payload.get("agent_id") or ""),
                "before_tool",
                room_id=str(payload.get("room_id") or ""),
                tool_name=str(payload.get("tool_name") or ""),
            )
        with self._emit_lock:
            event = {
                "seq": len(self.event_log) + 1,
                "type": event_type,
                "timestamp": utc_now(),
                **payload,
            }
            self.event_log.append(event)
            if self.event_handler is not None:
                self.event_handler(event)
        if event_type in {"tool_done", "tool_failed", "tool_denied", "tool_unconfigured"}:
            self._run_agent_spec_hooks(
                str(payload.get("agent_id") or ""),
                "after_tool",
                room_id=str(payload.get("room_id") or ""),
                tool_name=str(payload.get("tool_name") or ""),
                tool_status=str(payload.get("status") or event_type.replace("tool_", "")),
            )

    def _run_agent_spec_hooks(self, agent_id: str, phase: str, **payload: Any) -> None:
        if not agent_id:
            return
        spec = self.agent_specs.get(agent_id)
        if spec is None:
            return
        hook_names = spec.hooks.get(phase, [])
        for hook_name in hook_names:
            hook_payload = {
                "agent_id": agent_id,
                "hook_phase": phase,
                **payload,
            }
            self.hooks.run(hook_name, **hook_payload)
            self._emit(
                "agent_hook",
                hook_name=hook_name,
                **hook_payload,
                summary=f"{agent_id} hook {phase}:{hook_name}",
            )

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
        return aggregate_llm_usage(self.model_gateway.calls_after(start_index))

    def _agent_llm_usage(self, agent_id: str, start_index: int) -> dict[str, Any]:
        return aggregate_llm_usage(self.model_gateway.calls_after(start_index, agent_id=agent_id))

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

    def _apply_retention_policy(
        self,
        room: ResearchRoom,
        memory_path: str | Path | None,
        retention_policy: str | None,
    ) -> None:
        policy = _normalize_retention_policy(retention_policy)
        if policy != "report_only" or memory_path is None:
            return
        memory_file = Path(memory_path)
        runs_dir = memory_file.parent / "runs"
        index_path = append_report_index(room=room, root_dir=runs_dir)
        run_deleted = delete_run_snapshot(room_id=room.room_id, root_dir=runs_dir)
        memory_deleted = _delete_file(memory_file)
        evidence_deleted = _delete_file(Path(room.output_paths.get("evidence_packet", "")))
        retention = {
            "policy": policy,
            "report_index": str(index_path) if index_path is not None else "",
            "run_snapshot_deleted": run_deleted,
            "memory_deleted": memory_deleted,
            "evidence_packet_deleted": evidence_deleted,
            "kept": {
                "report": room.output_paths.get("report", ""),
                "vault": room.output_paths.get("obsidian_vault", ""),
            },
        }
        room.project_card["run_retention"] = retention
        room.output_paths["report_index"] = retention["report_index"]
        room.output_paths["run_snapshot"] = ""


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)


def _normalize_retention_policy(value: str | None) -> str:
    raw = (value or os.getenv("JIMMORIA_RUN_RETENTION") or "debug").strip().lower().replace("-", "_")
    if raw in {"report_only", "final_report_only", "clean", "cleanup", "delete_room", "delete_run"}:
        return "report_only"
    return "debug"


def _delete_file(path: Path) -> bool:
    if not str(path):
        return False
    try:
        if path.exists() and path.is_file():
            path.unlink()
            return True
    except OSError:
        return False
    return False


def _swarm_agent_kwargs(agent_id: str, *, title: str, content: str, url: str | None) -> dict[str, Any]:
    if agent_id == "ingestion_agent":
        return {"title": title, "content": content, "url": url, "source_type": "article"}
    return {}


def _seed_project_name(project_query: str) -> str:
    profile = find_project_profile(project_query)
    if profile:
        return profile.display_name
    if project_query.lower().strip() == "pearl":
        return "Pearl Network"
    return project_query.strip().title()


def _seed_website(project_query: str, identity_hints: list[dict[str, Any]]) -> str | None:
    if project_query.lower().strip() == "pearl":
        return "https://pearlresearch.ai/"
    for hint in identity_hints:
        url = str(hint.get("url") or "")
        if url.startswith(("http://", "https://")) and "x.com/" not in url and "twitter.com/" not in url:
            return url
    return None


def _seed_x_account(identity_hints: list[dict[str, Any]]) -> str | None:
    for hint in identity_hints:
        url = str(hint.get("url") or "")
        lowered = url.lower()
        if lowered.startswith(("https://x.com/", "https://twitter.com/", "http://x.com/", "http://twitter.com/")):
            return url
    return None


def _seed_evidence_urls(input_url: str | None, identity_hints: list[dict[str, Any]]) -> list[str]:
    urls: list[str] = []
    if input_url:
        urls.append(input_url)
    for hint in identity_hints:
        url = str(hint.get("url") or "")
        if url.startswith(("http://", "https://")):
            urls.append(url)
    deduped: list[str] = []
    for url in urls:
        if url not in deduped:
            deduped.append(url)
    return deduped[:16]


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
