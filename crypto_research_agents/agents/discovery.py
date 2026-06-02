from __future__ import annotations

from typing import Any

from crypto_research_agents.agents.base import AgentResult, BaseAgent
from crypto_research_agents.core.bus import CollaborationBus
from crypto_research_agents.core.memory import ProjectCandidate, SharedMemory
from crypto_research_agents.core.message import MessageType
from crypto_research_agents.core.room import ResearchRoom


class DiscoveryAgent(BaseAgent):
    agent_id = "discovery_agent"
    name = "Discovery Agent"
    task_type = "candidate_discovery"

    def run(self, room: ResearchRoom, memory: SharedMemory, bus: CollaborationBus, **kwargs: Any) -> AgentResult:
        narrative_messages = bus.find(room_id=room.room_id, to_agent=self.agent_id, message_type=MessageType.REQUEST)
        narratives = []
        for message in narrative_messages:
            narratives.extend(message.context.get("narratives", []))
        narratives = sorted(set(narratives)) or ["Unclassified Early Crypto"]

        candidates = build_candidates(narratives, room.source_inputs)
        for candidate in candidates:
            memory.upsert_project(candidate)

        summary = f"Discovered {len(candidates)} MVP candidate placeholders from narrative signals."
        finding = self.write_finding(
            room=room,
            memory=memory,
            finding_type="candidate_discovery",
            summary=summary,
            data={"candidates": [candidate.to_dict() for candidate in candidates]},
            sources=room.source_inputs,
            confidence=0.55,
        )

        candidate_context = {"candidate_ids": [candidate.project_id for candidate in candidates]}
        bus.request(
            room_id=room.room_id,
            from_agent=self.agent_id,
            to_agent="social_kol_agent",
            objective="Check whether candidates have social/KOL momentum.",
            required_output=["mention_trend", "key_accounts", "community_signal", "sources"],
            context=candidate_context,
        )
        bus.request(
            room_id=room.room_id,
            from_agent=self.agent_id,
            to_agent="contract_onchain_agent",
            objective="Check candidate chain, token status, and contract info.",
            required_output=["chain", "token_status", "contract_address", "dex_pair", "sources"],
            context=candidate_context,
        )
        bus.request(
            room_id=room.room_id,
            from_agent=self.agent_id,
            to_agent="product_tech_agent",
            objective="Check candidate website, docs, GitHub, and product state.",
            required_output=["product_status", "docs_status", "github_status", "sources"],
            context=candidate_context,
        )
        bus.request(
            room_id=room.room_id,
            from_agent=self.agent_id,
            to_agent="funding_token_agent",
            objective="Check funding, points, airdrop, and token opportunity signals.",
            required_output=["funding_status", "points_status", "token_opportunity", "sources"],
            context=candidate_context,
        )
        return AgentResult(
            self.agent_id,
            summary,
            {"finding_id": finding.finding_id, "candidate_ids": candidate_context["candidate_ids"]},
            room.source_inputs,
            0.55,
        )


def build_candidates(narratives: list[str], source_ids: list[str]) -> list[ProjectCandidate]:
    candidates: list[ProjectCandidate] = []
    for index, narrative in enumerate(narratives[:5], start=1):
        slug = (
            narrative.replace(" x ", " ")
            .replace("/", " ")
            .replace("-", " ")
            .replace("  ", " ")
            .strip()
            .split()[0]
        )
        candidates.append(
            ProjectCandidate(
                name=f"{slug} Candidate {index}",
                reason_found=f"Seed candidate generated from narrative: {narrative}",
                token_status="unknown",
                narratives=[narrative],
                score=max(45.0, 70.0 - index * 5),
                sources=list(source_ids),
                metadata={"mvp_generated": True},
            )
        )
    return candidates
