from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crypto_research_agents.storage.paths import resolve_project_path


@dataclass(slots=True)
class RoleSpec:
    description: str
    must_not: list[str] = field(default_factory=list)


@dataclass(slots=True)
class IdentitySpec:
    one_liner: str = ""
    description: str = ""


@dataclass(slots=True)
class PersonalitySpec:
    tone: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    biases_to_avoid: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MissionSpec:
    primary_goal: str = ""
    secondary_goals: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ScopeSpec:
    owns: list[str] = field(default_factory=list)
    does_not_own: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ModelPolicy:
    default_model: str = "mvp_shared_llm"
    escalate_model: str | None = None
    escalation_triggers: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MemoryScope:
    read: list[str] = field(default_factory=list)
    write: list[str] = field(default_factory=list)
    no_access: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ToolPolicy:
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)


@dataclass(slots=True)
class OutputSchema:
    type: str
    required: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AgentSpec:
    agent_id: str
    name: str
    role: RoleSpec
    persona_name: str = ""
    persona_strength: str = "medium"
    identity: IdentitySpec = field(default_factory=IdentitySpec)
    personality: PersonalitySpec = field(default_factory=PersonalitySpec)
    mission: MissionSpec = field(default_factory=MissionSpec)
    scope: ScopeSpec = field(default_factory=ScopeSpec)
    model_policy: ModelPolicy = field(default_factory=ModelPolicy)
    memory_scope: MemoryScope = field(default_factory=MemoryScope)
    tools: ToolPolicy = field(default_factory=ToolPolicy)
    hooks: dict[str, list[str]] = field(default_factory=dict)
    output_schema: OutputSchema | None = None
    collaboration: dict[str, list[str]] = field(default_factory=dict)
    must_follow: list[str] = field(default_factory=list)
    must_not: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentSpec":
        output_schema = data.get("output_schema")
        return cls(
            agent_id=data["agent_id"],
            name=data["name"],
            role=RoleSpec(**data["role"]),
            persona_name=data.get("persona_name", ""),
            persona_strength=data.get("persona_strength", "medium"),
            identity=IdentitySpec(**data.get("identity", {})),
            personality=PersonalitySpec(**data.get("personality", {})),
            mission=MissionSpec(**data.get("mission", {})),
            scope=ScopeSpec(**data.get("scope", {})),
            model_policy=ModelPolicy(**data.get("model_policy", {})),
            memory_scope=MemoryScope(**data.get("memory_scope", {})),
            tools=ToolPolicy(**data.get("tools", {})),
            hooks=data.get("hooks", {}),
            output_schema=OutputSchema(**output_schema) if output_schema else None,
            collaboration=data.get("collaboration", {}),
            must_follow=data.get("must_follow", []),
            must_not=data.get("must_not", []),
        )

    def system_prompt(self) -> str:
        lines = [
            f"You are {self.name}.",
        ]
        if self.persona_name:
            lines.append(f"Persona: {self.persona_name}.")
        if self.identity.one_liner:
            lines.append(f"Identity: {self.identity.one_liner}")
        if self.identity.description:
            lines.append(self.identity.description)
        if self.mission.primary_goal:
            lines.append(f"Primary goal: {self.mission.primary_goal}")
        if self.scope.owns:
            lines.append("You own: " + ", ".join(self.scope.owns))
        if self.scope.does_not_own:
            lines.append("You do not own: " + ", ".join(self.scope.does_not_own))
        if self.personality.tone:
            lines.append("Tone: " + ", ".join(self.personality.tone))
        if self.must_follow:
            lines.append("Must follow:")
            lines.extend(f"- {item}" for item in self.must_follow)
        combined_must_not = [*self.role.must_not, *self.must_not]
        if combined_must_not:
            lines.append("Must not:")
            lines.extend(f"- {item}" for item in combined_must_not)
        if self.output_schema:
            lines.append(
                f"Output schema type: {self.output_schema.type}. Required fields: "
                + ", ".join(self.output_schema.required)
            )
        return "\n".join(lines)


class AgentSpecRegistry:
    def __init__(self, specs: dict[str, AgentSpec] | None = None) -> None:
        self.specs = specs or {}

    def get(self, agent_id: str) -> AgentSpec | None:
        return self.specs.get(agent_id)

    @classmethod
    def load_dir(cls, directory: str | Path) -> "AgentSpecRegistry":
        root = resolve_project_path(directory)
        specs: dict[str, AgentSpec] = {}
        if not root.exists():
            return cls(specs)
        for path in sorted(root.glob("*.yaml")):
            data = _load_yaml_like(path)
            spec = AgentSpec.from_dict(data)
            specs[spec.agent_id] = spec
        return cls(specs)


def _load_yaml_like(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            f"{path} is not JSON-compatible YAML and PyYAML is not installed."
        ) from exc
    return yaml.safe_load(text)
