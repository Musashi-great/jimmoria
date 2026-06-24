from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from crypto_research_agents.core.time import utc_now


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
        "company_config": "apply personal-agent settings directly without a report",
        "company_status": "show personal-agent state without a report",
        "supervisor_chat": "answer directly without opening a Research Room",
    }


@dataclass(slots=True)
class CompanySettings:
    """Persistent operating preferences for JIMMORIA's Hermes personal agent stack."""

    report_language: str = "en"
    allow_english_terms: bool = True
    auto_apply_company_instructions: bool = True
    supervisor_mode: str = "research_director"
    client_relationship: str = "user"
    supervisor_authority: list[str] = field(default_factory=default_supervisor_authority)
    intake_policy: dict[str, str] = field(default_factory=default_intake_policy)
    operating_principles: list[str] = field(default_factory=list)
    raw_instructions: list[str] = field(default_factory=list)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CompanySettings":
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


def company_settings_path_for(memory_path: str | Path | None = None) -> Path:
    env_path = os.getenv("JIMMORIA_COMPANY_SETTINGS_PATH")
    if env_path:
        return Path(env_path)
    if memory_path is not None:
        return Path(memory_path).parent / "company_settings.json"
    return Path("data/company_settings.json")


def load_company_settings(path: str | Path | None = None) -> CompanySettings:
    settings_path = Path(path) if path is not None else company_settings_path_for()
    if not settings_path.exists():
        return CompanySettings()
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return CompanySettings()
    if not isinstance(data, dict):
        return CompanySettings()
    return CompanySettings.from_dict(data)


def save_company_settings(settings: CompanySettings, path: str | Path | None = None) -> None:
    settings_path = Path(path) if path is not None else company_settings_path_for()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings.updated_at = utc_now()
    settings_path.write_text(
        json.dumps(settings.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
