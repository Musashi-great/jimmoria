from __future__ import annotations

from typing import Any

from jimmoria.agents.base import AgentResult, BaseAgent
from jimmoria.core.bus import CollaborationBus
from jimmoria.core.memory import SharedMemory
from jimmoria.core.message import MessageType
from jimmoria.core.project_profile import find_project_profile_in_text
from jimmoria.core.room import ResearchRoom


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
            funding = _funding_profile(project.name, evidence_text, project.metadata)
            rows.append(
                {
                    "project_id": project_id,
                    "project_name": project.name,
                    "funding_status": funding["status"],
                    "funding_amount": funding["amount"],
                    "funding_stage": funding["stage"],
                    "lead_investors": funding["lead_investors"],
                    "investors": funding["investors"],
                    "funding_sources": funding["sources"],
                    "points_status": _points_status(evidence_text, tool_data),
                    "token_opportunity": _token_opportunity(project.token_status, evidence_text),
                    "token_status": project.token_status,
                    "tool_status": tool_result.get("status"),
                    "airdrop_hints": hints[:5],
                    "note": _note(evidence_text, tool_result, funding),
                }
            )
        signal_rows = sum(
            1
            for row in rows
            if row["points_status"] != "unknown"
            or row["token_opportunity"] != "unknown"
            or row["funding_status"] != "unknown"
        )
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
    hints = tool_data.get("hints", []) if isinstance(tool_data, dict) and isinstance(tool_data.get("hints"), list) else []
    if tool_data and tool_data.get("classification") == "hint_found" and hints:
        return "hint_found"
    project_specific_phrases = [
        "points program",
        "airdrop program",
        "airdrop campaign",
        "quest campaign",
        "testnet rewards",
        "rewards campaign",
        "waitlist rewards",
    ]
    if any(keyword in evidence_text for keyword in project_specific_phrases):
        return "hint_found"
    return "unknown"


def _token_opportunity(project_status: str, evidence_text: str) -> str:
    if any(keyword in evidence_text for keyword in [" prl", "ticker prl", "block reward", "proof-of-useful-work", "emissions"]):
        return "native_or_mining_token_signal"
    if project_status not in {"unknown", ""}:
        return project_status
    return "unknown"


def _funding_profile(project_name: str, evidence_text: str, metadata: dict[str, Any]) -> dict[str, Any]:
    text = f"{project_name} {evidence_text}".lower()
    sources = _funding_sources(metadata)
    profile = find_project_profile_in_text(text)
    if profile and profile.funding:
        funding = dict(profile.funding)
        profile_sources = [
            str(item.get("url"))
            for item in profile.article_notes
            if isinstance(item, dict) and item.get("url")
        ]
        return {
            "status": funding.get("status", "funding_reported"),
            "amount": funding.get("amount", "unknown"),
            "stage": funding.get("stage", "unknown"),
            "lead_investors": funding.get("lead_investors", []),
            "investors": funding.get("investors", []),
            "sources": _dedupe([*sources, *profile_sources])[:8],
        }
    if any(keyword in text for keyword in ["seed round", "funding round", "raised $", "raises $"]):
        return {
            "status": "funding_reported_unparsed",
            "amount": "unknown",
            "stage": "unknown",
            "lead_investors": [],
            "investors": [],
            "sources": sources,
        }
    return {
        "status": "unknown",
        "amount": "unknown",
        "stage": "unknown",
        "lead_investors": [],
        "investors": [],
        "sources": sources,
    }


def _funding_sources(metadata: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for result in metadata.get("web_results", []):
        if not isinstance(result, dict):
            continue
        text = " ".join(str(result.get(key, "")) for key in ["title", "snippet", "url"]).lower()
        if any(keyword in text for keyword in ["fund", "seed", "paradigm", "venture", "invest"]):
            url = str(result.get("url") or "")
            if url:
                urls.append(url)
    for url in metadata.get("evidence_urls", []):
        value = str(url)
        lowered = value.lower()
        if any(keyword in lowered for keyword in ["theblock", "paradigm", "wmt_ventures", "1930264347441615188"]):
            urls.append(value)
    deduped: list[str] = []
    for url in urls:
        if url not in deduped:
            deduped.append(url)
    return deduped[:8]


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _note(evidence_text: str, tool_result: dict[str, Any], funding: dict[str, Any] | None = None) -> str:
    status = str(tool_result.get("status") or "unknown")
    if funding and funding.get("status") != "unknown":
        amount = funding.get("amount", "unknown")
        leads = ", ".join(str(item) for item in funding.get("lead_investors", []) if item) or "unknown lead"
        return f"Funding evidence indicates {funding.get('stage', 'unknown')} financing of {amount}, led by {leads}; verify against official and reputable article sources."
    if "block reward" in evidence_text or "proof-of-useful-work" in evidence_text:
        return "Evidence mentions mining/block rewards; treat as token mechanics research, not investment advice."
    if _points_status(evidence_text) == "hint_found":
        return "Project-specific points/airdrop-style incentive language appeared in official or searched evidence; requires confirmation."
    if status == "success":
        return "Airdrop/points connector ran, but no public hint was found in the searched sources."
    return f"Airdrop/points connector status: {status}; treat incentive status as unknown."
