from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jimmoria.core.agent_spec import _load_yaml_like
from jimmoria.storage.paths import resolve_project_path


@dataclass(slots=True)
class StackOrchestrator:
    name: str = "Hermes Agent"
    internal_agent_id: str = "supervisor_agent"
    role: str = "single personal agent harness and central orchestrator"


@dataclass(slots=True)
class StackLayer:
    layer_id: str
    name: str
    kind: str
    status: str
    purpose: str
    tools: list[str] = field(default_factory=list)
    required_env: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StackLayer":
        return cls(
            layer_id=str(data.get("id") or data.get("layer_id") or ""),
            name=str(data.get("name") or ""),
            kind=str(data.get("kind") or ""),
            status=str(data.get("status") or "planned"),
            purpose=str(data.get("purpose") or ""),
            tools=[str(item) for item in data.get("tools", [])],
            required_env=[str(item) for item in data.get("required_env", [])],
        )

    @property
    def missing_env(self) -> list[str]:
        return [name for name in self.required_env if not os.getenv(name)]

    @property
    def runtime_status(self) -> str:
        if self.missing_env:
            return "missing_secret"
        if self.status == "implemented":
            return "configured"
        return self.status


@dataclass(slots=True)
class AgentStack:
    stack_id: str
    product_frame: str
    display_name: str
    orchestrator: StackOrchestrator
    principles: list[str] = field(default_factory=list)
    layers: list[StackLayer] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentStack":
        orchestrator_data = data.get("orchestrator") if isinstance(data.get("orchestrator"), dict) else {}
        return cls(
            stack_id=str(data.get("stack_id") or "jimmoria_personal_agent_stack"),
            product_frame=str(data.get("product_frame") or "personal_agent_os"),
            display_name=str(data.get("display_name") or "JIMMORIA Personal Agent"),
            orchestrator=StackOrchestrator(**orchestrator_data),
            principles=[str(item) for item in data.get("principles", [])],
            layers=[StackLayer.from_dict(item) for item in data.get("layers", []) if isinstance(item, dict)],
        )

    def layer(self, layer_id: str) -> StackLayer | None:
        for item in self.layers:
            if item.layer_id == layer_id:
                return item
        return None


def load_agent_stack(path: str | Path = "config/agent_stack.yaml") -> AgentStack:
    target = resolve_project_path(path)
    if not target.exists():
        return AgentStack.from_dict({})
    data = _load_yaml_like(target)
    if not isinstance(data, dict):
        raise RuntimeError(f"Agent stack config must be a mapping: {target}")
    return AgentStack.from_dict(data)
