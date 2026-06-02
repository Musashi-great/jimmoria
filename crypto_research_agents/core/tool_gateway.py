from __future__ import annotations

from collections.abc import Callable
from time import perf_counter
from typing import Any

from .tool_call import ToolCallRecord


ToolCallable = Callable[..., dict[str, Any]]


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

    def __init__(self, policy_engine: PolicyEngine | None = None) -> None:
        self.policy_engine = policy_engine or PolicyEngine()
        self._tools: dict[str, ToolCallable] = {}
        self.audit_log: list[dict[str, Any]] = []

    def register(self, tool_name: str, func: ToolCallable) -> None:
        self._tools[tool_name] = func

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
            raise

        self._record_tool_call(
            agent_id=agent_id,
            tool_name=tool_name,
            room_id=room_id,
            input_data=kwargs,
            status=result.get("status", "success"),
            latency_ms=_elapsed_ms(started),
            result=result,
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
            input=input_data,
            status=status,
            latency_ms=latency_ms,
            result=result,
        )
        self.audit_log.append(record.to_dict())


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)
