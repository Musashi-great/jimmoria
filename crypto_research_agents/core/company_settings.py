from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from crypto_research_agents.core.time import utc_now


@dataclass(slots=True)
class CompanySettings:
    """Persistent operating preferences for JIMMORIA as a research company."""

    report_language: str = "en"
    allow_english_terms: bool = True
    auto_apply_company_instructions: bool = True
    supervisor_mode: str = "research_director"
    client_relationship: str = "user"
    operating_principles: list[str] = field(default_factory=list)
    raw_instructions: list[str] = field(default_factory=list)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CompanySettings":
        known = {field_name for field_name in cls.__dataclass_fields__}
        filtered = {key: value for key, value in data.items() if key in known}
        return cls(**filtered)


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
