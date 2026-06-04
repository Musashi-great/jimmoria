from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import urllib.error
import urllib.request
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
    reasoning_effort: str = "standard"


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
            usage={"mode": "offline", "reasoning_effort": request.reasoning_effort},
        )


class HybridLLMProvider:
    """Status provider for Codex+Grok mode.

    Actual routing is handled by ModelGateway because each task can choose a
    different concrete provider.
    """

    provider_name = "codex_grok"

    def complete(self, request: LLMRequest) -> LLMResponse:
        raise RuntimeError("Codex+Grok hybrid mode must be routed through ModelGateway.")


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
                reasoning_effort=request.reasoning_effort,
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
            usage={
                "mode": "codex_cli",
                "reasoning_effort": request.reasoning_effort,
                "codex_model_reasoning_effort": _codex_config_reasoning_effort(request.reasoning_effort),
            },
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
                reasoning_effort=request.reasoning_effort,
            ),
            raw=result,
        )


class GrokProvider:
    """LLM provider for the xAI Grok OpenAI-compatible API."""

    provider_name = "grok"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        api_mode: str | None = None,
        prefer_hermes_oauth: bool | None = None,
    ) -> None:
        if prefer_hermes_oauth is None:
            prefer_hermes_oauth = os.getenv("LLM_PROVIDER", "").strip().lower() in {
                "grok_oauth",
                "xai_oauth",
                "xai-oauth",
                "grok-oauth",
            }
        token, source, auth_base_url = (
            (api_key, "constructor", None)
            if api_key
            else _grok_bearer_token(prefer_hermes_oauth=prefer_hermes_oauth)
        )
        self.api_key = token or ""
        self.auth_source = source
        self.base_url = (
            base_url
            or os.getenv("GROK_BASE_URL")
            or os.getenv("XAI_BASE_URL")
            or auth_base_url
            or "https://api.x.ai/v1"
        ).rstrip("/")
        self.api_mode = (api_mode or os.getenv("GROK_API_MODE") or "responses").strip().lower()
        self.timeout = float(os.getenv("GROK_API_TIMEOUT") or os.getenv("XAI_API_TIMEOUT") or "360")

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def complete(self, request: LLMRequest) -> LLMResponse:
        if not self.api_key:
            raise RuntimeError(
                "Grok provider is missing a bearer token. Set XAI_API_KEY, GROK_API_KEY, "
                "GROK_OAUTH_TOKEN, XAI_OAUTH_TOKEN, GROK_OAUTH_TOKEN_FILE, "
                "GROK_OAUTH_TOKEN_COMMAND, or run `hermes auth add xai-oauth`."
            )

        if self.api_mode in {"chat", "chat_completions", "chat-completions"}:
            endpoint = f"{self.base_url}/chat/completions"
            payload = _grok_chat_payload(request)
        else:
            endpoint = f"{self.base_url}/responses"
            payload = _grok_responses_payload(request)

        raw = _post_json(
            endpoint,
            payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
        )
        text = _extract_openai_compatible_text(raw)
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        usage = {
            **usage,
            "mode": "grok",
            "api_mode": self.api_mode,
            "base_url": self.base_url,
            "auth_source": self.auth_source,
            "reasoning_effort": request.reasoning_effort,
            "xai_reasoning_effort": _grok_reasoning_effort(request),
        }
        return LLMResponse(
            text=text.strip(),
            model=request.model,
            provider=self.provider_name,
            usage=usage,
            raw=raw,
        )


def provider_from_env() -> LLMProvider:
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    if provider in {"offline", "fallback", "none"}:
        return OfflineLLMProvider()
    if provider in {"codex_grok", "grok_codex", "codex+grok", "grok+codex", "hybrid", "dual", "multi"}:
        return HybridLLMProvider()
    if provider in {"codex_cli", "codex_device", "codex_login"}:
        return CodexCliProvider()
    if provider in {"codex_sdk", "codex", "sdk"}:
        return CodexSdkProvider()
    if provider in {"grok", "xai"}:
        return GrokProvider()
    if provider in {"grok_oauth", "xai_oauth", "grok-oauth", "xai-oauth"}:
        return GrokProvider(prefer_hermes_oauth=True)
    return OfflineLLMProvider()


