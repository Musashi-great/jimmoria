from __future__ import annotations

from dataclasses import asdict
from typing import Any

from crypto_research_agents.agents.base import AgentResult, BaseAgent
from crypto_research_agents.core.bus import CollaborationBus
from crypto_research_agents.core.memory import SharedMemory
from crypto_research_agents.core.room import ResearchRoom


class SupervisorAgent(BaseAgent):
    agent_id = "supervisor_agent"
    name = "Supervisor Agent"
    task_type = "supervision"

    def run(self, room: ResearchRoom, memory: SharedMemory, bus: CollaborationBus, **kwargs: Any) -> AgentResult:
        decision = self.model_gateway.select(agent_id=self.agent_id, task_type=self.task_type)
        goals = kwargs.get("goals") or room.goals
        company_settings = kwargs.get("company_settings") if isinstance(kwargs.get("company_settings"), dict) else {}
        intake_decision = kwargs.get("intake_decision") if isinstance(kwargs.get("intake_decision"), dict) else {}
        supervisor_mode = company_settings.get("supervisor_mode", "research_director")
        summary = (
            "Research room initialized by CEO-style supervisor intake and output routing."
            if supervisor_mode == "company_ceo"
            else "Research room initialized with controlled P2P collaboration."
        )
        finding = self.write_finding(
            room=room,
            memory=memory,
            finding_type="supervision_plan",
            summary=summary,
            data={
                "topic": room.topic,
                "goals": goals,
                "agents": room.agents,
                "model_decision": asdict(decision),
                "company_settings": company_settings,
                "intake_decision": intake_decision,
            },
            confidence=0.9,
        )
        bus.update(
            room_id=room.room_id,
            from_agent=self.agent_id,
            summary="Research room created after supervisor intake, output routing, and goals set.",
            payload={"finding_id": finding.finding_id, "goals": goals, "intake_decision": intake_decision},
        )
        return AgentResult(self.agent_id, summary, {"finding_id": finding.finding_id}, confidence=0.9)
