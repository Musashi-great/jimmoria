from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jimmoria.core.agent_spec import _load_yaml_like
from jimmoria.storage.paths import resolve_project_path


READ_ONLY_MODE = "read_only"
ARTIFACT_WRITE_MODE = "artifact_write"
WRITE_MODE = "write"
DANGEROUS_MODE = "dangerous"
DEFAULT_RESEARCH_MODES = {READ_ONLY_MODE, ARTIFACT_WRITE_MODE}


@dataclass(slots=True)
class ToolDefinition:
    tool_id: str
    connector_name: str = ""
    category: str = ""
    mode: str = READ_ONLY_MODE
    implementation_status: str = "planned"
    description: str = ""
    required_secrets: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, tool_id: str, data: dict[str, Any]) -> "ToolDefinition":
        return cls(
            tool_id=tool_id,
            connector_name=str(data.get("connector_name") or tool_id),
            category=str(data.get("category", "")),
            mode=str(data.get("mode", READ_ONLY_MODE)),
            implementation_status=str(data.get("implementation_status", "planned")),
            description=str(data.get("description", "")),
            required_secrets=list(data.get("required_secrets", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "connector_name": self.connector_name,
            "category": self.category,
            "mode": self.mode,
            "implementation_status": self.implementation_status,
            "description": self.description,
            "required_secrets": self.required_secrets,
        }

    @property
    def is_read_only_boundary_safe(self) -> bool:
        return self.mode in DEFAULT_RESEARCH_MODES

    @property
    def is_dangerous(self) -> bool:
        return self.mode == DANGEROUS_MODE

    def missing_secrets(self) -> list[str]:
        return [name for name in self.required_secrets if not os.getenv(name)]


@dataclass(slots=True)
class Toolset:
    toolset_id: str
    tools: list[str] = field(default_factory=list)
    description: str = ""

    @classmethod
    def from_dict(cls, toolset_id: str, data: dict[str, Any] | list[str]) -> "Toolset":
        if isinstance(data, list):
            return cls(toolset_id=toolset_id, tools=[str(item) for item in data])
        return cls(
            toolset_id=toolset_id,
            tools=[str(item) for item in data.get("tools", [])],
            description=str(data.get("description", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "toolset_id": self.toolset_id,
            "description": self.description,
            "tools": self.tools,
        }


@dataclass(slots=True)
class ToolAvailability:
    tool_id: str
    status: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"tool_id": self.tool_id, "status": self.status, "detail": self.detail}


class ToolRegistry:
    def __init__(
        self,
        *,
        definitions: dict[str, ToolDefinition] | None = None,
        toolsets: dict[str, Toolset] | None = None,
    ) -> None:
        self.definitions = definitions or {}
        self.toolsets = toolsets or {}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolRegistry":
        tool_data = data.get("tools", {})
        toolset_data = data.get("toolsets", {})
        definitions = {
            str(tool_id): ToolDefinition.from_dict(str(tool_id), dict(value or {}))
            for tool_id, value in tool_data.items()
        }
        toolsets = {
            str(toolset_id): Toolset.from_dict(str(toolset_id), value)
            for toolset_id, value in toolset_data.items()
        }
        return cls(definitions=definitions, toolsets=toolsets)

    def get(self, tool_id: str) -> ToolDefinition | None:
        return self.definitions.get(tool_id)

    def toolset(self, toolset_id: str) -> Toolset | None:
        return self.toolsets.get(toolset_id)

    def allowed_tools_for_toolsets(self, toolset_ids: list[str]) -> set[str]:
        allowed: set[str] = set()
        for toolset_id in toolset_ids:
            toolset = self.toolsets.get(toolset_id)
            if toolset is None:
                continue
            allowed.update(toolset.tools)
        return allowed

    def tools_for_agent_policy(self, explicit_tools: list[str], allowed_toolsets: list[str]) -> set[str]:
        return set(explicit_tools) | self.allowed_tools_for_toolsets(allowed_toolsets)

    def is_tool_allowed_for_research(self, tool_id: str) -> bool:
        definition = self.definitions.get(tool_id)
        if definition is None:
            return False
        return definition.is_read_only_boundary_safe

    def assert_toolsets_research_safe(self, toolset_ids: list[str]) -> None:
        unsafe = [
            tool_id
            for tool_id in sorted(self.allowed_tools_for_toolsets(toolset_ids))
            if not self.is_tool_allowed_for_research(tool_id)
        ]
        if unsafe:
            raise PermissionError(f"Toolsets include non-research tools: {', '.join(unsafe)}")

    def dangerous_tools(self) -> list[ToolDefinition]:
        return sorted(
            [definition for definition in self.definitions.values() if definition.is_dangerous],
            key=lambda item: item.tool_id,
        )

    def availability(
        self,
        tool_id: str,
        *,
        registered_connectors: set[str] | None = None,
    ) -> ToolAvailability:
        definition = self.definitions.get(tool_id)
        if definition is None:
            return ToolAvailability(tool_id, "missing", "tool is not defined in config/toolsets.yaml")
        if definition.is_dangerous:
            return ToolAvailability(tool_id, "blocked", "dangerous wallet/trading/signing tool is blocked")
        missing_secrets = definition.missing_secrets()
        if missing_secrets:
            return ToolAvailability(
                tool_id,
                "missing_secret",
                "missing " + ", ".join(missing_secrets),
            )
        if registered_connectors is not None and definition.connector_name not in registered_connectors:
            if definition.implementation_status == "implemented":
                return ToolAvailability(
                    tool_id,
                    "missing_connector",
                    f"{definition.connector_name} is implemented in config but not registered",
                )
            return ToolAvailability(
                tool_id,
                "placeholder",
                f"{definition.connector_name} connector not registered",
            )
        if definition.implementation_status == "implemented":
            return ToolAvailability(tool_id, "configured", definition.connector_name)
        return ToolAvailability(tool_id, "placeholder", definition.implementation_status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tools": {tool_id: definition.to_dict() for tool_id, definition in self.definitions.items()},
            "toolsets": {toolset_id: toolset.to_dict() for toolset_id, toolset in self.toolsets.items()},
        }


def load_tool_registry(path: str | Path = "config/toolsets.yaml") -> ToolRegistry:
    target = resolve_project_path(path)
    if not target.exists():
        return ToolRegistry()
    data = _load_yaml_like(target)
    if not isinstance(data, dict):
        raise RuntimeError(f"Tool registry must be a mapping: {target}")
    return ToolRegistry.from_dict(data)
