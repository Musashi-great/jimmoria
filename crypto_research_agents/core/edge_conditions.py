from __future__ import annotations

from typing import Any

from crypto_research_agents.core.workflow import LoopCounter


def evaluate_edge_condition(
    condition: bool | str | dict[str, Any],
    context: dict[str, Any],
    *,
    loop_counters: dict[str, LoopCounter] | None = None,
) -> bool:
    if isinstance(condition, bool):
        return condition

    if isinstance(condition, str):
        condition_type = condition
        condition_config: dict[str, Any] = {}
    elif isinstance(condition, dict):
        condition_type = str(condition.get("type", "true"))
        condition_config = condition
    else:
        return False

    match condition_type:
        case "true":
            return True
        case "has_candidates":
            return bool(context.get("candidates"))
        case "has_sources":
            return bool(context.get("sources"))
        case "has_findings":
            return bool(context.get("findings"))
        case "has_risk_flags":
            return bool(context.get("risk_flags"))
        case "no_kill_switch":
            return not bool(context.get("kill_switch"))
        case "has_kill_switch":
            return bool(context.get("kill_switch"))
        case "quality_passed":
            return quality_passed(context)
        case "quality_failed":
            return not quality_passed(context)
        case "max_loop_not_exceeded":
            counter_id = str(condition_config.get("loop_id") or condition_config.get("counter_id") or "")
            if not counter_id or loop_counters is None or counter_id not in loop_counters:
                return True
            return loop_counters[counter_id].can_continue()
        case _:
            return False


def quality_passed(context: dict[str, Any]) -> bool:
    quality = context.get("quality")
    if isinstance(quality, dict):
        if "passed" in quality:
            return bool(quality.get("passed"))
        status = str(quality.get("status", ""))
        if status:
            return status in {"passed", "research_complete", "quality_passed"}
    return bool(context.get("quality_passed"))
