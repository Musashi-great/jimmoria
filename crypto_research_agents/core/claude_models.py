from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ClaudeModel:
    model_id: str
    label: str
    role: str


SUPPORTED_CLAUDE_MODELS = [
    ClaudeModel("claude-sonnet-4-5", "Claude Sonnet 4.5", "default Anthropic reasoning, product review, and writing model"),
    ClaudeModel("claude-opus-4-1", "Claude Opus 4.1", "strong Anthropic reasoning and synthesis model"),
    ClaudeModel("claude-haiku-4-5", "Claude Haiku 4.5", "fast Anthropic extraction and routine agent work"),
]

SUPPORTED_CLAUDE_MODEL_IDS = tuple(model.model_id for model in SUPPORTED_CLAUDE_MODELS)

CLAUDE_DEFAULT_BY_TIER = {
    "FAST_CHAT": "claude-haiku-4-5",
    "FAST": "claude-haiku-4-5",
    "REASONING": "claude-sonnet-4-5",
    "WRITING": "claude-sonnet-4-5",
    "STRONG": "claude-opus-4-1",
}


def claude_model_for_tier(tier: str) -> str:
    return CLAUDE_DEFAULT_BY_TIER.get(tier.upper(), CLAUDE_DEFAULT_BY_TIER["REASONING"])


def claude_model_from_env_value(model: str | None) -> str | None:
    model = (model or "").strip()
    if not model:
        return None
    if model in SUPPORTED_CLAUDE_MODEL_IDS:
        return model
    if model.startswith("claude-"):
        return model
    return None


def claude_model_by_index(value: str) -> str | None:
    value = value.strip()
    if not value.isdigit():
        return None
    index = int(value)
    if index < 1 or index > len(SUPPORTED_CLAUDE_MODELS):
        return None
    return SUPPORTED_CLAUDE_MODELS[index - 1].model_id


def supported_claude_model_lines() -> list[str]:
    return [f"{index}. {model.model_id} - {model.role}" for index, model in enumerate(SUPPORTED_CLAUDE_MODELS, start=1)]
