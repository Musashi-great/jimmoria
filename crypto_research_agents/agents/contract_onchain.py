from __future__ import annotations

from typing import Any

from crypto_research_agents.agents.base import AgentResult, BaseAgent
from crypto_research_agents.core.bus import CollaborationBus
from crypto_research_agents.core.memory import SharedMemory
from crypto_research_agents.core.message import MessageType
from crypto_research_agents.core.room import ResearchRoom


class ContractOnchainAgent(BaseAgent):
    agent_id = "contract_onchain_agent"
    name = "Contract / On-chain Agent"
    task_type = "contract_info"

    def run(self, room: ResearchRoom, memory: SharedMemory, bus: CollaborationBus, **kwargs: Any) -> AgentResult:
        requests = bus.find(room_id=room.room_id, to_agent=self.agent_id, message_type=MessageType.REQUEST)
        rows = []
        for project_id in _collect_candidate_ids(requests):
            project = memory.projects[project_id]
            self.tool_gateway.call(
                self.agent_id,
                "get_contract_address",
                room_id=room.room_id,
                project_name=project.name,
            )
            rows.append(
                {
                    "project_id": project_id,
                    "project_name": project.name,
                    "chain": project.chain or "unknown",
                    "token_status": project.token_status,
                    "contract_address": None,
                    "dex_pair": None,
                    "source": "not_configured",
                }
            )
        summary = "Contract/token check produced MVP placeholders; live explorer connectors are not configured."
        finding = self.write_finding(
            room=room,
            memory=memory,
            finding_type="contract_token_info",
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
                notes=["Explorer/RPC connector is not configured."],
            )
        return AgentResult(self.agent_id, summary, {"finding_id": finding.finding_id, "rows": rows}, confidence=0.35)


def _collect_candidate_ids(requests: list[Any]) -> list[str]:
    candidate_ids: list[str] = []
    for request in requests:
        candidate_ids.extend(request.context.get("candidate_ids", []))
    return sorted(set(candidate_ids))
