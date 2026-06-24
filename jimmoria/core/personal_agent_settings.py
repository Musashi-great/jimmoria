from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from jimmoria.core.time import utc_now


def default_supervisor_authority() -> list[str]:
    return [
        "classify_every_plain_chat_input",
        "choose_output_mode",
        "apply_personal_agent_settings_without_research_room",
        "open_research_room_for_explicit_research",
        "route_personal_agent_stack",
        "use_long_term_memory_when_relevant",
        "assign_specialist_agents",
        "orchestrate_specialist_workflow",
        "coordinate_agent_council",
        "set_report_direction",
        "final_quality_gate",
    ]


def default_intake_policy() -> dict[str, str]:
    return {
        "research_request": "open full Research Room and produce a dossier",
        "source_ingestion": "open small ingestion room and save notes",
        "personal_agent_config": "apply personal-agent settings directly without a report",
        "personal_agent_status": "show personal-agent state without a report",
        "supervisor_chat": "answer directly without opening a Research Room",
    }


@dataclass(slots=True)
class PersonalAgentSettings:
    """Persistent operating preferences for JIMMORIA's Hermes personal agent stack."""

    report_language: str = "en"
    allow_english_terms: bool = True
    auto_apply_agent_instructions: bool = True
    supervisor_mode: str = "research_director"
    user_relationship: str = "owner_operator"
    supervisor_authority: list[str] = field(default_factory=default_supervisor_authority)
    intake_policy: dict[str, str] = field(default_factory=default_intake_policy)
    operating_principles: list[str] = field(default_factory=list)
    raw_instructions: list[str] = field(default_factory=list)
    updated_at: str = field(default_factory=utc_now)

    @property
    def auto_apply_personal_agent_instructions(self) -> bool:
        return self.auto_apply_agent_instructions

    @auto_apply_personal_agent_instructions.setter
    def auto_apply_personal_agent_instructions(self, value: bool) -> None:
        self.auto_apply_agent_instructions = value

    @property
    def client_relationship(self) -> str:
        return self.user_relationship

    @client_relationship.setter
    def client_relationship(self, value: str) -> None:
        self.user_relationship = value

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PersonalAgentSettings":
        if "auto_apply_personal_agent_instructions" in data and "auto_apply_agent_instructions" not in data:
            data = dict(data)
            data["auto_apply_agent_instructions"] = data.pop("auto_apply_personal_agent_instructions")
        if "client_relationship" in data and "user_relationship" not in data:
            data = dict(data)
            data["user_relationship"] = data.pop("client_relationship")
        if isinstance(data.get("intake_policy"), dict):
            policy = dict(data["intake_policy"])
            if "company_config" in policy and "personal_agent_config" not in policy:
                policy["personal_agent_config"] = policy.pop("company_config")
            if "company_status" in policy and "personal_agent_status" not in policy:
                policy["personal_agent_status"] = policy.pop("company_status")
            data = dict(data)
            data["intake_policy"] = policy
        known = {field_name for field_name in cls.__dataclass_fields__}
        filtered = {key: value for key, value in data.items() if key in known}
        settings = cls(**filtered)
        default_authority = default_supervisor_authority()
        settings.supervisor_authority = [
            *default_authority,
            *[item for item in settings.supervisor_authority if item not in default_authority],
        ]
        default_policy = default_intake_policy()
        default_policy.update(settings.intake_policy or {})
        settings.intake_policy = default_policy
        return settings


def personal_agent_settings_path_for(memory_path: str | Path | None = None) -> Path:
    env_path = os.getenv("JIMMORIA_PERSONAL_AGENT_SETTINGS_PATH") or os.getenv("JIMMORIA_COMPANY_SETTINGS_PATH")
    if env_path:
        return Path(env_path)
    if memory_path is not None:
        return Path(memory_path).parent / "personal_agent_settings.json"
    return Path("data/personal_agent_settings.json")


def legacy_company_settings_path_for(memory_path: str | Path | None = None) -> Path:
    if memory_path is not None:
        return Path(memory_path).parent / "company_settings.json"
    return Path("data/company_settings.json")


def _legacy_path_for(settings_path: Path) -> Path:
    if settings_path.name == "personal_agent_settings.json":
        return settings_path.with_name("company_settings.json")
    return legacy_company_settings_path_for()


def load_personal_agent_settings(path: str | Path | None = None) -> PersonalAgentSettings:
    settings_path = Path(path) if path is not None else personal_agent_settings_path_for()
    if not settings_path.exists():
        legacy_path = _legacy_path_for(settings_path)
        if legacy_path.exists():
            settings_path = legacy_path
    if not settings_path.exists():
        return PersonalAgentSettings()
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return PersonalAgentSettings()
    if not isinstance(data, dict):
        return PersonalAgentSettings()
    return PersonalAgentSettings.from_dict(data)


def save_personal_agent_settings(settings: PersonalAgentSettings, path: str | Path | None = None) -> None:
    settings_path = Path(path) if path is not None else personal_agent_settings_path_for()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings.updated_at = utc_now()
    settings_path.write_text(
        json.dumps(settings.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# Backward-compatible names for older code and saved integrations.
CompanySettings = PersonalAgentSettings
company_settings_path_for = personal_agent_settings_path_for
load_company_settings = load_personal_agent_settings
save_company_settings = save_personal_agent_settings
