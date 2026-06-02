from __future__ import annotations

import re
from typing import Any

from crypto_research_agents.agents.base import AgentResult, BaseAgent
from crypto_research_agents.core.bus import CollaborationBus
from crypto_research_agents.core.memory import SharedMemory, SourceRecord
from crypto_research_agents.core.room import ResearchRoom


class IngestionAgent(BaseAgent):
    agent_id = "ingestion_agent"
    name = "Ingestion Agent"
    task_type = "source_ingestion"

    def run(self, room: ResearchRoom, memory: SharedMemory, bus: CollaborationBus, **kwargs: Any) -> AgentResult:
        title = kwargs.get("title") or room.topic
        content = kwargs.get("content", "")
        url = kwargs.get("url")
        source_type = kwargs.get("source_type", "article")
        llm_data = self.model_gateway.complete_json(
            agent_id=self.agent_id,
            task_type=self.task_type,
            system_prompt=self.system_prompt(
                "You are the Ingestion Agent for a crypto research company. "
                "Extract a concise summary, entities, and keywords. "
                "Return JSON only with keys: summary, entities, keywords."
            ),
            user_prompt=f"Title: {title}\n\nSource content:\n{content}",
        )
        entities = _list_or_fallback(llm_data.get("entities"), extract_entities(content))
        keywords = _list_or_fallback(llm_data.get("keywords"), extract_keywords(content))
        llm_summary = str(llm_data.get("summary") or "").strip()

        source = memory.add_source(
            SourceRecord(
                title=title,
                content=content,
                source_type=source_type,
                url=url,
                metadata={
                    "summary": llm_summary,
                    "entities": entities,
                    "keywords": keywords,
                },
            )
        )
        room.add_source(source.source_id)

        summary = f"Stored source {source.source_id} and extracted initial entities."
        finding = self.write_finding(
            room=room,
            memory=memory,
            finding_type="source_ingestion",
            summary=summary,
            data=source.metadata,
            sources=[source.source_id],
            confidence=0.8,
        )
        bus.handoff(
            room_id=room.room_id,
            from_agent=self.agent_id,
            to_agent="narrative_agent",
            summary="Source ingested. Narrative extraction requested.",
            payload={"source_id": source.source_id, "finding_id": finding.finding_id},
        )
        bus.handoff(
            room_id=room.room_id,
            from_agent=self.agent_id,
            to_agent="obsidian_curator_agent",
            summary="Source note should be created.",
            payload={"source_id": source.source_id},
        )
        return AgentResult(
            self.agent_id,
            summary,
            {"source_id": source.source_id, "metadata": source.metadata},
            [source.source_id],
            0.8,
        )


def _list_or_fallback(value: object, fallback: list[str]) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return fallback


def extract_entities(text: str) -> list[str]:
    candidates = re.findall(r"\b[A-Z][A-Za-z0-9]{2,}(?:\s+[A-Z][A-Za-z0-9]{2,})?\b", text)
    seen: set[str] = set()
    entities: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            entities.append(candidate)
            seen.add(candidate)
    return entities[:20]


def extract_keywords(text: str) -> list[str]:
    taxonomy = [
        "agent",
        "wallet",
        "automation",
        "intent",
        "defi",
        "consumer",
        "depin",
        "rwa",
        "restaking",
        "airdrop",
        "points",
        "testnet",
        "github",
        "docs",
    ]
    lowered = text.lower()
    return [keyword for keyword in taxonomy if keyword in lowered]
