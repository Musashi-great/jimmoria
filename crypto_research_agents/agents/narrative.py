from __future__ import annotations

from typing import Any

from crypto_research_agents.agents.base import AgentResult, BaseAgent
from crypto_research_agents.core.bus import CollaborationBus
from crypto_research_agents.core.memory import SharedMemory
from crypto_research_agents.core.room import ResearchRoom


NARRATIVE_RULES = {
    "AI x Wallet Automation": {"ai", "agent", "wallet", "automation", "intent"},
    "Consumer Crypto": {"consumer", "wallet", "social", "mobile"},
    "DeFi Automation": {"defi", "intent", "automation", "trading"},
    "Pre-token / Airdrop": {"airdrop", "points", "testnet", "pre-token"},
    "Developer Infra": {"github", "docs", "sdk", "api"},
    "DePIN": {"depin", "sensor", "device", "network"},
    "RWA": {"rwa", "asset", "treasury", "tokenized"},
    "Restaking": {"restaking", "avs", "eigen"},
}


class NarrativeAgent(BaseAgent):
    agent_id = "narrative_agent"
    name = "Narrative Agent"
    task_type = "narrative_reasoning"

    def run(self, room: ResearchRoom, memory: SharedMemory, bus: CollaborationBus, **kwargs: Any) -> AgentResult:
        source_text = " ".join(source.content for source in memory.sources.values() if source.source_id in room.source_inputs)
        narrative_names = list(NARRATIVE_RULES.keys()) + ["Unclassified Early Crypto"]
        llm_data = self.model_gateway.complete_json(
            agent_id=self.agent_id,
            task_type=self.task_type,
            system_prompt=self.system_prompt(
                "You are the Narrative Agent for a crypto research company. "
                "Classify the source into the provided narrative taxonomy. "
                "Return JSON only with keys: narratives, rationale."
            ),
            user_prompt=(
                f"Allowed narratives: {narrative_names}\n\n"
                f"Source content:\n{source_text}"
            ),
        )
        narratives = normalize_narratives(llm_data.get("narratives"), source_text)
        rationale = str(llm_data.get("rationale") or "").strip()
        related_sources = [source.source_id for source in memory.search_sources(" ".join(narratives), limit=3)]

        summary = "Narrative classification completed."
        finding = self.write_finding(
            room=room,
            memory=memory,
            finding_type="narrative_map",
            summary=summary,
            data={"narratives": narratives, "related_sources": related_sources, "rationale": rationale},
            sources=related_sources,
            confidence=0.75 if narratives else 0.45,
        )
        bus.request(
            room_id=room.room_id,
            from_agent=self.agent_id,
            to_agent="discovery_agent",
            objective="Find early project candidates that match the detected narratives.",
            required_output=["project_name", "website", "chain", "token_status", "reason_found"],
            context={"narratives": narratives, "finding_id": finding.finding_id},
            priority="high",
        )
        return AgentResult(
            self.agent_id,
            summary,
            {"narratives": narratives, "finding_id": finding.finding_id},
            related_sources,
            0.75 if narratives else 0.45,
        )


def classify_narratives(text: str) -> list[str]:
    lowered = text.lower()
    selected: list[str] = []
    for narrative, keywords in NARRATIVE_RULES.items():
        if any(keyword in lowered for keyword in keywords):
            selected.append(narrative)
    return selected or ["Unclassified Early Crypto"]


def normalize_narratives(value: object, source_text: str) -> list[str]:
    allowed = set(NARRATIVE_RULES.keys()) | {"Unclassified Early Crypto"}
    if isinstance(value, list):
        narratives = [str(item) for item in value if str(item) in allowed]
        if narratives:
            return narratives
    return classify_narratives(source_text)
