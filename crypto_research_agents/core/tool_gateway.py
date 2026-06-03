from __future__ import annotations

from collections.abc import Callable, Mapping
from time import perf_counter
from typing import Any

from .tool_call import ToolCallRecord


ToolCallable = Callable[..., dict[str, Any]]
EventCallback = Callable[..., None]


class PolicyEngine:
    def __init__(self, policies: dict[str, set[str]] | None = None) -> None:
        self._policies = policies or {}

    def allow(self, agent_id: str, tool_name: str) -> None:
        self._policies.setdefault(agent_id, set()).add(tool_name)

    def can_call(self, agent_id: str, tool_name: str) -> bool:
        allowed = self._policies.get(agent_id, set())
        return "*" in allowed or tool_name in allowed


class ToolGateway:
    """Permission-checked tool facade.

    OAuth/API keys belong behind this gateway. Agents only see tool names and
    normalized results.
    """

    def __init__(
        self,
        policy_engine: PolicyEngine | None = None,
        *,
        event_callback: EventCallback | None = None,
    ) -> None:
        self.policy_engine = policy_engine or PolicyEngine()
        self._tools: dict[str, ToolCallable] = {}
        self.audit_log: list[dict[str, Any]] = []
        self.event_callback = event_callback

    def register(self, tool_name: str, func: ToolCallable) -> None:
        self._tools[tool_name] = func

    def set_event_callback(self, callback: EventCallback | None) -> None:
        self.event_callback = callback

    @property
    def registered_tools(self) -> set[str]:
        return set(self._tools)

    def has_tool(self, tool_name: str) -> bool:
        return tool_name in self._tools

    def call(
        self,
        agent_id: str,
        tool_name: str,
        *,
        room_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        started = perf_counter()
        self._emit_tool_event(
            "tool_start",
            agent_id=agent_id,
            tool_name=tool_name,
            room_id=room_id,
            input_data=kwargs,
        )
        if not self.policy_engine.can_call(agent_id, tool_name):
            self._record_tool_call(
                agent_id=agent_id,
                tool_name=tool_name,
                room_id=room_id,
                input_data=kwargs,
                status="denied",
                latency_ms=_elapsed_ms(started),
                result={"error": "permission_denied"},
            )
            self._emit_tool_event(
                "tool_denied",
                agent_id=agent_id,
                tool_name=tool_name,
                room_id=room_id,
                input_data=kwargs,
                latency_ms=_elapsed_ms(started),
                message="permission_denied",
            )
            raise PermissionError(f"{agent_id} is not allowed to call {tool_name}")

        tool = self._tools.get(tool_name)
        if tool is None:
            result = {
                "status": "unconfigured",
                "tool": tool_name,
                "message": "Tool connector is not configured in MVP runtime.",
                "data": None,
            }
            self._record_tool_call(
                agent_id=agent_id,
                tool_name=tool_name,
                room_id=room_id,
                input_data=kwargs,
                status="unconfigured",
                latency_ms=_elapsed_ms(started),
                result=result,
            )
            self._emit_tool_event(
                "tool_unconfigured",
                agent_id=agent_id,
                tool_name=tool_name,
                room_id=room_id,
                input_data=kwargs,
                latency_ms=_elapsed_ms(started),
                message=result.get("message", "Tool connector is not configured."),
            )
            return result

        try:
            result = tool(**kwargs)
        except Exception as exc:
            self._record_tool_call(
                agent_id=agent_id,
                tool_name=tool_name,
                room_id=room_id,
                input_data=kwargs,
                status="failed",
                latency_ms=_elapsed_ms(started),
                result={"error": str(exc)},
            )
            self._emit_tool_event(
                "tool_failed",
                agent_id=agent_id,
                tool_name=tool_name,
                room_id=room_id,
                input_data=kwargs,
                latency_ms=_elapsed_ms(started),
                message=str(exc),
            )
            raise

        status = result.get("status", "success")
        self._record_tool_call(
            agent_id=agent_id,
            tool_name=tool_name,
            room_id=room_id,
            input_data=kwargs,
            status=status,
            latency_ms=_elapsed_ms(started),
            result=result,
        )
        self._emit_tool_event(
            "tool_done",
            agent_id=agent_id,
            tool_name=tool_name,
            room_id=room_id,
            input_data=kwargs,
            latency_ms=_elapsed_ms(started),
            status=status,
            message=str(result.get("message") or status),
        )
        return result

    def _record_tool_call(
        self,
        *,
        agent_id: str,
        tool_name: str,
        room_id: str | None,
        input_data: dict[str, Any],
        status: str,
        latency_ms: int,
        result: dict[str, Any],
    ) -> None:
        record = ToolCallRecord(
            room_id=room_id,
            agent_id=agent_id,
            tool_name=tool_name,
            input=_sanitize_for_log(input_data),
            status=status,
            latency_ms=latency_ms,
            result=_sanitize_for_log(result),
        )
        self.audit_log.append(record.to_dict())

    def _emit_tool_event(
        self,
        event_type: str,
        *,
        agent_id: str,
        tool_name: str,
        room_id: str | None,
        input_data: dict[str, Any],
        latency_ms: int | None = None,
        status: str | None = None,
        message: str | None = None,
    ) -> None:
        if self.event_callback is None:
            return
        payload: dict[str, Any] = {
            "agent_id": agent_id,
            "tool_name": tool_name,
            "room_id": room_id,
            "input_preview": _preview(_sanitize_for_log(input_data)),
        }
        if latency_ms is not None:
            payload["latency_ms"] = latency_ms
        if status is not None:
            payload["status"] = status
        if message:
            payload["summary"] = message
        self.event_callback(event_type, **payload)


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)


def _preview(value: Any, *, limit: int = 180) -> str:
    text = repr(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


SENSITIVE_KEY_NAMES = {
    "api_key",
    "apikey",
    "authorization",
    "auth_header",
    "bearer",
    "credential",
    "credentials",
    "password",
    "private_key",
    "secret",
    "seed_phrase",
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "client_secret",
}
SENSITIVE_KEY_SUFFIXES = (
    "_api_key",
    "_apikey",
    "_token",
    "_secret",
    "_password",
    "_private_key",
    "_seed_phrase",
)


def _sanitize_for_log(value: Any, *, max_string_length: int = 2000) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                sanitized[key_text] = "<redacted>"
            else:
                sanitized[key_text] = _sanitize_for_log(item, max_string_length=max_string_length)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_for_log(item, max_string_length=max_string_length) for item in value[:100]]
    if isinstance(value, tuple):
        return tuple(_sanitize_for_log(item, max_string_length=max_string_length) for item in value[:100])
    if isinstance(value, set):
        return [_sanitize_for_log(item, max_string_length=max_string_length) for item in list(value)[:100]]
    if isinstance(value, str):
        return _truncate_string(value, max_string_length)
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if normalized in {"required_secret", "required_secrets"}:
        return False
    return (
        normalized in SENSITIVE_KEY_NAMES
        or normalized.startswith("authorization")
        or any(normalized.endswith(suffix) for suffix in SENSITIVE_KEY_SUFFIXES)
    )


def _truncate_string(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 30].rstrip() + f"... <truncated {len(value) - limit + 30} chars>"
