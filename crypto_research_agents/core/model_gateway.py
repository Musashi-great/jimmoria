from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from .codex_models import codex_model_for_tier, codex_model_from_env_value
from .llm_provider import LLMProvider, LLMRequest, LLMResponse, parse_json_response, provider_from_env
from .usage import enrich_usage_with_estimates, token_usage_summary


@dataclass(slots=True)
class ModelDecision:
    selected_model: str
    reason: str
    max_tokens: int
    temperature: float
    reasoning_effort: str = "standard"


class ModelGateway:
    """Routes model requests by task type and calls the configured LLM provider."""

    def __init__(
        self,
        default_model: str | None = None,
        provider: LLMProvider | None = None,
    ) -> None:
        self.provider = provider or provider_from_env()
        default_model = default_model or _model_env("FAST") or codex_model_for_tier("FAST")
        self.default_model = default_model
        self.call_log: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    def select(self, *, agent_id: str, task_type: str) -> ModelDecision:
        if task_type == "supervisor_chat":
            return ModelDecision(
                selected_model=_model_env("FAST_CHAT")
                or _model_env("FAST")
                or _model_env("STRONG")
                or codex_model_for_tier("FAST_CHAT"),
                reason=f"{agent_id} requested front-door conversation",
                max_tokens=1200,
                temperature=0.45,
                reasoning_effort=_reasoning_effort(default="standard"),
            )
        if task_type in {"report_writing", "final_synthesis"}:
            return ModelDecision(
                selected_model=_model_env("WRITING")
                or _model_env("STRONG")
                or codex_model_for_tier("WRITING"),
                reason=f"{agent_id} requested synthesis/report work",
                max_tokens=9000,
                temperature=0.2,
                reasoning_effort=_reasoning_effort(default="pro"),
            )
        if task_type in {
            "source_ingestion",
            "narrative_reasoning",
            "supervision",
            "candidate_discovery",
            "social_summary",
            "contract_info",
            "product_docs",
            "funding_token",
            "obsidian_sync",
        }:
            return ModelDecision(
                selected_model=_model_env("REASONING")
                or _model_env("STRONG")
                or codex_model_for_tier("REASONING"),
                reason=f"{agent_id} requested reasoning work",
                max_tokens=8000,
                temperature=0.2,
                reasoning_effort=_reasoning_effort(default="pro"),
            )
        if task_type == "embedding_search":
            return ModelDecision(
                selected_model="embedding_model",
                reason="Vector lookup requested",
                max_tokens=0,
                temperature=0.0,
                reasoning_effort="standard",
            )
        return ModelDecision(
            selected_model=self.default_model,
            reason=f"{agent_id} requested routine task: {task_type}",
            max_tokens=3000,
            temperature=0.1,
            reasoning_effort=_reasoning_effort(default="standard"),
        )

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
        request = LLMRequest(
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
        started = perf_counter()
        response = self.provider.complete(request)
        duration_ms = int((perf_counter() - started) * 1000)
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


def _model_env(tier: str) -> str | None:
    return codex_model_from_env_value(
        os.getenv(f"CODEX_MODEL_{tier}") or os.getenv(f"CODEX_CLI_MODEL_{tier}"),
    )


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
