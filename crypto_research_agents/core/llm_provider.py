from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError
from urllib.request import Request as UrlRequest
from urllib.request import urlopen


@dataclass(slots=True)
class LLMRequest:
    agent_id: str
    task_type: str
    model: str
    system_prompt: str
    user_prompt: str
    max_tokens: int
    temperature: float
    response_format: str = "text"


@dataclass(slots=True)
class LLMResponse:
    text: str
    model: str
    provider: str
    usage: dict[str, Any]
    raw: Any = None


class LLMProvider(Protocol):
    provider_name: str

    def complete(self, request: LLMRequest) -> LLMResponse:
        ...


class OfflineLLMProvider:
    """Deterministic fallback used when no live LLM provider is configured."""

    provider_name = "offline_fallback"

    def complete(self, request: LLMRequest) -> LLMResponse:
        if request.response_format == "json":
            payload = {
                "summary": _fallback_summary(request.user_prompt),
                "entities": _fallback_entities(request.user_prompt),
                "keywords": _fallback_keywords(request.user_prompt),
                "narratives": _fallback_narratives(request.user_prompt),
                "notes": ["Offline fallback used; configure OPENAI_API_KEY for live LLM calls."],
            }
            text = json.dumps(payload, ensure_ascii=False)
        else:
            text = _fallback_summary(request.user_prompt)
        return LLMResponse(
            text=text,
            model=request.model,
            provider=self.provider_name,
            usage={"mode": "offline"},
        )


class OpenAIChatProvider:
    """OpenAI chat-completions provider loaded lazily to keep tests dependency-free."""

    provider_name = "openai"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not set.")

        try:
            from openai import OpenAI  # type: ignore
        except ImportError as exc:
            raise RuntimeError("Install the OpenAI Python package to use OpenAIChatProvider.") from exc

        self._client = OpenAI(api_key=self.api_key)

    def complete(self, request: LLMRequest) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "temperature": request.temperature,
        }
        if request.max_tokens > 0:
            kwargs["max_tokens"] = request.max_tokens
        if request.response_format == "json":
            kwargs["response_format"] = {"type": "json_object"}

        response = self._client.chat.completions.create(**kwargs)
        message = response.choices[0].message.content or ""
        usage = response.usage.model_dump() if getattr(response, "usage", None) else {}
        return LLMResponse(
            text=message,
            model=request.model,
            provider=self.provider_name,
            usage=usage,
            raw=response,
        )


class CodexCliProvider:
    """LLM provider that uses the local Codex CLI ChatGPT login session."""

    provider_name = "codex_cli"

    def __init__(self, *, command: str | None = None) -> None:
        self.command = command or os.getenv("CODEX_CLI_COMMAND", "codex")
        self.timeout = float(os.getenv("CODEX_CLI_TIMEOUT", "180"))
        self._exec_help_text: str | None = None

    def complete(self, request: LLMRequest) -> LLMResponse:
        prompt = _codex_cli_prompt(request)
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "last_message.txt"
            command = _build_codex_exec_command(
                executable=self.command,
                help_text=self.exec_help_text(),
                output_path=output_path,
                model=request.model,
            )

            completed = subprocess.run(
                command,
                input=prompt.encode("utf-8"),
                check=False,
                capture_output=True,
                text=False,
                timeout=self.timeout,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    "Codex CLI provider failed: "
                    + (
                        _decode_process_output(completed.stderr).strip()
                        or _decode_process_output(completed.stdout).strip()
                    )
                )
            text = (
                output_path.read_text(encoding="utf-8").strip()
                if output_path.exists()
                else _decode_process_output(completed.stdout).strip()
            )

        return LLMResponse(
            text=text,
            model=request.model,
            provider=self.provider_name,
            usage={"mode": "codex_cli"},
        )

    def exec_help_text(self) -> str:
        if self._exec_help_text is None:
            self._exec_help_text = _load_codex_exec_help(self.command)
        return self._exec_help_text


