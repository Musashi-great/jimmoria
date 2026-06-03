from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crypto_research_agents.core.agent_spec import _load_yaml_like
from crypto_research_agents.storage.paths import resolve_project_path


@dataclass(slots=True)
class TaskSpec:
    task_id: str
    agent_id: str
    description: str = ""
    expected_output: str = ""
    phase: str = ""
    requires: list[str] = field(default_factory=list)
    output_channels: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskSpec":
        return cls(
            task_id=data["task_id"],
            agent_id=data["agent_id"],
            description=data.get("description", ""),
            expected_output=data.get("expected_output", ""),
            phase=data.get("phase", ""),
            requires=list(data.get("requires", [])),
            output_channels=list(data.get("output_channels", [])),
        )


@dataclass(slots=True)
class ProcessSpec:
    process_id: str
    name: str
    process_type: str = "sequential"
    supervisor_mode: str = "controlled_p2p"
    goals: list[str] = field(default_factory=list)
    playbooks: list[str] = field(default_factory=list)
    tasks: list[TaskSpec] = field(default_factory=list)
    execution_strategy: dict[str, Any] = field(default_factory=dict)
    artifact_contracts: dict[str, str] = field(default_factory=dict)
    ui: dict[str, Any] = field(default_factory=dict)
    memory_policy: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProcessSpec":
        return cls(
            process_id=data["process_id"],
            name=data.get("name", data["process_id"]),
            process_type=data.get("process_type", "sequential"),
            supervisor_mode=data.get("supervisor_mode", "controlled_p2p"),
            goals=list(data.get("goals", [])),
            playbooks=list(data.get("playbooks", [])),
            tasks=[TaskSpec.from_dict(item) for item in data.get("tasks", [])],
            execution_strategy=dict(data.get("execution_strategy", {})),
            artifact_contracts=dict(data.get("artifact_contracts", {})),
            ui=dict(data.get("ui", {})),
            memory_policy=dict(data.get("memory_policy", {})),
        )

    @property
    def agent_ids(self) -> list[str]:
        agent_ids: list[str] = []
        for task in self.tasks:
            if task.agent_id not in agent_ids:
                agent_ids.append(task.agent_id)
        return agent_ids

    def task_for_agent(self, agent_id: str) -> TaskSpec | None:
        for task in self.tasks:
            if task.agent_id == agent_id:
                return task
        return None

    def event_payload(self) -> dict[str, Any]:
        return {
            "process_id": self.process_id,
            "process_name": self.name,
            "process_type": self.process_type,
            "supervisor_mode": self.supervisor_mode,
            "execution_strategy": self.execution_strategy,
            "playbooks": self.playbooks,
            "tasks": [
                {
                    "task_id": task.task_id,
                    "agent_id": task.agent_id,
                    "phase": task.phase,
                    "description": task.description,
                    "expected_output": task.expected_output,
                }
                for task in self.tasks
            ],
        }


class ProcessSpecRegistry:
    def __init__(self, specs: dict[str, ProcessSpec] | None = None) -> None:
        self.specs = specs or {}

    def get(self, process_id: str) -> ProcessSpec | None:
        return self.specs.get(process_id)

    @classmethod
    def load_dir(cls, directory: str | Path) -> "ProcessSpecRegistry":
        root = resolve_project_path(directory)
        specs: dict[str, ProcessSpec] = {}
        if not root.exists():
            return cls(specs)
        for path in sorted(root.glob("*.yaml")):
            data = _load_yaml_like(path)
            spec = ProcessSpec.from_dict(data)
            specs[spec.process_id] = spec
        return cls(specs)


def load_process_spec(process_id: str, directory: str | Path = "config/processes") -> ProcessSpec:
    registry = ProcessSpecRegistry.load_dir(directory)
    spec = registry.get(process_id)
    if spec is None:
        available = ", ".join(sorted(registry.specs)) or "none"
        raise RuntimeError(f"Process spec not found: {process_id}. Available: {available}")
    return spec


def process_spec_to_json(spec: ProcessSpec) -> str:
    return json.dumps(spec.event_payload(), ensure_ascii=False, indent=2)
