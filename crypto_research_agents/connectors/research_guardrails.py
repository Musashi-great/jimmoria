from __future__ import annotations

from typing import Any

from crypto_research_agents.connectors.base import missing_input, success
from crypto_research_agents.core.source_quality import (
    is_generic_platform_url,
    is_low_signal_url,
    is_relevant_source_url,
)


def url_safety_check(url: str | None = None) -> dict[str, Any]:
    if not url:
        return missing_input("url_safety_check", "url is required")
    data = {
        "url": url,
        "safe_for_read_only_research": not is_low_signal_url(url),
        "generic_platform": is_generic_platform_url(url),
        "low_signal": is_low_signal_url(url),
    }
    return success("url_safety_check", data, "url safety checked")


def source_relevance_filter(
    urls: list[str] | None = None,
    *,
    project_name: str | None = None,
    project_query: str | None = None,
) -> dict[str, Any]:
    if not urls:
        return missing_input("source_relevance_filter", "urls are required")
    project_stub = type(
        "ProjectStub",
        (),
        {
            "name": project_name or project_query or "",
            "website": "",
            "metadata": {"project_query": project_query or project_name or ""},
        },
    )()
    accepted: list[str] = []
    rejected: list[str] = []
    for url in urls:
        if is_relevant_source_url(project_stub, url):
            accepted.append(str(url))
        else:
            rejected.append(str(url))
    return success(
        "source_relevance_filter",
        {
            "project_name": project_name,
            "project_query": project_query,
            "accepted": accepted,
            "rejected": rejected,
        },
        f"accepted {len(accepted)}/{len(urls)} source URLs",
    )


def tool_call_guardrail(
    agent_id: str | None = None,
    tool_name: str | None = None,
    *,
    repeated_failures: int = 0,
) -> dict[str, Any]:
    if not agent_id or not tool_name:
        return missing_input("tool_call_guardrail", "agent_id and tool_name are required")
    decision = "allow"
    reason = "read-only research tool call is allowed"
    if repeated_failures >= 3:
        decision = "pause"
        reason = "same tool failed repeatedly; route back to Supervisor before another attempt"
    return success(
        "tool_call_guardrail",
        {
            "agent_id": agent_id,
            "tool_name": tool_name,
            "decision": decision,
            "reason": reason,
            "repeated_failures": repeated_failures,
        },
        reason,
    )
