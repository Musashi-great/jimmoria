from __future__ import annotations

from typing import Any


def success(tool: str, data: dict[str, Any], message: str = "ok") -> dict[str, Any]:
    return {"status": "success", "tool": tool, "message": message, "data": data}


def missing_input(tool: str, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"status": "missing_input", "tool": tool, "message": message, "data": data or {}}


def failed(tool: str, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"status": "failed", "tool": tool, "message": message, "data": data or {}}
