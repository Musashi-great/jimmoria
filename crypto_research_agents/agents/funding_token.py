from __future__ import annotations

from typing import Any

from crypto_research_agents.agents.base import AgentResult, BaseAgent
from crypto_research_agents.core.bus import CollaborationBus
from crypto_research_agents.core.memory import SharedMemory
from crypto_research_agents.core.message import MessageType
from crypto_research_agents.core.room import ResearchRoom


class FundingTokenAgent(BaseAgent):
    agent_id = "funding_token_agent"
    name = "Funding / Token Agent"
    task_type = "funding_token"

    def run(self, room: ResearchRoom, memory: SharedMemory, bus: CollaborationBus, **kwargs: Any) -> AgentResult:
        requests = bus.find(room_id=room.room_id, to_agent=self.agent_id, message_type=MessageType.REQUEST)
        rows = []
        for project_id in _collect_candidate_ids(requests):
            project = memory.projects[project_id]
            tool_result = self.tool_gateway.call(
                self.agent_id,
                "check_airdrop_points",
                room_id=room.room_id,
                project_name=project.name,
            )
            tool_data = tool_result.get("data") if isinstance(tool_result.get("data"), dict) else {}
            hints = tool_data.get("hints", []) if isinstance(tool_data.get("hints"), list) else []
            evidence_text = _evidence_text(project.metadata)
            rows.append(
                {
                    "project_id": project_id,
                    "project_name": project.name,
                    "funding_status": "unknown",
                    "points_status": _points_status(evidence_text, tool_data),
                    "token_opportunity": _token_opportunity(project.token_status, evidence_text),
                    "token_status": project.token_status,
                    "tool_status": tool_result.get("status"),
                    "airdrop_hints": hints[:5],
                    "note": _note(evidence_text, tool_result),
                }
            )
        signal_rows = sum(1 for row in rows if row["points_status"] != "unknown" or row["token_opportunity"] != "unknown")
        summary = (
            f"Funding/token check extracted token or incentive hints for {signal_rows}/{len(rows)} candidates from available evidence."
            if rows
            else "Funding/token check found no candidate projects to inspect."
        )
        llm_analysis = self.llm_analysis_pass(
            room=room,
            objective="Interpret funding, points, airdrop, and token opportunity evidence without turning it into investment advice.",
            evidence={"rows": rows},
            fallback_summary=summary,
        )
        finding = self.write_finding(
            room=room,
            memory=memory,
            finding_type="funding_token_signal",
            summary=summary,
            data={"rows": rows, "llm_analysis": llm_analysis},
            confidence=0.52 if signal_rows else 0.35,
        )
        for request in requests:
            bus.response(
                request=request,
                from_agent=self.agent_id,
                result={"rows": rows, "finding_id": finding.finding_id, "llm_analysis": llm_analysis},
                confidence=finding.confidence,
                notes=[summary, str(llm_analysis.get("summary", summary))],
            )
        return AgentResult(self.agent_id, summary, {"finding_id": finding.finding_id, "rows": rows, "llm_analysis": llm_analysis}, confidence=finding.confidence)


def _collect_candidate_ids(requests: list[Any]) -> list[str]:
    candidate_ids: list[str] = []
    for request in requests:
        candidate_ids.extend(request.context.get("candidate_ids", []))
    return sorted(set(candidate_ids))


def _evidence_text(metadata: dict[str, Any]) -> str:
    parts: list[str] = []
    for result in metadata.get("web_results", []):
        if isinstance(result, dict):
            parts.extend([str(result.get("title", "")), str(result.get("snippet", "")), str(result.get("url", ""))])
    for key in ["website_crawl", "docs_crawl"]:
        value = metadata.get(key)
        if isinstance(value, dict):
            parts.append(str(value))
    return " ".join(parts).lower()


def _points_status(evidence_text: str, tool_data: dict[str, Any] | None = None) -> str:
    if tool_data and tool_data.get("classification") == "hint_found":
        return "hint_found"
    if any(keyword in evidence_text for keyword in ["points", "airdrop", "quest", "rewards campaign"]):
        return "hint_found"
    return "unknown"


def _token_opportunity(project_status: str, evidence_text: str) -> str:
    if any(keyword in evidence_text for keyword in [" prl", "ticker", "block reward", "mining", "coin", "emissions"]):
        return "native_or_mining_token_signal"
    if project_status not in {"unknown", ""}:
        return project_status
    return "unknown"


def _note(evidence_text: str, tool_result: dict[str, Any]) -> str:
    status = str(tool_result.get("status") or "unknown")
    if "mining" in evidence_text or "block reward" in evidence_text:
        return "Evidence mentions mining/block rewards; treat as token mechanics research, not investment advice."
    if "points" in evidence_text or "airdrop" in evidence_text:
        return "Evidence mentions points/airdrop-style incentives; requires official confirmation."
    if status == "success":
        return "Airdrop/points connector ran, but no public hint was found in the searched sources."
    return f"Airdrop/points connector status: {status}; treat incentive status as unknown."
