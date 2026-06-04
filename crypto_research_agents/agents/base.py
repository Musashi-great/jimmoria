from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from crypto_research_agents.core.bus import CollaborationBus
from crypto_research_agents.core.agent_spec import AgentSpec
from crypto_research_agents.core.memory import FindingRecord, SharedMemory
from crypto_research_agents.core.model_gateway import ModelGateway
from crypto_research_agents.core.room import ResearchRoom
from crypto_research_agents.core.tool_gateway import ToolGateway


@dataclass(slots=True)
class AgentResult:
    agent_id: str
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    confidence: float = 0.5


class BaseAgent:
    agent_id = "base_agent"
    name = "Base Agent"
    task_type = "routine"

    def __init__(
        self,
        *,
        model_gateway: ModelGateway | None = None,
        tool_gateway: ToolGateway | None = None,
        spec: AgentSpec | None = None,
    ) -> None:
        self.model_gateway = model_gateway or ModelGateway()
        self.tool_gateway = tool_gateway or ToolGateway()
        self.spec = spec

    def system_prompt(self, fallback: str) -> str:
        if self.spec is not None:
            return self.spec.system_prompt()
        return fallback

    def run(self, room: ResearchRoom, memory: SharedMemory, bus: CollaborationBus, **kwargs: Any) -> AgentResult:
        raise NotImplementedError

    def llm_analysis_pass(
        self,
        *,
        room: ResearchRoom,
        objective: str,
        evidence: dict[str, Any],
        fallback_summary: str,
    ) -> dict[str, Any]:
        """Run an optional CrewAI-style task reflection pass over collected evidence.

        Tool and rule execution remain the source of truth. The LLM pass interprets
        that evidence, flags gaps, and proposes next actions. It is intentionally
        non-fatal so an optional reasoning pass cannot break the research room.
        """

        prompt = {
            "topic": room.topic,
            "agent_id": self.agent_id,
            "objective": objective,
            "evidence": evidence,
            "rules": [
                "Do not invent facts, URLs, token data, KOL mentions, or funding data.",
                "If evidence is missing or connector output is unconfigured, say so plainly.",
                "Return concise JSON only.",
            ],
            "required_json_keys": [
                "summary",
                "confidence",
                "evidence_gaps",
                "unclear_points",
                "next_actions",
            ],
        }
        try:
            data = self.model_gateway.complete_json(
                agent_id=self.agent_id,
                task_type=self.task_type,
                system_prompt=self.system_prompt(
                    "You are a specialist agent inside JIMMORIA. "
                    "Interpret tool results and shared memory as a research worker. "
                    "Be evidence-bound and never fabricate missing live data."
                ),
                user_prompt=json.dumps(prompt, ensure_ascii=False, default=str),
            )
        except Exception as exc:  # pragma: no cover - defensive around optional providers
            return {
                "status": "llm_failed",
                "summary": fallback_summary,
                "confidence": 0.0,
                "evidence_gaps": ["LLM analysis pass failed."],
                "unclear_points": [str(exc)],
                "risks": [str(exc)],
                "next_actions": [],
            }
        return normalize_llm_analysis(data, fallback_summary=fallback_summary)

    def write_finding(
        self,
        *,
        room: ResearchRoom,
        memory: SharedMemory,
        finding_type: str,
        summary: str,
        data: dict[str, Any] | None = None,
        sources: list[str] | None = None,
        confidence: float = 0.5,
    ) -> FindingRecord:
        finding = memory.add_finding(
            FindingRecord(
                room_id=room.room_id,
                agent_id=self.agent_id,
                finding_type=finding_type,
                summary=summary,
                data=data or {},
                sources=sources or [],
                confidence=confidence,
            )
        )
        room.add_finding(finding.finding_id)
        return finding


def normalize_llm_analysis(data: dict[str, Any], *, fallback_summary: str) -> dict[str, Any]:
    def string_list(value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()][:8]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    confidence_value = data.get("confidence")
    try:
        confidence = float(confidence_value)
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(confidence, 1.0))

    summary = str(data.get("summary") or fallback_summary).strip() or fallback_summary
    unclear_points = string_list(data.get("unclear_points") or data.get("risks"))
    return {
        "status": "ok",
        "summary": summary,
        "confidence": confidence,
        "evidence_gaps": string_list(data.get("evidence_gaps") or data.get("gaps")),
        "unclear_points": unclear_points,
        "risks": unclear_points,
        "next_actions": string_list(data.get("next_actions") or data.get("next_steps")),
    }
