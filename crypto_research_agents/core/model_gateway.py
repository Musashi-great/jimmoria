from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .llm_provider import LLMProvider, LLMRequest, LLMResponse, parse_json_response, provider_from_env


@dataclass(slots=True)
class ModelDecision:
    selected_model: str
    reason: str
    max_tokens: int
    temperature: float


class ModelGateway:
    """Routes model requests by task type and calls the configured LLM provider."""

    def __init__(
        self,
        default_model: str | None = None,
        provider: LLMProvider | None = None,
    ) -> None:
        default_model = default_model or _model_env("FAST") or "mvp_shared_llm"
        self.default_model = default_model
        self.provider = provider or provider_from_env()
        self.call_log: list[dict[str, Any]] = []

    def select(self, *, agent_id: str, task_type: str) -> ModelDecision:
        if task_type == "supervisor_chat":
            return ModelDecision(
                selected_model=_model_env("FAST")
                or _model_env("STRONG")
                or "fast_chat_model",
                reason=f"{agent_id} requested front-door conversation",
                max_tokens=1200,
                temperature=0.45,
            )
        if task_type in {"report_writing", "final_synthesis"}:
            return ModelDecision(
                selected_model=_model_env("WRITING")
                or _model_env("STRONG")
                or "strong_writing_model",
                reason=f"{agent_id} requested synthesis/report work",
                max_tokens=6000,
                temperature=0.2,
            )
        if task_type in {
            "narrative_reasoning",
            "supervision",
            "candidate_discovery",
            "social_summary",
            "contract_info",
            "product_docs",
            "funding_token",
        }:
            return ModelDecision(
                selected_model=_model_env("REASONING")
                or _model_env("STRONG")
                or "strong_reasoning_model",
                reason=f"{agent_id} requested reasoning work",
                max_tokens=5000,
                temperature=0.2,
            )
        if task_type == "embedding_search":
            return ModelDecision(
                selected_model="embedding_model",
                reason="Vector lookup requested",
                max_tokens=0,
                temperature=0.0,
            )
        return ModelDecision(
            selected_model=self.default_model,
            reason=f"{agent_id} requested routine task: {task_type}",
            max_tokens=3000,
            temperature=0.1,
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
        )
        response = self.provider.complete(request)
        self.call_log.append(
            {
                "agent_id": agent_id,
                "task_type": task_type,
                "selected_model": decision.selected_model,
                "provider": response.provider,
                "usage": response.usage,
            }
        )
        return response

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
    return (
        os.getenv(f"CODEX_CLI_MODEL_{tier}")
        or os.getenv(f"CODEX_OAUTH_MODEL_{tier}")
        or os.getenv(f"OPENAI_MODEL_{tier}")
    )
