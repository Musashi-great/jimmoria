from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from .claude_models import claude_model_for_tier, claude_model_from_env_value
from .codex_models import codex_model_for_tier, codex_model_from_env_value
from .grok_models import grok_model_for_tier, grok_model_from_env_value
from .llm_provider import (
    ClaudeApiProvider,
    ClaudeCliProvider,
    CodexApiProvider,
    CodexCliProvider,
    CodexSdkProvider,
    GrokProvider,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    codex_sdk_available,
    parse_json_response,
    provider_from_env,
)
from .usage import enrich_usage_with_estimates, token_usage_summary


@dataclass(slots=True)
class ModelDecision:
    selected_model: str
    reason: str
    max_tokens: int
    temperature: float
    reasoning_effort: str = "standard"
    provider_family: str = "codex"


class ModelGateway:
    """Routes model requests by task type and calls the configured LLM provider."""

    def __init__(
        self,
        default_model: str | None = None,
        provider: LLMProvider | None = None,
        providers: dict[str, LLMProvider] | None = None,
    ) -> None:
        configured_provider = provider or provider_from_env()
        self.provider_mode = _provider_mode(configured_provider)
        if providers is not None:
            self.providers = dict(providers)
            self.provider_mode = "codex_grok"
        elif self.provider_mode == "codex_grok":
            self.providers = _hybrid_providers_from_env()
        else:
            self.providers = {}
        self.provider = self.providers.get("codex") or next(iter(self.providers.values()), configured_provider)
        self.provider_name = "codex_grok" if self.provider_mode == "codex_grok" else getattr(self.provider, "provider_name", "unknown")
        self.provider_family = _default_provider_family(self.providers) if self.provider_mode == "codex_grok" else _provider_family(self.provider)
        default_model = default_model or _model_env("FAST", provider_family=self.provider_family) or _default_model_for_tier(
            "FAST",
            provider_family=self.provider_family,
        )
        self.default_model = default_model
        self.call_log: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    def select(self, *, agent_id: str, task_type: str) -> ModelDecision:
        provider_family = self.provider_family_for_task(agent_id=agent_id, task_type=task_type)
        return self._select_for_family(agent_id=agent_id, task_type=task_type, provider_family=provider_family)

    def _select_for_family(self, *, agent_id: str, task_type: str, provider_family: str) -> ModelDecision:
        if task_type == "supervisor_chat":
            return ModelDecision(
                selected_model=_model_env("FAST_CHAT", provider_family=provider_family)
                or _model_env("FAST", provider_family=provider_family)
                or _model_env("STRONG", provider_family=provider_family)
                or _default_model_for_tier("FAST_CHAT", provider_family=provider_family),
                reason=f"{agent_id} requested front-door conversation",
                max_tokens=1200,
                temperature=0.45,
                reasoning_effort=_reasoning_effort(default="standard"),
                provider_family=provider_family,
            )
        if task_type in {"report_writing", "final_synthesis"}:
            return ModelDecision(
                selected_model=_model_env("WRITING", provider_family=provider_family)
                or _model_env("STRONG", provider_family=provider_family)
                or _default_model_for_tier("WRITING", provider_family=provider_family),
                reason=f"{agent_id} requested synthesis/report work",
                max_tokens=9000,
                temperature=0.2,
                reasoning_effort=_reasoning_effort(default="pro"),
                provider_family=provider_family,
            )
        if task_type in {
            "source_ingestion",
            "narrative_reasoning",
            "supervision",
            "candidate_discovery",
            "signal_triage",
            "social_summary",
            "contract_info",
            "identity_verification",
            "product_docs",
            "funding_token",
            "thesis_card",
            "outcome_labeling",
            "obsidian_sync",
        }:
            return ModelDecision(
                selected_model=_model_env("REASONING", provider_family=provider_family)
                or _model_env("STRONG", provider_family=provider_family)
                or _default_model_for_tier("REASONING", provider_family=provider_family),
                reason=f"{agent_id} requested reasoning work",
                max_tokens=8000,
                temperature=0.2,
                reasoning_effort=_reasoning_effort(default="pro"),
                provider_family=provider_family,
            )
        if task_type == "embedding_search":
            return ModelDecision(
                selected_model="embedding_model",
                reason="Vector lookup requested",
                max_tokens=0,
                temperature=0.0,
                reasoning_effort="standard",
                provider_family=provider_family,
            )
        return ModelDecision(
            selected_model=self.default_model,
            reason=f"{agent_id} requested routine task: {task_type}",
            max_tokens=3000,
            temperature=0.1,
            reasoning_effort=_reasoning_effort(default="standard"),
            provider_family=provider_family,
        )

    def provider_family_for_task(self, *, agent_id: str, task_type: str) -> str:
        if self.provider_mode != "codex_grok":
            return self.provider_family
        override = _agent_provider_override(agent_id)
        if override and override in self.providers:
            return override
        agent_family = _hybrid_agent_provider_families(set(self.providers)).get(agent_id)
        if agent_family:
            return agent_family
        task_family = _hybrid_task_provider_families(set(self.providers)).get(task_type)
        if task_family:
            return task_family
        return _default_provider_family(self.providers)

    def provider_for_task(self, *, agent_id: str, task_type: str) -> LLMProvider:
        family = self.provider_family_for_task(agent_id=agent_id, task_type=task_type)
        return self.providers.get(family) or self.provider

    def provider_name_for_task(self, *, agent_id: str, task_type: str) -> str:
        provider = self.provider_for_task(agent_id=agent_id, task_type=task_type)
        return getattr(provider, "provider_name", self.provider_name)

    def complete(
        self,
        *,
        agent_id: str,
        task_type: str,
        system_prompt: str,
        user_prompt: str,
        response_format: str = "text",
    ) -> LLMResponse:
        decision = self.select(agent_id=agent_id, task_type=task_type)
        request = _request_from_decision(
            decision,
            agent_id=agent_id,
            task_type=task_type,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_format=response_format,
        )
        provider = self.providers.get(decision.provider_family) or self.provider
        requested_decision = decision
        fallback_error: str | None = None
        fallback_provider_name: str | None = None
        started = perf_counter()
        try:
            response = provider.complete(request)
        except Exception as exc:
            if not self._can_fallback_to_codex(decision.provider_family):
                raise
            fallback_error = str(exc)
            fallback_provider_name = getattr(provider, "provider_name", decision.provider_family)
            decision = self._select_for_family(agent_id=agent_id, task_type=task_type, provider_family="codex")
            request = _request_from_decision(
                decision,
                agent_id=agent_id,
                task_type=task_type,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_format=response_format,
            )
            provider = self.providers.get("codex") or self.provider
            try:
                response = provider.complete(request)
            except Exception as fallback_exc:
                raise RuntimeError(
                    "Grok route failed and Codex fallback also failed: "
                    f"grok_error={fallback_error}; codex_error={fallback_exc}"
                ) from fallback_exc
        duration_ms = int((perf_counter() - started) * 1000)
        if fallback_error:
            response.usage = {
                **response.usage,
                "fallback_from_provider": fallback_provider_name or "grok",
                "fallback_from_provider_family": requested_decision.provider_family,
                "fallback_from_model": requested_decision.selected_model,
                "fallback_error": fallback_error,
            }
        response.usage = enrich_usage_with_estimates(
            response.usage,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_text=response.text,
            duration_ms=duration_ms,
        )
        token_usage = token_usage_summary(response.usage)
        with self._lock:
            self.call_log.append(
                {
                    "agent_id": agent_id,
                    "task_type": task_type,
                    "selected_model": decision.selected_model,
                    "reasoning_effort": decision.reasoning_effort,
                    "provider": response.provider,
                    "provider_family": decision.provider_family,
                    "requested_provider_family": requested_decision.provider_family,
                    "requested_model": requested_decision.selected_model,
                    "fallback_from_provider": fallback_provider_name,
                    "fallback_error": fallback_error,
                    "duration_ms": response.usage.get("duration_ms", duration_ms),
                    "token_usage": token_usage,
                    "usage": response.usage,
                }
            )
        return response

    def calls_after(self, start_index: int, *, agent_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self.call_log[start_index:])
        if agent_id is not None:
            items = [item for item in items if item.get("agent_id") == agent_id]
        return items

    def complete_json(
        self,
        *,
        agent_id: str,
        task_type: str,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        response = self.complete(
            agent_id=agent_id,
            task_type=task_type,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_format="json",
        )
        return parse_json_response(response)

    def _can_fallback_to_codex(self, provider_family: str) -> bool:
        return (
            self.provider_mode == "codex_grok"
            and provider_family == "grok"
            and "codex" in self.providers
            and _grok_fallback_to_codex_enabled()
        )


def _request_from_decision(
    decision: ModelDecision,
    *,
    agent_id: str,
    task_type: str,
    system_prompt: str,
    user_prompt: str,
    response_format: str,
) -> LLMRequest:
    return LLMRequest(
        agent_id=agent_id,
        task_type=task_type,
        model=decision.selected_model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=decision.max_tokens,
        temperature=decision.temperature,
        response_format=response_format,
        reasoning_effort=decision.reasoning_effort,
    )


def _grok_fallback_to_codex_enabled() -> bool:
    raw = (
        os.getenv("JIMMORIA_GROK_FALLBACK_TO_CODEX")
        or os.getenv("JIMMORIA_HYBRID_GROK_FALLBACK")
        or "1"
    )
    if os.getenv("JIMMORIA_DISABLE_GROK_FALLBACK"):
        return False
    return raw.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _model_env(tier: str, *, provider_family: str = "codex") -> str | None:
    if provider_family == "grok":
        return grok_model_from_env_value(
            os.getenv(f"GROK_MODEL_{tier}")
            or os.getenv(f"XAI_MODEL_{tier}")
            or os.getenv("GROK_MODEL")
            or os.getenv("XAI_MODEL"),
        )
    if provider_family == "claude":
        return claude_model_from_env_value(
            os.getenv(f"CLAUDE_MODEL_{tier}")
            or os.getenv(f"ANTHROPIC_MODEL_{tier}")
            or os.getenv("CLAUDE_MODEL")
            or os.getenv("ANTHROPIC_MODEL"),
        )
    return codex_model_from_env_value(
        os.getenv(f"CODEX_MODEL_{tier}") or os.getenv(f"CODEX_CLI_MODEL_{tier}"),
    )


def _provider_family(provider: LLMProvider) -> str:
    provider_name = getattr(provider, "provider_name", "").lower()
    if provider_name in {"grok", "xai", "grok_oauth", "xai_oauth"}:
        return "grok"
    if provider_name in {"claude", "claude_api", "claude_cli", "anthropic"}:
        return "claude"
    configured = os.getenv("LLM_PROVIDER", "").strip().lower()
    if configured in {"grok", "xai", "grok_oauth", "xai_oauth"}:
        return "grok"
    if configured in {"claude", "claude_api", "claude_cli", "claude_oauth", "anthropic", "anthropic_api"}:
        return "claude"
    return "codex"


def _provider_mode(provider: LLMProvider) -> str:
    provider_name = getattr(provider, "provider_name", "").strip().lower()
    configured = os.getenv("LLM_PROVIDER", "").strip().lower()
    if provider_name in _HYBRID_PROVIDER_NAMES or configured in _HYBRID_PROVIDER_NAMES:
        return "codex_grok"
    return "single"


_HYBRID_PROVIDER_NAMES = {
    "codex_grok",
    "grok_codex",
    "codex+grok",
    "grok+codex",
    "dual",
    "hybrid",
    "multi",
}


def _hybrid_providers_from_env() -> dict[str, LLMProvider]:
    grok_auth = os.getenv("JIMMORIA_GROK_AUTH_PROVIDER", "xai_oauth").strip().lower().replace("-", "_")
    prefer_hermes_oauth = grok_auth in {"", "xai_oauth", "grok_oauth", "hermes", "oauth", "hermes_xai_oauth"}
    families = _configured_model_families()
    providers: dict[str, LLMProvider] = {}
    if "codex" in families:
        providers["codex"] = _codex_provider_from_env()
    if "grok" in families:
        providers["grok"] = GrokProvider(prefer_hermes_oauth=prefer_hermes_oauth)
    if "claude" in families:
        providers["claude"] = _claude_provider_from_env()
    if providers:
        return providers
    return {"codex": _codex_provider_from_env(), "grok": GrokProvider(prefer_hermes_oauth=prefer_hermes_oauth)}


def _configured_model_families() -> set[str]:
    raw = os.getenv("JIMMORIA_MODEL_FAMILIES", "").strip()
    families = {_normalize_family_name(item) for item in raw.split(",") if item.strip()}
    families.discard("")
    provider = os.getenv("LLM_PROVIDER", "").strip().lower().replace("-", "_")
    if provider in {"codex_grok", "grok_codex"}:
        families.update({"codex", "grok"})
    elif provider in {"multi", "hybrid", "dual", "codex+grok", "grok+codex"} and not families:
        families.update({"codex", "grok"})
    return {family for family in families if family in {"codex", "grok", "claude"}}


def _normalize_family_name(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized in {"codex", "openai", "openai_codex", "codex_cli", "codex_sdk", "codex_api"}:
        return "codex"
    if normalized in {"grok", "xai", "grok_oauth", "xai_oauth", "grok_api", "xai_api"}:
        return "grok"
    if normalized in {"claude", "anthropic", "claude_api", "anthropic_api", "claude_cli", "claude_oauth"}:
        return "claude"
    return ""


def _codex_provider_from_env() -> LLMProvider:
    preferred = (
        os.getenv("JIMMORIA_CODEX_PROVIDER")
        or os.getenv("CODEX_PROVIDER")
        or os.getenv("JIMMORIA_HYBRID_CODEX_PROVIDER")
        or ""
    ).strip().lower()
    if preferred in {"codex_cli", "cli", "exec"}:
        return CodexCliProvider()
    if preferred in {"codex_api", "openai_api", "api"}:
        return CodexApiProvider()
    if preferred in {"codex_sdk", "sdk"}:
        return CodexSdkProvider()
    return CodexSdkProvider() if codex_sdk_available() else CodexCliProvider()


def _claude_provider_from_env() -> LLMProvider:
    preferred = (os.getenv("JIMMORIA_CLAUDE_AUTH_PROVIDER") or "").strip().lower().replace("-", "_")
    if preferred in {"cli", "oauth", "claude_cli", "claude_oauth"}:
        return ClaudeCliProvider()
    return ClaudeApiProvider()


def _grok_task_types() -> set[str]:
    raw = os.getenv("JIMMORIA_GROK_TASKS", "").strip()
    if raw:
        return {item.strip() for item in raw.split(",") if item.strip()}
    return {
        "candidate_discovery",
        "narrative_reasoning",
        "social_summary",
    }


def _grok_agent_ids() -> set[str]:
    raw = os.getenv("JIMMORIA_GROK_AGENTS", "").strip()
    if raw:
        return {item.strip() for item in raw.split(",") if item.strip()}
    return {
        "social_kol_agent",
    }


def _hybrid_agent_provider_families(available: set[str]) -> dict[str, str]:
    """Default role-based provider split for the research company."""

    choose = _family_chooser(available)
    return {
        "supervisor_agent": choose("codex", "claude", "grok"),
        "ingestion_agent": choose("codex", "claude", "grok"),
        "monitor_24h_agent": choose("grok", "codex", "claude"),
        "signal_triage_agent": choose("grok", "codex", "claude"),
        "memory_retrieval_agent": choose("codex", "claude", "grok"),
        "narrative_agent": choose("grok", "claude", "codex"),
        "discovery_agent": choose("grok", "claude", "codex"),
        "social_kol_agent": choose("grok", "claude", "codex"),
        "contract_onchain_agent": choose("codex", "claude", "grok"),
        "product_tech_agent": choose("claude", "codex", "grok"),
        "funding_token_agent": choose("codex", "claude", "grok"),
        "thesis_engine_agent": choose("codex", "claude", "grok"),
        "outcome_labeler_agent": choose("codex", "claude", "grok"),
        "report_agent": choose("claude", "codex", "grok"),
        "obsidian_curator_agent": choose("codex", "claude", "grok"),
    }


def _hybrid_task_provider_families(available: set[str]) -> dict[str, str]:
    choose = _family_chooser(available)
    return {
        "supervisor_chat": choose("codex", "claude", "grok"),
        "source_ingestion": choose("codex", "claude", "grok"),
        "narrative_reasoning": choose("grok", "claude", "codex"),
        "candidate_discovery": choose("grok", "claude", "codex"),
        "signal_triage": choose("grok", "codex", "claude"),
        "social_summary": choose("grok", "claude", "codex"),
        "contract_info": choose("codex", "claude", "grok"),
        "identity_verification": choose("codex", "claude", "grok"),
        "product_docs": choose("claude", "codex", "grok"),
        "funding_token": choose("codex", "claude", "grok"),
        "thesis_card": choose("codex", "claude", "grok"),
        "outcome_labeling": choose("codex", "claude", "grok"),
        "obsidian_sync": choose("codex", "claude", "grok"),
        "report_writing": choose("claude", "codex", "grok"),
        "final_synthesis": choose("claude", "codex", "grok"),
    }


def _family_chooser(available: set[str]):
    def choose(*candidates: str) -> str:
        for family in candidates:
            if family in available:
                return family
        return next(iter(sorted(available)), "codex")

    return choose


def _default_provider_family(providers: dict[str, LLMProvider]) -> str:
    for family in ("codex", "claude", "grok"):
        if family in providers:
            return family
    return "codex"


def _agent_provider_override(agent_id: str) -> str | None:
    normalized_agent = "".join(ch if ch.isalnum() else "_" for ch in agent_id).upper()
    raw = (
        os.getenv(f"JIMMORIA_AGENT_PROVIDER_{normalized_agent}")
        or os.getenv(f"JIMMORIA_PROVIDER_{normalized_agent}")
        or ""
    ).strip()
    return _normalize_provider_family(raw)


def _normalize_provider_family(raw: str) -> str | None:
    normalized = raw.strip().lower().replace("-", "_")
    if normalized in {"codex", "codex_cli", "codex_sdk", "codex_api", "openai_api", "openai_codex"}:
        return "codex"
    if normalized in {"grok", "xai", "xai_oauth", "grok_oauth", "xai_api", "grok_api"}:
        return "grok"
    if normalized in {"claude", "anthropic", "claude_api", "anthropic_api", "claude_cli", "claude_oauth"}:
        return "claude"
    return None


def _default_model_for_tier(tier: str, *, provider_family: str) -> str:
    if provider_family == "grok":
        return grok_model_for_tier(tier)
    if provider_family == "claude":
        return claude_model_for_tier(tier)
    return codex_model_for_tier(tier)


def _reasoning_effort(*, default: str) -> str:
    raw = (
        os.getenv("CODEX_REASONING_EFFORT")
        or os.getenv("CODEX_MODEL_REASONING_EFFORT")
        or os.getenv("CODEX_CLI_MODEL_REASONING_EFFORT")
        or default
    )
    normalized = raw.strip().lower().replace("-", "_")
    if normalized in {"pro", "xhigh", "extra_high", "max", "maximum"}:
        return "pro"
    if normalized in {"high", "deep"}:
        return "high"
    if normalized in {"fast", "low"}:
        return "fast"
    return "standard"
