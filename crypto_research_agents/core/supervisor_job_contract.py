from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from crypto_research_agents.core.supervisor_intake import SupervisorIntakeDecision


@dataclass(slots=True)
class SupervisorJobContract:
    """Bounded loop contract chosen by the Supervisor before work dispatch."""

    loop_mode: str
    output_mode: str
    goal: str
    owner_agent: str = "supervisor_agent"
    process_id: str = ""
    topic: str = ""
    agent_ids: list[str] = field(default_factory=list)
    source_requirements: list[str] = field(default_factory=list)
    cost_controls: dict[str, Any] = field(default_factory=dict)
    verification_gates: list[str] = field(default_factory=list)
    completion_criteria: list[str] = field(default_factory=list)
    iteration_policy: dict[str, Any] = field(default_factory=dict)
    ui_policy: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_supervisor_job_contract(
    *,
    line: str,
    decision: SupervisorIntakeDecision | dict[str, Any],
    process_id: str = "",
    agent_ids: list[str] | None = None,
    topic: str = "",
) -> SupervisorJobContract:
    decision_dict = decision.to_dict() if isinstance(decision, SupervisorIntakeDecision) else dict(decision or {})
    output_mode = str(decision_dict.get("output_mode") or "supervisor_reply")
    intent_type = str(decision_dict.get("intent_type") or "supervisor_chat")
    needs_room = bool(decision_dict.get("needs_research_room"))
    agent_list = list(agent_ids or [])
    target = topic.strip() or line.strip()

    if needs_room or output_mode in {"research_dossier", "source_note"} or intent_type in {"research_request", "source_ingestion"}:
        return _closed_fleet_contract(
            line=line,
            output_mode=output_mode,
            intent_type=intent_type,
            process_id=process_id,
            agent_ids=agent_list,
            topic=target,
        )

    return SupervisorJobContract(
        loop_mode="single_agent_chat",
        output_mode=output_mode,
        goal="Answer or adjust settings directly without opening a Research Room.",
        process_id=process_id,
        topic=target,
        agent_ids=["supervisor_agent"],
        cost_controls={
            "max_visible_agents": 1,
            "max_agent_attempts": 1,
            "dispatch_requires_confirmation": False,
        },
        verification_gates=[
            "Do not open a Research Room for normal chat, settings, status, or saved-report retrieval.",
            "Do not hide internal uncertainty if the user asks about system behavior.",
        ],
        completion_criteria=[
            "Supervisor answered directly or persisted the requested setting/memory update.",
        ],
        iteration_policy={
            "mode": "bounded_single_turn",
            "max_agent_attempts": 1,
            "max_revision_rounds": 0,
            "loop_until_goal_met": False,
        },
        ui_policy={
            "visible_mode": "conversation",
            "raw_logs": "not_applicable",
        },
        notes=["Single-agent loop is used for Supervisor conversation and lightweight control-plane work."],
    )


def _closed_fleet_contract(
    *,
    line: str,
    output_mode: str,
    intent_type: str,
    process_id: str,
    agent_ids: list[str],
    topic: str,
) -> SupervisorJobContract:
    if intent_type == "source_ingestion":
        verification_gates = [
            "Input source is saved or deduplicated in SharedMemory.",
            "Obsidian/source note sync completes without treating the source as a verified report.",
        ]
        completion_criteria = [
            "Source artifact is persisted.",
            "No client-facing project dossier is created unless explicitly requested.",
        ]
        max_llm_calls = 6
        max_agent_attempts = 2
    else:
        verification_gates = [
            "Identity Gate must separate candidate trigger from verified project identity.",
            "Social/KOL/article signal must be separated from official-source proof.",
            "Product/docs/GitHub evidence must be checked where available.",
            "Token, contract, chain, and value-capture claims must be marked confirmed, partial, or unverified.",
            "Report output must be a reader-facing project intelligence memo, not raw logs.",
        ]
        completion_criteria = [
            "Supervisor owns the goal and delegates specialist evidence work.",
            "Specialist findings are merged through council/final review.",
            "Report or diagnostic memo states missing evidence plainly.",
            "Evidence packet and raw logs remain in artifacts, not the main report body.",
        ]
        max_llm_calls = 40
        max_agent_attempts = 2

    return SupervisorJobContract(
        loop_mode="closed_fleet",
        output_mode=output_mode,
        goal="Run a bounded specialist fleet loop for the user's requested work.",
        process_id=process_id,
        topic=topic or line.strip(),
        agent_ids=agent_ids,
        source_requirements=[
            "Prefer official site, official X, docs, GitHub, explorer, DEX/market metadata, or reputable article URLs.",
            "If required source context is missing, ask before launching the fleet.",
        ],
        cost_controls={
            "max_llm_calls": max_llm_calls,
            "max_agent_attempts": max_agent_attempts,
            "max_revision_rounds": 2,
            "dispatch_requires_confirmation": True,
            "raw_logs_visible_by_default": False,
        },
        verification_gates=verification_gates,
        completion_criteria=completion_criteria,
        iteration_policy={
            "mode": "bounded_gap_fix",
            "max_agent_attempts": max_agent_attempts,
            "max_revision_rounds": 2,
            "loop_until_goal_met": False,
            "retry_only_failed_or_missing_evidence": True,
        },
        ui_policy={
            "visible_mode": "fixed_agent_dashboard",
            "show_agent_current_work": True,
            "show_total_token_usage": True,
            "raw_logs": "background_artifacts",
        },
        notes=[
            "Open exploration is limited to candidate discovery; the room itself is a closed fleet loop.",
            "The Supervisor owns the goal; specialists own evidence slices.",
        ],
    )


def max_agent_attempts_from_contract(contract: dict[str, Any] | None, *, default: int = 2) -> int:
    if not isinstance(contract, dict):
        return default
    policy = contract.get("iteration_policy")
    controls = contract.get("cost_controls")
    raw = None
    if isinstance(policy, dict):
        raw = policy.get("max_agent_attempts")
    if raw is None and isinstance(controls, dict):
        raw = controls.get("max_agent_attempts")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, 5))
