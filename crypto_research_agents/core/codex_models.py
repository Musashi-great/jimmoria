from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CodexModel:
    model_id: str
    label: str
    role: str


SUPPORTED_CODEX_MODELS = [
    CodexModel("gpt-5.5", "GPT-5.5", "strong default for research, reasoning, writing, and complex agent work"),
    CodexModel("gpt-5.4", "GPT-5.4", "frontier professional work and agentic workflows"),
    CodexModel("gpt-5.4-mini", "GPT-5.4 mini", "fast subagent and routine extraction work"),
    CodexModel("gpt-5.3-codex", "GPT-5.3 Codex", "coding-specialized fallback"),
    CodexModel("gpt-5.3-codex-spark", "GPT-5.3 Codex Spark", "near-instant Pro preview path"),
]

SUPPORTED_CODEX_MODEL_IDS = tuple(model.model_id for model in SUPPORTED_CODEX_MODELS)

CODEX_DEFAULT_MODEL_BY_TIER = {
    "FAST_CHAT": "gpt-5.4-mini",
    "FAST": "gpt-5.4-mini",
    "REASONING": "gpt-5.5",
    "WRITING": "gpt-5.5",
    "STRONG": "gpt-5.5",
}


def codex_model_for_tier(tier: str) -> str:
    return CODEX_DEFAULT_MODEL_BY_TIER.get(tier, CODEX_DEFAULT_MODEL_BY_TIER["STRONG"])


def normalize_codex_model(model: str | None, *, tier: str) -> str:
    if model and model in SUPPORTED_CODEX_MODEL_IDS:
        return model
    return codex_model_for_tier(tier)


def codex_model_from_env_value(model: str | None) -> str | None:
    if model and model in SUPPORTED_CODEX_MODEL_IDS:
        return model
    return None


def supported_codex_model_lines() -> list[str]:
    return [f"{index}. {model.model_id} - {model.role}" for index, model in enumerate(SUPPORTED_CODEX_MODELS, start=1)]


def model_by_index(index: str) -> str | None:
    try:
        value = int(index)
    except ValueError:
        return None
    if value < 1 or value > len(SUPPORTED_CODEX_MODELS):
        return None
    return SUPPORTED_CODEX_MODELS[value - 1].model_id
