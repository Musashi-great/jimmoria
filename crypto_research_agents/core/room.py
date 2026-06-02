from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from .time import utc_now
from .runtime_state import RuntimeState


@dataclass(slots=True)
class ResearchRoom:
    topic: str
    goals: list[str]
    agents: list[str]
    room_id: str = field(default_factory=lambda: f"room_{uuid4().hex[:10]}")
    source_inputs: list[str] = field(default_factory=list)
    project_card: dict[str, Any] = field(default_factory=dict)
    shared_findings: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    report_draft: str | None = None
    output_paths: dict[str, str] = field(default_factory=dict)
    status: str = RuntimeState.CREATED.value
    created_at: str = field(default_factory=utc_now)

    def add_source(self, source_id: str) -> None:
        if source_id not in self.source_inputs:
            self.source_inputs.append(source_id)

    def add_finding(self, finding_id: str) -> None:
        if finding_id not in self.shared_findings:
            self.shared_findings.append(finding_id)

    def add_open_question(self, question: str) -> None:
        if question not in self.open_questions:
            self.open_questions.append(question)

    def close(self) -> None:
        self.status = RuntimeState.COMPLETED.value

    def set_status(self, status: RuntimeState | str) -> None:
        self.status = status.value if isinstance(status, RuntimeState) else status

    def to_dict(self) -> dict[str, Any]:
        return {
            "room_id": self.room_id,
            "topic": self.topic,
            "goals": self.goals,
            "agents": self.agents,
            "source_inputs": self.source_inputs,
            "project_card": self.project_card,
            "shared_findings": self.shared_findings,
            "open_questions": self.open_questions,
            "report_draft": self.report_draft,
            "output_paths": self.output_paths,
            "status": self.status,
            "created_at": self.created_at,
        }
