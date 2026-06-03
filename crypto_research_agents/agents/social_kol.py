from __future__ import annotations

from typing import Any

from crypto_research_agents.agents.base import AgentResult, BaseAgent
from crypto_research_agents.core.bus import CollaborationBus
from crypto_research_agents.core.memory import SharedMemory
from crypto_research_agents.core.message import MessageType
from crypto_research_agents.core.room import ResearchRoom


class SocialKOLAgent(BaseAgent):
    agent_id = "social_kol_agent"
    name = "Social / KOL Agent"
    task_type = "social_summary"

    def run(self, room: ResearchRoom, memory: SharedMemory, bus: CollaborationBus, **kwargs: Any) -> AgentResult:
        requests = bus.find(room_id=room.room_id, to_agent=self.agent_id, message_type=MessageType.REQUEST)
        candidate_ids = _collect_candidate_ids(requests)
        rows = []
        for project_id in candidate_ids:
            project = memory.projects[project_id]
            tool_result = self.tool_gateway.call(
                self.agent_id,
                "x_search_posts",
                room_id=room.room_id,
                query=project.name,
            )
            website_result = self.tool_gateway.call(
                self.agent_id,
                "crawl_website",
                room_id=room.room_id,
                url=project.website,
                project_name=project.name,
            )
            web_result = self.tool_gateway.call(
                self.agent_id,
                "web_search",
                room_id=room.room_id,
                query=f"{project.name} X Twitter official community",
                limit=5,
            )
            website_data = website_result.get("data") if isinstance(website_result.get("data"), dict) else {}
            web_data = web_result.get("data") if isinstance(web_result.get("data"), dict) else {}
            social_urls = _social_urls(project.metadata, website_data, web_data.get("results", []))
            rows.append(
                {
                    "project_id": project_id,
                    "project_name": project.name,
                    "mention_trend": _mention_trend(tool_result),
                    "key_accounts": social_urls,
                    "community_signal": _community_signal(social_urls, tool_result["status"], web_result["status"]),
                    "tool_status": tool_result["status"],
                    "website_status": website_result["status"],
                    "web_search_status": web_result["status"],
                }
            )

        linked = sum(1 for row in rows if row["key_accounts"])
        summary = (
            f"Social/KOL check found public social links for {linked}/{len(rows)} candidates; live X status: {_status_summary(rows)}."
            if rows
            else "Social/KOL check found no candidate projects to inspect."
        )
        llm_analysis = self.llm_analysis_pass(
            room=room,
            objective="Interpret public web and X/KOL evidence, separate official links from live mention history, and avoid private chat connectors.",
            evidence={"rows": rows},
            fallback_summary=summary,
        )
        finding = self.write_finding(
            room=room,
            memory=memory,
            finding_type="social_kol_signal",
            summary=summary,
            data={"rows": rows, "llm_analysis": llm_analysis},
            confidence=0.35,
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


def _social_urls(metadata: dict[str, Any], website_data: dict[str, Any], web_results: list[Any]) -> list[str]:
    urls: list[str] = []
    official_links = website_data.get("official_links") if isinstance(website_data.get("official_links"), dict) else {}
    for bucket in ["x"]:
        for link in official_links.get(bucket, []):
            if isinstance(link, dict):
                urls.extend(_maybe_social_url(link.get("url")))
    for result in metadata.get("web_results", []):
        if isinstance(result, dict):
            urls.extend(_maybe_social_url(result.get("url")))
    for result in web_results:
        if isinstance(result, dict):
            urls.extend(_maybe_social_url(result.get("url")))
    return _dedupe(urls)[:10]


def _maybe_social_url(value: object) -> list[str]:
    url = str(value or "")
    lowered = url.lower()
    if any(host in lowered for host in ["x.com/", "twitter.com/"]):
        return [url]
    return []


def _community_signal(social_urls: list[str], x_status: str, web_status: str) -> str:
    if social_urls:
        if x_status == "success":
            return "Official/community social links found and live X search returned post evidence."
        return "Official X/community links found through public web evidence; private Telegram/Discord connectors are intentionally out of scope."
    if x_status in {"missing_secret", "unconfigured"} and web_status == "success":
        return "No X handle resolved from web search; continue with public website, docs, GitHub, and market metadata evidence."
    if x_status == "success":
        return "Live X connector returned data, but no official/community URL was resolved."
    return "Live social connector did not return usable evidence yet."


def _mention_trend(tool_result: dict[str, Any]) -> str:
    status = str(tool_result.get("status") or "")
    if status == "success":
        posts = tool_result.get("data", {}).get("posts", []) if isinstance(tool_result.get("data"), dict) else []
        return f"live_posts:{len(posts)}"
    return status or "unknown"


def _status_summary(rows: list[dict[str, Any]]) -> str:
    statuses = sorted({str(row.get("tool_status") or "unknown") for row in rows})
    return ", ".join(statuses) if statuses else "unknown"


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped
