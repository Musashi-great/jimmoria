from __future__ import annotations

from typing import Any

from jimmoria.core.workflow import LoopCounter


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
        case "suggested_action_in":
            values = {str(item) for item in condition_config.get("values", [])}
            if not values:
                return False
            return bool(values.intersection(_suggested_actions(context)))
        case "identity_not_excluded":
            return _identity_status(context) not in {
                "exclude",
                "excluded",
                "fake",
                "impersonation",
                "red_flag",
                "kill_switch",
            }
        case "thesis_card_ready":
            return bool(context.get("thesis_card") or context.get("thesis_cards"))
        case "board_row_has_next_check":
            return _has_next_check_date(context)
        case "missing_source_backing":
            return _missing_source_backing(context)
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


def _suggested_actions(context: dict[str, Any]) -> set[str]:
    actions: set[str] = set()
    raw = context.get("suggested_action")
    if raw:
        actions.add(str(raw))
    for key in ("signals", "signal"):
        value = context.get(key)
        for item in _as_list(value):
            if isinstance(item, dict) and item.get("suggested_action"):
                actions.add(str(item["suggested_action"]))
    return actions


def _identity_status(context: dict[str, Any]) -> str:
    for key in ("identity_status", "identity"):
        value = context.get(key)
        if isinstance(value, dict):
            status = value.get("identity_status") or value.get("status")
            if status:
                return str(status).lower()
        if value:
            return str(value).lower()
    return "unknown"


def _has_next_check_date(context: dict[str, Any]) -> bool:
    for key in ("thesis_card", "radar_board"):
        value = context.get(key)
        for item in _as_list(value):
            if isinstance(item, dict) and item.get("next_check_date"):
                return True
    for item in _as_list(context.get("thesis_cards")):
        if isinstance(item, dict) and item.get("next_check_date"):
            return True
    return False


def _missing_source_backing(context: dict[str, Any]) -> bool:
    if bool(context.get("missing_source_backing")):
        return True
    for issue in _as_list(context.get("quality_issues")):
        if "source" in str(issue).lower() or "citation" in str(issue).lower():
            return True
    for key in ("thesis_card", "thesis_cards", "signals", "findings"):
        value = context.get(key)
        for item in _as_list(value):
            if isinstance(item, dict) and "source_ids" in item and not item.get("source_ids"):
                return True
    return False


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]
