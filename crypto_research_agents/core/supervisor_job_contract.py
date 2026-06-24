from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from crypto_research_agents.core.supervisor_intake import SupervisorIntakeDecision


@dataclass(slots=True)
class SupervisorJobContract:
    """Bounded loop contract chosen by Hermes Agent before work dispatch."""

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
    extension_policy: dict[str, Any] = field(default_factory=dict)
    context_policy: dict[str, Any] = field(default_factory=dict)
    memory_policy: dict[str, Any] = field(default_factory=dict)
    delegation_policy: dict[str, Any] = field(default_factory=dict)
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
            "Hermes Agent answered directly or persisted the requested setting/memory update.",
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
        extension_policy=_hermes_extension_policy(extension_rung="existing_config_or_skill"),
        context_policy=_hermes_context_policy(loop_shape="single_turn"),
        memory_policy=_hermes_memory_policy(),
        delegation_policy={
            "mode": "none",
            "subagents_start_fresh": False,
            "requires_explicit_handoff_context": False,
            "recursive_delegation_allowed": False,
        },
        notes=["Single-agent loop is used for Hermes Agent conversation and lightweight control-plane work."],
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
            "Hermes Agent owns the goal and delegates specialist evidence work.",
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
        extension_policy=_hermes_extension_policy(extension_rung="closed_fleet_contract"),
        context_policy=_hermes_context_policy(loop_shape="closed_fleet"),
        memory_policy=_hermes_memory_policy(),
        delegation_policy={
            "mode": "one_level_fanout",
            "subagents_start_fresh": True,
            "requires_explicit_handoff_context": True,
            "recursive_delegation_allowed": False,
            "shared_memory_write_policy": "restricted_to_runtime_and_agent_outputs",
            "required_handoff_fields": [
                "task_id",
                "objective",
                "expected_output",
                "source_requirements",
                "verification_gates",
                "completion_criteria",
            ],
        },
        notes=[
            "Open exploration is limited to candidate discovery; the room itself is a closed fleet loop.",
            "Hermes Agent owns the goal; specialists own evidence slices.",
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


def _hermes_extension_policy(*, extension_rung: str) -> dict[str, Any]:
    return {
        "architecture_rule": "narrow_waist",
        "core_boundary": "Keep the Hermes/Runtime loop stable; add capability through config, skills, toolsets, connectors, MCP, or job contracts first.",
        "selected_rung": extension_rung,
        "footprint_ladder": [
            "extend_existing_config_or_skill",
            "add_cli_command_plus_skill",
            "add_service_gated_tool_or_toolset",
            "add_mcp_or_connector_edge",
            "add_runtime_core_change_only_when_contract_requires_it",
        ],
    }


def _hermes_context_policy(*, loop_shape: str) -> dict[str, Any]:
    return {
        "loop_shape": loop_shape,
        "turn_phases": ["prologue_intake_and_contract", "bounded_work_loop", "epilogue_persist_memory_and_artifacts"],
        "progressive_disclosure": "Expose compact skill/tool indexes first; load detailed procedures only when the task needs them.",
        "prompt_cache_rule": "Treat prior context as append-only; use summaries or compression instead of rewriting earlier turns.",
        "raw_log_policy": "Store raw tool/agent logs as artifacts; keep the visible UI on concise current work.",
    }


def _hermes_memory_policy() -> dict[str, Any]:
    return {
        "durable_notes": "Keep Hermes memory small, high-signal, and preference-oriented.",
        "user_model": "Use Hermes session context for style, continuity, last room, and operating preferences.",
        "deep_recall": "Search saved runs/sessions on demand instead of stuffing old transcripts into every prompt.",
        "memory_write_rule": "Persist stable preferences and room pointers; do not dump raw logs into durable memory.",
    }
