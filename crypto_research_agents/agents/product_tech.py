from __future__ import annotations

from typing import Any

from crypto_research_agents.agents.base import AgentResult, BaseAgent
from crypto_research_agents.core.bus import CollaborationBus
from crypto_research_agents.core.memory import SharedMemory
from crypto_research_agents.core.message import MessageType
from crypto_research_agents.core.room import ResearchRoom


class ProductTechAgent(BaseAgent):
    agent_id = "product_tech_agent"
    name = "Product / Tech Agent"
    task_type = "product_docs"

    def run(self, room: ResearchRoom, memory: SharedMemory, bus: CollaborationBus, **kwargs: Any) -> AgentResult:
        requests = bus.find(room_id=room.room_id, to_agent=self.agent_id, message_type=MessageType.REQUEST)
        rows = []
        for project_id in _collect_candidate_ids(requests):
            project = memory.projects[project_id]
            target_url = _select_project_url(project.website, room, memory)
            website_result = self.tool_gateway.call(
                self.agent_id,
                "crawl_website",
                room_id=room.room_id,
                url=target_url,
                project_name=project.name,
            )
            docs_result = self.tool_gateway.call(
                self.agent_id,
                "crawl_docs",
                room_id=room.room_id,
                website_url=target_url,
                project_name=project.name,
            )
            website_data = website_result.get("data") if isinstance(website_result.get("data"), dict) else {}
            docs_data = docs_result.get("data") if isinstance(docs_result.get("data"), dict) else {}
            github_target = _select_github_target(website_data, project.metadata)
            github_result = self.tool_gateway.call(
                self.agent_id,
                "read_github_repo",
                room_id=room.room_id,
                repo_url=github_target,
            )
            github_activity_result = self.tool_gateway.call(
                self.agent_id,
                "github_get_repo_activity",
                room_id=room.room_id,
                repo_url=github_target,
                limit=8,
            )
            github_data = github_result.get("data") if isinstance(github_result.get("data"), dict) else {}
            github_activity_data = github_activity_result.get("data") if isinstance(github_activity_result.get("data"), dict) else {}
            if target_url:
                project.website = target_url
            project.metadata["website_crawl"] = _compact_website_data(website_data)
            project.metadata["docs_crawl"] = _compact_docs_data(docs_data)
            if github_data:
                project.metadata["github_read"] = github_data
            if github_activity_data:
                project.metadata["github_activity"] = _compact_github_activity(github_activity_data)
            rows.append(
                {
                    "project_id": project_id,
                    "project_name": project.name,
                    "target_url": target_url,
                    "product_status": website_data.get("product_status", "unknown"),
                    "docs_status": docs_data.get("docs_status", website_data.get("docs_status", "unknown")),
                    "github_status": "read" if github_result.get("status") == "success" else website_data.get("github_status", "unknown"),
                    "official_links": website_data.get("official_links", {}),
                    "github_repo": github_data.get("repo") if isinstance(github_data.get("repo"), dict) else None,
                    "github_languages": github_data.get("languages", {}),
                    "github_activity": _compact_github_activity(github_activity_data),
                    "technical_keywords": docs_data.get("technical_keywords", []),
                    "signals": {
                        "website": website_data.get("signals", {}),
                        "docs": docs_data.get("signals", {}),
                    },
                    "connector_status": {
                        "crawl_website": website_result.get("status"),
                        "crawl_docs": docs_result.get("status"),
                        "read_github_repo": github_result.get("status"),
                        "github_get_repo_activity": github_activity_result.get("status"),
                    },
                    "note": _row_note(website_result, docs_result),
                }
            )
        summary = _summary(rows)
        llm_analysis = self.llm_analysis_pass(
            room=room,
            objective="Interpret product, docs, and GitHub evidence and identify whether the project has real product readiness.",
            evidence={"rows": rows},
            fallback_summary=summary,
        )
        finding = self.write_finding(
            room=room,
            memory=memory,
            finding_type="product_tech_signal",
            summary=summary,
            data={"rows": rows, "llm_analysis": llm_analysis},
            confidence=_confidence(rows),
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


def _select_project_url(project_website: str | None, room: ResearchRoom, memory: SharedMemory) -> str | None:
    if project_website:
        return project_website
    for source_id in room.source_inputs:
        source = memory.sources.get(source_id)
        if source and source.url:
            return source.url
    return None


def _row_note(website_result: dict[str, Any], docs_result: dict[str, Any]) -> str:
    if website_result.get("status") == "success" or docs_result.get("status") == "success":
        return "Website/docs connector returned source-backed product data."
    if website_result.get("status") == "missing_input":
        return "No website URL available yet; provide a project URL for live product checks."
    return "Website/docs connector could not fetch live product data."


def _summary(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "Product/Tech check found no candidate projects to inspect."
    successful = sum(
        1
        for row in rows
        if row.get("connector_status", {}).get("crawl_website") == "success"
        or row.get("connector_status", {}).get("crawl_docs") == "success"
    )
    if successful:
        return f"Product/Tech check inspected {successful}/{len(rows)} candidates with live website/docs connectors."
    return "Product/Tech check ran registered connectors, but no candidate website URL was available."


def _confidence(rows: list[dict[str, Any]]) -> float:
    if not rows:
        return 0.3
    if any(row.get("connector_status", {}).get("crawl_website") == "success" for row in rows):
        return 0.65
    return 0.4


def _select_github_target(website_data: dict[str, Any], metadata: dict[str, Any]) -> str | None:
    official_links = website_data.get("official_links") if isinstance(website_data.get("official_links"), dict) else {}
    github_links = official_links.get("github", []) if isinstance(official_links.get("github"), list) else []
    for link in github_links:
        if isinstance(link, dict) and link.get("url"):
            return str(link["url"])
    for repo in metadata.get("github_repos", []):
        if isinstance(repo, dict) and repo.get("html_url"):
            return str(repo["html_url"])
    for result in metadata.get("web_results", []):
        if isinstance(result, dict) and "github.com" in str(result.get("url", "")).lower():
            return str(result["url"])
    return None


def _compact_website_data(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": data.get("final_url") or data.get("url"),
        "title": data.get("title"),
        "meta_description": data.get("meta_description"),
        "product_status": data.get("product_status"),
        "docs_status": data.get("docs_status"),
        "github_status": data.get("github_status"),
        "x_status": data.get("x_status"),
        "official_links": data.get("official_links", {}),
        "signals": data.get("signals", {}),
    }


def _compact_docs_data(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "docs_status": data.get("docs_status"),
        "pages": data.get("pages", []),
        "technical_keywords": data.get("technical_keywords", []),
        "signals": data.get("signals", {}),
    }


def _compact_github_activity(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "full_name": data.get("full_name"),
        "latest_commits": data.get("commits", [])[:5],
        "latest_releases": data.get("releases", [])[:5],
        "latest_issues": data.get("issues", [])[:5],
        "connector_status": data.get("connector_status", {}),
    }