def grok_auth_status() -> str:
    token, source, _base_url = _grok_bearer_token(prefer_hermes_oauth=True)
    if token:
        return f"Grok/xAI bearer token from {source}"
    return "missing Grok/xAI token; run `hermes auth add xai-oauth` or set XAI_API_KEY"


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
    reasoning_effort: str = "standard",
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

    if _codex_exec_supports(help_text, "--config"):
        codex_effort = _codex_config_reasoning_effort(reasoning_effort)
        if codex_effort:
            command.extend(["--config", f'model_reasoning_effort="{codex_effort}"'])

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
        f"\nReasoning effort requested: {request.reasoning_effort.upper()}."
        "\nFor PRO effort, slow down mentally, compare alternatives, identify missing evidence, "
        "and produce a richer synthesis without inventing facts."
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
        f"\nReasoning effort requested: {request.reasoning_effort.upper()}."
        "\nFor PRO effort, slow down mentally, compare alternatives, identify missing evidence, "
        "and produce a richer synthesis without inventing facts."
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


def _codex_sdk_usage(result: Any, *, sandbox: str, approval_mode: str, cwd: str, reasoning_effort: str) -> dict[str, Any]:
    usage: dict[str, Any] = {
        "mode": "codex_sdk",
        "sandbox": sandbox,
        "approval_mode": approval_mode,
        "cwd": cwd,
        "reasoning_effort": reasoning_effort,
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


def _codex_config_reasoning_effort(reasoning_effort: str) -> str:
    normalized = (reasoning_effort or "standard").strip().lower().replace("-", "_")
    if normalized in {"pro", "xhigh", "extra_high", "max", "maximum"}:
        return "xhigh"
    if normalized in {"high", "deep"}:
        return "high"
    if normalized in {"fast", "low"}:
        return "low"
    return "medium"


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


def _grok_bearer_token(*, prefer_hermes_oauth: bool = False) -> tuple[str | None, str, str | None]:
    if prefer_hermes_oauth:
        hermes_token, hermes_source, hermes_base_url = _hermes_xai_oauth_bearer_token()
        if hermes_token:
            return hermes_token, hermes_source, hermes_base_url

    for name in ["GROK_OAUTH_TOKEN", "XAI_OAUTH_TOKEN", "GROK_API_KEY", "XAI_API_KEY"]:
        value = os.getenv(name)
        if value:
            return value.strip(), name, None

    for name in ["GROK_OAUTH_TOKEN_FILE", "XAI_OAUTH_TOKEN_FILE", "GROK_TOKEN_FILE", "XAI_TOKEN_FILE"]:
        path = os.getenv(name)
        if not path:
            continue
        try:
            value = Path(path).expanduser().read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value, name, None

    for name in ["GROK_OAUTH_TOKEN_COMMAND", "XAI_OAUTH_TOKEN_COMMAND", "GROK_TOKEN_COMMAND", "XAI_TOKEN_COMMAND"]:
        command = os.getenv(name)
        if not command:
            continue
        try:
            completed = subprocess.run(
                shlex.split(command, posix=os.name != "nt"),
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode == 0:
            value = completed.stdout.strip()
            if value:
                return value, name, None

    if not prefer_hermes_oauth:
        hermes_token, hermes_source, hermes_base_url = _hermes_xai_oauth_bearer_token()
        if hermes_token:
            return hermes_token, hermes_source, hermes_base_url
    return None, "missing", None


def _hermes_xai_oauth_bearer_token() -> tuple[str | None, str, str | None]:
    """Resolve a Grok OAuth token from Hermes without exposing token material."""

    try:
        from hermes_cli.auth import resolve_xai_oauth_runtime_credentials  # type: ignore

        creds = resolve_xai_oauth_runtime_credentials()
        token = str(creds.get("api_key") or "").strip()
        base_url = str(creds.get("base_url") or "").strip().rstrip("/") or None
        if token:
            return token, "Hermes xai-oauth session", base_url
    except Exception:
        pass

    return _read_hermes_xai_oauth_auth_json()


def _read_hermes_xai_oauth_auth_json() -> tuple[str | None, str, str | None]:
    auth_path = _hermes_auth_json_path()
    if not auth_path.exists():
        return None, "missing", None
    try:
        data = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "missing", None
    if not isinstance(data, dict):
        return None, "missing", None

    providers = data.get("providers")
    if isinstance(providers, dict):
        state = providers.get("xai-oauth") or providers.get("grok-oauth")
        if isinstance(state, dict):
            tokens = state.get("tokens")
            if isinstance(tokens, dict):
                token = str(tokens.get("access_token") or "").strip()
                if token:
                    return token, f"Hermes auth.json ({auth_path})", _auth_json_xai_base_url(state)

    pool = data.get("credential_pool")
    if isinstance(pool, dict):
        entries = pool.get("xai-oauth") or pool.get("grok-oauth")
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                token = str(entry.get("access_token") or "").strip()
                if token:
                    base_url = str(entry.get("base_url") or entry.get("inference_base_url") or "").strip().rstrip("/")
                    return token, f"Hermes credential_pool ({auth_path})", base_url or None
    return None, "missing", None


def _auth_json_xai_base_url(state: dict[str, Any]) -> str | None:
    base_url = str(
        state.get("base_url")
        or state.get("inference_base_url")
        or state.get("api_base_url")
        or ""
    ).strip().rstrip("/")
    if base_url:
        return base_url
    return None


def _hermes_auth_json_path() -> Path:
    configured = os.getenv("HERMES_AUTH_JSON")
    if configured:
        return Path(configured).expanduser()
    hermes_home = os.getenv("HERMES_HOME")
    if hermes_home:
        return Path(hermes_home).expanduser() / "auth.json"
    home = os.getenv("USERPROFILE") or os.getenv("HOME")
    if home:
        return Path(home).expanduser() / ".hermes" / "auth.json"
    try:
        return Path.home() / ".hermes" / "auth.json"
    except RuntimeError:
        return Path(".hermes") / "auth.json"


def _grok_responses_payload(request: LLMRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": request.model,
        "input": [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": request.user_prompt},
        ],
        "store": False,
    }
    if request.max_tokens:
        payload["max_output_tokens"] = request.max_tokens
    reasoning_effort = _grok_reasoning_effort(request)
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}
    if request.response_format == "json":
        payload["text"] = {"format": {"type": "json_object"}}
    return payload


def _grok_chat_payload(request: LLMRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": request.model,
        "messages": [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": request.user_prompt},
        ],
        "temperature": request.temperature,
    }
    if request.max_tokens:
        payload["max_tokens"] = request.max_tokens
    reasoning_effort = _grok_reasoning_effort(request)
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    if request.response_format == "json":
        payload["response_format"] = {"type": "json_object"}
    return payload


def _grok_reasoning_effort(request: LLMRequest) -> str | None:
    model = request.model.lower()
    if "grok-4.20" in model and "multi-agent" not in model:
        return None
    normalized = (request.reasoning_effort or "standard").strip().lower().replace("-", "_")
    if "multi-agent" in model:
        if normalized in {"pro", "xhigh", "extra_high", "max", "maximum"}:
            return "xhigh"
        if normalized in {"high", "deep"}:
            return "high"
        return "medium"
    if normalized in {"pro", "xhigh", "extra_high", "max", "maximum", "high", "deep"}:
        return "high"
    if normalized in {"fast", "low"}:
        return "low"
    return "medium"


def _post_json(url: str, payload: dict[str, Any], *, headers: dict[str, str], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Grok provider failed: HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Grok provider failed: {exc}") from exc
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Grok provider returned non-JSON response: {body[:500]}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Grok provider returned an unexpected response shape.")
    return data


def _extract_openai_compatible_text(raw: dict[str, Any]) -> str:
    output_text = raw.get("output_text")
    if isinstance(output_text, str):
        return output_text

    output = raw.get("output")
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, list):
                for content_item in content:
                    if not isinstance(content_item, dict):
                        continue
                    text = content_item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                    elif isinstance(content_item.get("output_text"), str):
                        parts.append(str(content_item["output_text"]))
            elif isinstance(content, str):
                parts.append(content)
        if parts:
            return "\n".join(parts)

    choices = raw.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]
            if isinstance(first.get("text"), str):
                return first["text"]

    return json.dumps(raw, ensure_ascii=False)


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
