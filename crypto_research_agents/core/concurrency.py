from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crypto_research_agents.core.agent_spec import _load_yaml_like
from crypto_research_agents.storage.paths import resolve_project_path


@dataclass(slots=True)
class ParallelGroupSpec:
    group_id: str
    agents: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    after_agent: str = ""
    join_before: str = ""
    room_template: str = ""
    candidate_fanout: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ParallelGroupSpec":
        return cls(
            group_id=str(data.get("group_id", "")),
            agents=[str(item) for item in data.get("agents", [])],
            tools=[str(item) for item in data.get("tools", [])],
            after_agent=str(data.get("after_agent", "")),
            join_before=str(data.get("join_before", "")),
            room_template=str(data.get("room_template", "")),
            candidate_fanout=bool(data.get("candidate_fanout", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "agents": self.agents,
            "tools": self.tools,
            "after_agent": self.after_agent,
            "join_before": self.join_before,
            "room_template": self.room_template,
            "candidate_fanout": self.candidate_fanout,
        }


@dataclass(slots=True)
class ConcurrencyPhaseSpec:
    phase: int
    name: str
    status: str = "planned"
    mode: str = "sequential"
    description: str = ""
    max_parallel: int = 1
    parallel_groups: list[ParallelGroupSpec] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConcurrencyPhaseSpec":
        return cls(
            phase=int(data["phase"]),
            name=str(data.get("name", data["phase"])),
            status=str(data.get("status", "planned")),
            mode=str(data.get("mode", "sequential")),
            description=str(data.get("description", "")),
            max_parallel=max(1, int(data.get("max_parallel", 1) or 1)),
            parallel_groups=[
                ParallelGroupSpec.from_dict(dict(item or {}))
                for item in data.get("parallel_groups", [])
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "name": self.name,
            "status": self.status,
            "mode": self.mode,
            "description": self.description,
            "max_parallel": self.max_parallel,
            "parallel_groups": [group.to_dict() for group in self.parallel_groups],
        }


@dataclass(slots=True)
class ConcurrencyPolicy:
    policy_version: str = "0.1"
    active_phase: int = 1
    default_max_parallel: int = 1
    safety: dict[str, Any] = field(default_factory=dict)
    phases: list[ConcurrencyPhaseSpec] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConcurrencyPolicy":
        return cls(
            policy_version=str(data.get("policy_version", "0.1")),
            active_phase=int(data.get("active_phase", 1) or 1),
            default_max_parallel=max(1, int(data.get("default_max_parallel", 1) or 1)),
            safety=dict(data.get("safety", {})),
            phases=[ConcurrencyPhaseSpec.from_dict(dict(item or {})) for item in data.get("phases", [])],
        )

    @property
    def active(self) -> ConcurrencyPhaseSpec:
        for phase in self.phases:
            if phase.phase == self.active_phase:
                return phase
        return ConcurrencyPhaseSpec(
            phase=self.active_phase,
            name="sequential_room",
            status="active",
            mode="sequential",
            description="Fallback sequential execution.",
            max_parallel=1,
        )

    def event_payload(self) -> dict[str, Any]:
        active = self.active
        return {
            "policy_version": self.policy_version,
            "active_phase": active.to_dict(),
            "default_max_parallel": self.default_max_parallel,
            "safety": self.safety,
        }


def load_concurrency_policy(path: str | Path = "config/concurrency.yaml") -> ConcurrencyPolicy:
    target = resolve_project_path(path)
    if not target.exists():
        return ConcurrencyPolicy(
            phases=[
                ConcurrencyPhaseSpec(
                    phase=1,
                    name="sequential_room",
                    status="active",
                    mode="sequential",
                    description="Fallback sequential execution.",
                    max_parallel=1,
                )
            ]
        )
    data = _load_yaml_like(target)
    if not isinstance(data, dict):
        raise RuntimeError(f"Concurrency policy must be a mapping: {target}")
    return ConcurrencyPolicy.from_dict(data)
