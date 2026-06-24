from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GrokModel:
    model_id: str
    label: str
    role: str


SUPPORTED_GROK_MODELS = [
    GrokModel("grok-4.3", "Grok 4.3", "default xAI research, synthesis, and agent reasoning model"),
    GrokModel("grok-4.3-latest", "Grok 4.3 latest", "auto-updating Grok 4.3 alias"),
    GrokModel("grok-latest", "Grok latest", "stable latest chat/reasoning alias"),
    GrokModel("grok-4.20", "Grok 4.20", "high-performance reasoning-capable model alias"),
    GrokModel("grok-4.20-reasoning", "Grok 4.20 reasoning", "automatic reasoning path"),
    GrokModel("grok-4.20-multi-agent", "Grok 4.20 multi-agent", "xAI multi-agent deep research path"),
]

SUPPORTED_GROK_MODEL_IDS = tuple(model.model_id for model in SUPPORTED_GROK_MODELS)

GROK_DEFAULT_BY_TIER = {
    "FAST_CHAT": "grok-4.3",
    "FAST": "grok-4.3",
    "REASONING": "grok-4.3",
    "WRITING": "grok-4.3",
    "STRONG": "grok-4.3",
}


def grok_model_for_tier(tier: str) -> str:
    return GROK_DEFAULT_BY_TIER.get(tier.upper(), GROK_DEFAULT_BY_TIER["FAST"])


def grok_model_from_env_value(model: str | None) -> str | None:
    model = (model or "").strip()
    if not model:
        return None
    if model in SUPPORTED_GROK_MODEL_IDS:
        return model
    if model.startswith("grok-"):
        return model
    return None


def grok_model_by_index(value: str) -> str | None:
    value = value.strip()
    if not value.isdigit():
        return None
    index = int(value)
    if index < 1 or index > len(SUPPORTED_GROK_MODELS):
        return None
    return SUPPORTED_GROK_MODELS[index - 1].model_id


def supported_grok_model_lines() -> list[str]:
    return [f"{index}. {model.model_id} - {model.role}" for index, model in enumerate(SUPPORTED_GROK_MODELS, start=1)]
