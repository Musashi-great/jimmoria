from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from crypto_research_agents.storage.paths import project_root


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
                "notes": ["Offline fallback used; configure Codex SDK or Codex CLI for live LLM calls."],
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


class CodexSdkProvider:
    """LLM provider backed by the local Codex SDK app-server."""

    provider_name = "codex_sdk"

    def __init__(self) -> None:
        self.sandbox_name = os.getenv("CODEX_SDK_SANDBOX", "read_only")
        self.approval_mode_name = os.getenv("CODEX_SDK_APPROVAL_MODE", "deny_all")
        self.cwd = os.getenv("CODEX_SDK_CWD") or str(project_root())

    def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox  # type: ignore
        except ImportError as exc:
            raise RuntimeError("Install the Codex Python SDK with `pip install openai-codex`.") from exc

        sandbox = _codex_sdk_sandbox(Sandbox, self.sandbox_name)
        approval_mode = _codex_sdk_approval_mode(ApprovalMode, self.approval_mode_name)
        prompt = _codex_sdk_prompt(request)
        config = CodexConfig(
            cwd=self.cwd,
            client_name="jimmoria",
            client_title="JIMMORIA",
        )
        with Codex(config) as codex:
            thread = codex.thread_start(
                approval_mode=approval_mode,
                cwd=self.cwd,
                developer_instructions=request.system_prompt,
                ephemeral=True,
                model=request.model,
                sandbox=sandbox,
                service_name=f"jimmoria:{request.agent_id}:{request.task_type}",
            )
            result = thread.run(prompt, approval_mode=approval_mode, cwd=self.cwd, sandbox=sandbox)
            text = str(getattr(result, "final_response", "") or "")

        return LLMResponse(
            text=text.strip(),
            model=request.model,
            provider=self.provider_name,
            usage=_codex_sdk_usage(
                result,
                sandbox=self.sandbox_name,
                approval_mode=self.approval_mode_name,
                cwd=self.cwd,
            ),
            raw=result,
        )


def provider_from_env() -> LLMProvider:
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    if provider in {"offline", "fallback", "none"}:
        return OfflineLLMProvider()
    if provider in {"codex_cli", "codex_device", "codex_login"}:
        return CodexCliProvider()
    if provider in {"codex_sdk", "codex", "sdk"}:
        return CodexSdkProvider()
    return OfflineLLMProvider()


def codex_sdk_available() -> bool:
    try:
        import openai_codex  # type: ignore  # noqa: F401
    except ImportError:
        return False
    return True


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


def _codex_sdk_prompt(request: LLMRequest) -> str:
    response_rule = ""
    if request.response_format == "json":
        response_rule = "\nReturn only a valid JSON object. Do not include markdown fences."
    return (
        "You are serving as a Codex SDK worker inside JIMMORIA, a crypto research-only "
        "multi-agent company. You are not here to modify the repository for this task. "
        "Answer from the provided context and keep outputs suitable for the requesting agent."
        f"{response_rule}\n\n"
        f"System prompt:\n{request.system_prompt}\n\n"
        f"User prompt:\n{request.user_prompt}\n"
    )


def _codex_sdk_sandbox(sandbox_module: Any, sandbox_name: str) -> Any:
    normalized = sandbox_name.lower().replace("-", "_")
    if normalized == "workspace_write":
        return sandbox_module.workspace_write
    if normalized == "full_access":
        return sandbox_module.full_access
    return sandbox_module.read_only


def _codex_sdk_approval_mode(approval_module: Any, approval_mode: str) -> Any:
    normalized = approval_mode.lower().replace("-", "_")
    if normalized in {"auto", "auto_review", "on_request"}:
        return approval_module.auto_review
    return approval_module.deny_all


def _codex_sdk_usage(result: Any, *, sandbox: str, approval_mode: str, cwd: str) -> dict[str, Any]:
    usage: dict[str, Any] = {
        "mode": "codex_sdk",
        "sandbox": sandbox,
        "approval_mode": approval_mode,
        "cwd": cwd,
    }
    for attr_name in ["id", "status", "duration_ms"]:
        value = getattr(result, attr_name, None)
        if value is not None:
            usage[attr_name] = getattr(value, "value", value)
    token_usage = getattr(result, "usage", None)
    if token_usage is not None:
        if hasattr(token_usage, "model_dump"):
            usage["token_usage"] = token_usage.model_dump()
        else:
            usage["token_usage"] = token_usage
    return usage


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
    for candidate in _json_response_candidates(response.text):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _json_response_candidates(text: str) -> list[str]:
    stripped = text.strip()
    candidates = [stripped]
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        fenced = "\n".join(lines).strip()
        if fenced:
            candidates.append(fenced)
    extracted = _extract_first_json_object(stripped)
    if extracted:
        candidates.append(extracted)
    return candidates


def _extract_first_json_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


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
