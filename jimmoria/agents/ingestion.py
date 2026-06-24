from __future__ import annotations

import re
from typing import Any

from jimmoria.agents.base import AgentResult, BaseAgent
from jimmoria.core.bus import CollaborationBus
from jimmoria.core.memory import SharedMemory, SourceRecord
from jimmoria.core.room import ResearchRoom


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
                "You are the Ingestion Agent inside a Hermes-led personal crypto research stack. "
                "Extract a concise summary, entities, and keywords. "
                "Return JSON only with keys: summary, entities, keywords."
            ),
            user_prompt=f"Title: {title}\n\nSource content:\n{content}",
        )
        entities = _list_or_fallback(llm_data.get("entities"), extract_entities(content))
        keywords = _list_or_fallback(llm_data.get("keywords"), extract_keywords(content))
        llm_summary = str(llm_data.get("summary") or "").strip()
        source_metadata: dict[str, Any] = {
            "summary": llm_summary,
            "entities": entities,
            "keywords": keywords,
        }
        content_hash = None
        canonical_url = None
        raw_path = None
        source_quality_score = 0.55

        if url:
            fetch_result = self.tool_gateway.call(
                self.agent_id,
                "fetch_url",
                room_id=room.room_id,
                url=str(url),
            )
            fetch_data = fetch_result.get("data") if isinstance(fetch_result.get("data"), dict) else {}
            if fetch_result.get("status") == "success":
                content_hash = str(fetch_data.get("content_hash") or "") or None
                canonical_url = str(fetch_data.get("canonical_url") or "") or None
                source_quality_score = 0.75
                source_metadata["url_fetch"] = {
                    "status": fetch_result.get("status"),
                    "title": fetch_data.get("title"),
                    "meta_description": fetch_data.get("meta_description"),
                    "canonical_url": canonical_url,
                    "content_hash": content_hash,
                    "official_links": fetch_data.get("official_links", {}),
                    "signals": fetch_data.get("signals", {}),
                }
            else:
                source_metadata["url_fetch"] = {
                    "status": fetch_result.get("status"),
                    "message": fetch_result.get("message"),
                }

        if content:
            snapshot_result = self.tool_gateway.call(
                self.agent_id,
                "archive_source_snapshot",
                room_id=room.room_id,
                content=str(content),
                url=str(url) if url else None,
            )
            snapshot_data = snapshot_result.get("data") if isinstance(snapshot_result.get("data"), dict) else {}
            raw_path = str(snapshot_data.get("raw_path") or "") or None
            content_hash = content_hash or str(snapshot_data.get("content_hash") or "") or None

        source = memory.add_source(
            SourceRecord(
                title=title,
                content=content,
                source_type=source_type,
                url=url,
                metadata=source_metadata,
                content_hash=content_hash,
                canonical_url=canonical_url,
                raw_path=raw_path,
                source_quality_score=source_quality_score,
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
