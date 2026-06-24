from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jimmoria.core.agent_spec import _load_yaml_like
from jimmoria.storage.paths import resolve_project_path
from jimmoria.tools.registry import ToolRegistry
from jimmoria.tools.registry import load_tool_registry


@dataclass(slots=True)
class WorkerProfile:
    profile_id: str
    role: str
    allowed_toolsets: list[str] = field(default_factory=list)
    output_destination: str = "local"
    model_route: str = "reasoning_model"

    @classmethod
    def from_dict(cls, profile_id: str, data: dict[str, Any]) -> "WorkerProfile":
        return cls(
            profile_id=profile_id,
            role=str(data.get("role", "")),
            allowed_toolsets=list(data.get("allowed_toolsets", [])),
            output_destination=str(data.get("output_destination", "local")),
            model_route=str(data.get("model_route", "reasoning_model")),
        )

    def allowed_tools(self, registry: ToolRegistry | None = None) -> set[str]:
        tool_registry = registry or load_tool_registry()
        return tool_registry.allowed_tools_for_toolsets(self.allowed_toolsets)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "role": self.role,
            "allowed_toolsets": self.allowed_toolsets,
            "output_destination": self.output_destination,
            "model_route": self.model_route,
        }


class WorkerProfileRegistry:
    def __init__(self, profiles: dict[str, WorkerProfile] | None = None) -> None:
        self.profiles = profiles or {}

    @classmethod
    def load(cls, path: str | Path = "config/profiles.yaml") -> "WorkerProfileRegistry":
        target = resolve_project_path(path)
        if not target.exists():
            return cls()
        data = _load_yaml_like(target)
        profiles = {
            str(profile_id): WorkerProfile.from_dict(str(profile_id), dict(raw or {}))
            for profile_id, raw in dict(data.get("profiles", {})).items()
        }
        return cls(profiles)

    def get(self, profile_id: str) -> WorkerProfile | None:
        return self.profiles.get(profile_id)

    def list_profiles(self) -> list[WorkerProfile]:
        return sorted(self.profiles.values(), key=lambda item: item.profile_id)
