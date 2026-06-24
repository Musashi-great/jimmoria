from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jimmoria.storage.paths import resolve_project_path


@dataclass(slots=True)
class SkillSpec:
    skill_id: str
    name: str = ""
    owner_agents: list[str] = field(default_factory=list)
    purpose: str = ""
    description: str = ""
    tools: dict[str, list[str]] = field(default_factory=dict)
    steps: list[str] = field(default_factory=list)
    outputs: dict[str, Any] = field(default_factory=dict)
    guardrails: list[str] = field(default_factory=list)
    quality_gates: list[str] = field(default_factory=list)
    required_output: list[str] = field(default_factory=list)
    source_path: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, source_path: str = "") -> "SkillSpec":
        skill_id = str(data.get("skill_id") or data.get("id") or data.get("name") or "").strip()
        if not skill_id:
            raise ValueError(f"Skill spec missing skill_id: {source_path}")
        owner_agents = _string_list(data.get("owner_agents"))
        owner = data.get("owner")
        if owner and str(owner) not in owner_agents:
            owner_agents.append(str(owner))
        return cls(
            skill_id=skill_id,
            name=str(data.get("name") or skill_id),
            owner_agents=owner_agents,
            purpose=str(data.get("purpose") or data.get("description") or ""),
            description=str(data.get("description") or ""),
            tools=_tools_dict(data.get("tools")),
            steps=_string_list(data.get("steps")),
            outputs=data.get("outputs") if isinstance(data.get("outputs"), dict) else {},
            guardrails=_string_list(data.get("guardrails")),
            quality_gates=_string_list(data.get("quality_gates")),
            required_output=_string_list(data.get("required_output")),
            source_path=source_path,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "owner": self.owner_agents[0] if self.owner_agents else "",
            "owner_agents": self.owner_agents,
            "purpose": self.purpose,
            "description": self.description,
            "tools": self.tools,
            "steps": self.steps,
            "outputs": self.outputs,
            "guardrails": self.guardrails,
            "quality_gates": self.quality_gates,
            "required_output": self.required_output,
            "source_path": self.source_path,
        }


class SkillSpecRegistry:
    def __init__(self, skills: dict[str, SkillSpec] | None = None) -> None:
        self.skills = skills or {}

    def get(self, skill_id: str) -> SkillSpec | None:
        return self.skills.get(_normalize_skill_id(skill_id))

    def list_for_agent(self, agent_id: str) -> list[SkillSpec]:
        return [
            skill
            for skill in self.skills.values()
            if agent_id in skill.owner_agents
        ]

    def to_dict(self) -> dict[str, Any]:
        return {"skills": {skill_id: skill.to_dict() for skill_id, skill in self.skills.items()}}

    @classmethod
    def load_dir(cls, directory: str | Path = "config/skills") -> "SkillSpecRegistry":
        root = resolve_project_path(directory)
        skills: dict[str, SkillSpec] = {}
        if not root.exists():
            return cls(skills)
        for path in sorted(root.glob("*.yaml")):
            data = _load_yaml_like(path)
            for spec in _specs_from_file(data, path):
                skills[_normalize_skill_id(spec.skill_id)] = spec
        return cls(skills)


def _specs_from_file(data: dict[str, Any], path: Path) -> list[SkillSpec]:
    if isinstance(data.get("skills"), dict):
        specs: list[SkillSpec] = []
        for skill_id, raw in data["skills"].items():
            entry = dict(raw) if isinstance(raw, dict) else {"description": str(raw)}
            entry.setdefault("skill_id", str(skill_id))
            specs.append(SkillSpec.from_dict(entry, source_path=str(path)))
        return specs
    return [SkillSpec.from_dict(data, source_path=str(path))]


def _normalize_skill_id(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, tuple):
        return [str(item) for item in value]
    if isinstance(value, str):
        return [value]
    return []


def _tools_dict(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _string_list(raw) for key, raw in value.items()}


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
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise RuntimeError(f"{path} must contain a YAML mapping.")
    return loaded
