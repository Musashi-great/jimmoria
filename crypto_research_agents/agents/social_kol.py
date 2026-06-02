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
            rows.append(
                {
                    "project_id": project_id,
                    "project_name": project.name,
                    "mention_trend": "unconfigured",
                    "key_accounts": [],
                    "community_signal": "Live X/Telegram connector not configured yet.",
                    "tool_status": tool_result["status"],
                }
            )

        summary = "Social/KOL check queued through Tool Gateway; live connectors are not configured in MVP."
        finding = self.write_finding(
            room=room,
            memory=memory,
            finding_type="social_kol_signal",
            summary=summary,
            data={"rows": rows},
            confidence=0.35,
        )
        for request in requests:
            bus.response(
                request=request,
                from_agent=self.agent_id,
                result={"rows": rows, "finding_id": finding.finding_id},
                confidence=0.35,
                notes=["Live social connector is not configured."],
            )
        return AgentResult(self.agent_id, summary, {"finding_id": finding.finding_id, "rows": rows}, confidence=0.35)


def _collect_candidate_ids(requests: list[Any]) -> list[str]:
    candidate_ids: list[str] = []
    for request in requests:
        candidate_ids.extend(request.context.get("candidate_ids", []))
    return sorted(set(candidate_ids))
