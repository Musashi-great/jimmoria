from __future__ import annotations

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
