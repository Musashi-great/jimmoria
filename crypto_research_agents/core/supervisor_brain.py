from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crypto_research_agents.core.company_settings import CompanySettings
from crypto_research_agents.core.supervisor_intake import SupervisorIntakeDecision, decide_supervisor_intake
from crypto_research_agents.core.supervisor_memory import SupervisorMemoryStore, supervisor_memory_path_for
from crypto_research_agents.core.supervisor_session import SupervisorSessionStore, supervisor_session_path_for


@dataclass(slots=True)
class SupervisorBrainTurn:
    user_message: str
    route_line: str
    decision: SupervisorIntakeDecision
    recent_dialogue: list[dict[str, str]] = field(default_factory=list)
    memory_context: list[str] = field(default_factory=list)
    session_context: list[str] = field(default_factory=list)
    captured_memory_keys: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_message": self.user_message,
            "route_line": self.route_line,
            "decision": self.decision.to_dict(),
            "recent_dialogue": self.recent_dialogue,
            "memory_context": self.memory_context,
            "session_context": self.session_context,
            "captured_memory_keys": self.captured_memory_keys,
        }


class SupervisorBrain:
    """Hermes-style front-door state layer for JIMMORIA Supervisor."""

    def __init__(
        self,
        *,
        memory_store: SupervisorMemoryStore,
        session_store: SupervisorSessionStore,
    ) -> None:
        self.memory_store = memory_store
        self.session_store = session_store

    @classmethod
    def for_memory_path(cls, memory_path: str | Path) -> "SupervisorBrain":
        return cls(
            memory_store=SupervisorMemoryStore.load(supervisor_memory_path_for(memory_path)),
            session_store=SupervisorSessionStore.load(supervisor_session_path_for(memory_path)),
        )

    def prepare_turn(self, user_message: str, route_line: str, settings: CompanySettings) -> SupervisorBrainTurn:
        captured = self.memory_store.observe_user_message(route_line, settings)
        if captured:
            self.memory_store.save()
        decision = decide_supervisor_intake(route_line, settings)
        return SupervisorBrainTurn(
            user_message=user_message,
            route_line=route_line,
            decision=decision,
            recent_dialogue=self.session_store.recent_dialogue(limit=16),
            memory_context=self.memory_store.prompt_lines(route_line, limit=10),
            session_context=self.session_store.memory_summary_lines(),
            captured_memory_keys=[item.key for item in captured],
        )

    def record_reply(
        self,
        turn: SupervisorBrainTurn,
        reply_lines: list[str],
        *,
        room_id: str = "",
        topic: str = "",
    ) -> None:
        if room_id:
            self.session_store.set_last_room(room_id, topic)
            self.memory_store.remember(
                key="last_research_room",
                value=f"{room_id}: {topic}",
                category="runtime_state",
                source="runtime",
                confidence=0.8,
            )
            self.memory_store.save()
        self.session_store.record_turn(
            user_message=turn.user_message,
            supervisor_reply="\n".join(reply_lines),
            decision=turn.decision.to_dict(),
        )
        self.session_store.save()