class OAuthTokenProvider:
    """Loads an OAuth bearer token from an explicit env, file, or command.

    This class intentionally does not auto-read Codex's internal auth files.
    The caller must explicitly provide a token source.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        token_env: str = "CODEX_OAUTH_TOKEN",
        token_file_env: str = "CODEX_OAUTH_TOKEN_FILE",
        token_command_env: str = "CODEX_OAUTH_TOKEN_COMMAND",
    ) -> None:
        self.token = token
        self.token_env = token_env
        self.token_file_env = token_file_env
        self.token_command_env = token_command_env

    def get_token(self) -> str:
        if self.token:
            return self.token.strip()

        env_token = os.getenv(self.token_env)
        if env_token:
            return env_token.strip()

        token_file = os.getenv(self.token_file_env)
        if token_file:
            from pathlib import Path

            return Path(token_file).read_text(encoding="utf-8").strip()

        token_command = os.getenv(self.token_command_env)
        if token_command:
            completed = subprocess.run(
                token_command,
                shell=True,
                check=True,
                capture_output=True,
                text=True,
            )
            return completed.stdout.strip()

        raise RuntimeError(
            "Codex OAuth token source is not configured. Set CODEX_OAUTH_TOKEN, "
            "CODEX_OAUTH_TOKEN_FILE, or CODEX_OAUTH_TOKEN_COMMAND."
        )


class CodexOAuthChatProvider:
    """OpenAI-compatible chat provider using an explicit OAuth bearer token."""

    provider_name = "codex_oauth"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token_provider: OAuthTokenProvider | None = None,
    ) -> None:
        self.base_url = (
            base_url
            or os.getenv("CODEX_OAUTH_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        ).rstrip("/")
        self.token_provider = token_provider or OAuthTokenProvider()
        self.timeout = float(os.getenv("CODEX_OAUTH_TIMEOUT", "60"))

    def complete(self, request: LLMRequest) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "temperature": request.temperature,
        }
        if request.max_tokens > 0:
            payload["max_tokens"] = request.max_tokens
        if request.response_format == "json":
            payload["response_format"] = {"type": "json_object"}

        body = json.dumps(payload).encode("utf-8")
        http_request = UrlRequest(
            f"{self.base_url}/chat/completions",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token_provider.get_token()}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

        try:
            with urlopen(http_request, timeout=self.timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Codex OAuth provider HTTP {exc.code}: {error_body}") from exc

        choices = response_data.get("choices") or []
        message = ""
        if choices:
            message = choices[0].get("message", {}).get("content") or ""
        return LLMResponse(
            text=message,
            model=request.model,
            provider=self.provider_name,
            usage=response_data.get("usage", {}),
            raw=response_data,
        )


def provider_from_env() -> LLMProvider:
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    if provider in {"offline", "fallback", "none"}:
        return OfflineLLMProvider()
    if provider in {"codex_cli", "codex_device", "codex_login"}:
        return CodexCliProvider()
    if provider in {"codex_oauth", "codex", "oauth"}:
        return CodexOAuthChatProvider()
    if (
        os.getenv("CODEX_OAUTH_TOKEN")
        or os.getenv("CODEX_OAUTH_TOKEN_FILE")
        or os.getenv("CODEX_OAUTH_TOKEN_COMMAND")
    ):
        try:
            return CodexOAuthChatProvider()
        except RuntimeError:
            return OfflineLLMProvider()
    if provider == "openai" or os.getenv("OPENAI_API_KEY"):
        try:
            return OpenAIChatProvider()
        except RuntimeError:
            return OfflineLLMProvider()
    return OfflineLLMProvider()


def _load_codex_exec_help(command: str) -> str:
    try:
        completed = subprocess.run(
            [command, "exec", "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return "\n".join(part for part in [completed.stdout, completed.stderr] if part)


def _decode_process_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _build_codex_exec_command(
    *,
    executable: str,
    help_text: str,
    output_path: Path,
    model: str,
) -> list[str]:
    command = [executable, "exec"]

    if _codex_exec_supports(help_text, "--ephemeral"):
        command.append("--ephemeral")
    if _codex_exec_supports(help_text, "--skip-git-repo-check"):
        command.append("--skip-git-repo-check")

    if _codex_exec_supports(help_text, "--ask-for-approval"):
        command.extend(["--ask-for-approval", "never"])
    elif _codex_exec_supports(help_text, "--approval-policy"):
        command.extend(["--approval-policy", "never"])

    if _codex_exec_supports(help_text, "--sandbox"):
        command.extend(["--sandbox", os.getenv("CODEX_CLI_SANDBOX", "read-only")])

    if _codex_exec_supports(help_text, "--output-last-message"):
        command.extend(["--output-last-message", str(output_path)])

    if _is_real_model_name(model) and _codex_exec_supports(help_text, "--model"):
        command.extend(["--model", model])

    command.append("-")
    return command


def _codex_exec_supports(help_text: str, option: str) -> bool:
    return option in help_text


def _codex_cli_prompt(request: LLMRequest) -> str:
    response_rule = ""
    if request.response_format == "json":
        response_rule = "\nReturn only a valid JSON object. Do not include markdown fences."
    return (
        "You are serving as an LLM provider inside JIMMORIA, a crypto research-only "
        "multi-agent runtime. Follow the system prompt and user prompt. Do not browse "
        "or run tools unless explicitly necessary; answer from the provided context."
        f"{response_rule}\n\n"
        f"System prompt:\n{request.system_prompt}\n\n"
        f"User prompt:\n{request.user_prompt}\n"
    )


def _is_real_model_name(model: str) -> bool:
    placeholders = {
        "mvp_shared_llm",
        "fast_model",
        "medium_model",
        "cheap_model",
        "fast_reasoning",
        "strong_reasoning_model",
        "strong_writing_model",
        "embedding_model",
    }
    return bool(model and model not in placeholders)


def parse_json_response(response: LLMResponse) -> dict[str, Any]:
    try:
        value = json.loads(response.text)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _fallback_summary(text: str, limit: int = 260) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _fallback_entities(text: str) -> list[str]:
    import re

    candidates = re.findall(r"\b[A-Z][A-Za-z0-9]{2,}(?:\s+[A-Z][A-Za-z0-9]{2,})?\b", text)
    seen: set[str] = set()
    result: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            result.append(candidate)
            seen.add(candidate)
    return result[:20]


def _fallback_keywords(text: str) -> list[str]:
    taxonomy = [
        "agent",
        "wallet",
        "automation",
        "intent",
        "defi",
        "consumer",
        "depin",
        "rwa",
        "restaking",
        "airdrop",
        "points",
        "testnet",
        "github",
        "docs",
    ]
    lowered = text.lower()
    return [keyword for keyword in taxonomy if keyword in lowered]


def _fallback_narratives(text: str) -> list[str]:
    lowered = text.lower()
    rules = {
        "AI x Wallet Automation": {"ai", "agent", "wallet", "automation", "intent"},
        "Consumer Crypto": {"consumer", "wallet", "social", "mobile"},
        "DeFi Automation": {"defi", "intent", "automation", "trading"},
        "Pre-token / Airdrop": {"airdrop", "points", "testnet", "pre-token"},
        "Developer Infra": {"github", "docs", "sdk", "api"},
        "DePIN": {"depin", "sensor", "device", "network"},
        "RWA": {"rwa", "asset", "treasury", "tokenized"},
        "Restaking": {"restaking", "avs", "eigen"},
    }
    selected = [name for name, keywords in rules.items() if any(keyword in lowered for keyword in keywords)]
    return selected or ["Unclassified Early Crypto"]
