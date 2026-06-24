from __future__ import annotations

from typing import Any


def estimate_tokens(*texts: object) -> int:
    """Cheap token estimate used when a provider does not expose usage."""

    ascii_chars = 0
    non_ascii_chars = 0
    for text in texts:
        for char in str(text or ""):
            if char.isspace():
                continue
            if ord(char) < 128:
                ascii_chars += 1
            else:
                non_ascii_chars += 1
    return max(0, int(ascii_chars / 4) + int(non_ascii_chars * 0.9))


def enrich_usage_with_estimates(
    usage: dict[str, Any] | None,
    *,
    system_prompt: str,
    user_prompt: str,
    response_text: str,
    duration_ms: int,
) -> dict[str, Any]:
    enriched = dict(usage or {})
    enriched.setdefault("duration_ms", duration_ms)
    summary = token_usage_summary(enriched)
    if summary["total_tokens"] <= 0:
        input_tokens = estimate_tokens(system_prompt, user_prompt)
        output_tokens = estimate_tokens(response_text)
        summary = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "estimated": True,
        }
        enriched["estimated_token_usage"] = summary
    else:
        summary["estimated"] = False
    enriched["token_usage_summary"] = summary
    return enriched


def token_usage_summary(usage: Any) -> dict[str, Any]:
    data = _token_fields(usage)
    input_tokens = data.get("input_tokens", 0)
    output_tokens = data.get("output_tokens", 0)
    total_tokens = data.get("total_tokens", 0)
    if total_tokens <= 0 and (input_tokens or output_tokens):
        total_tokens = input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "estimated": bool(data.get("estimated", False)),
    }


def aggregate_llm_usage(entries: list[dict[str, Any]]) -> dict[str, Any]:
    calls = len(entries)
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    duration_ms = 0
    estimated = False
    providers: list[str] = []
    models: list[str] = []

    for entry in entries:
        usage = entry.get("usage") if isinstance(entry, dict) else {}
        summary = entry.get("token_usage") if isinstance(entry, dict) else {}
        if not isinstance(summary, dict) or not summary:
            summary = token_usage_summary(usage)
        input_tokens += int(summary.get("input_tokens") or 0)
        output_tokens += int(summary.get("output_tokens") or 0)
        total_tokens += int(summary.get("total_tokens") or 0)
        estimated = estimated or bool(summary.get("estimated"))
        duration_ms += int(entry.get("duration_ms") or _duration_from_usage(usage))
        provider = str(entry.get("provider") or "")
        model = str(entry.get("selected_model") or entry.get("model") or "")
        if provider and provider not in providers:
            providers.append(provider)
        if model and model not in models:
            models.append(model)

    return {
        "calls": calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "duration_ms": duration_ms,
        "estimated": estimated,
        "providers": providers,
        "models": models,
    }


def _duration_from_usage(usage: Any) -> int:
    if isinstance(usage, dict):
        try:
            return int(usage.get("duration_ms") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _token_fields(value: Any) -> dict[str, int | bool]:
    result: dict[str, int | bool] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated": False,
    }
    if not isinstance(value, dict):
        return result

    nested_candidates = [
        value.get("token_usage_summary"),
        value.get("estimated_token_usage"),
        value.get("token_usage"),
        value.get("usage"),
    ]
    for nested in nested_candidates:
        if isinstance(nested, dict):
            nested_result = _token_fields(nested)
            _merge_token_fields(result, nested_result)

    for key, target in [
        ("input_tokens", "input_tokens"),
        ("prompt_tokens", "input_tokens"),
        ("inputTokenCount", "input_tokens"),
        ("promptTokenCount", "input_tokens"),
        ("output_tokens", "output_tokens"),
        ("completion_tokens", "output_tokens"),
        ("outputTokenCount", "output_tokens"),
        ("completionTokenCount", "output_tokens"),
        ("total_tokens", "total_tokens"),
        ("totalTokens", "total_tokens"),
        ("totalTokenCount", "total_tokens"),
    ]:
        if key in value:
            try:
                result[target] = max(int(result[target]), int(value.get(key) or 0))
            except (TypeError, ValueError):
                continue
    if value.get("estimated"):
        result["estimated"] = True
    return result


def _merge_token_fields(target: dict[str, int | bool], source: dict[str, int | bool]) -> None:
    for key in ["input_tokens", "output_tokens", "total_tokens"]:
        target[key] = max(int(target[key]), int(source.get(key) or 0))
    target["estimated"] = bool(target.get("estimated")) or bool(source.get("estimated"))
