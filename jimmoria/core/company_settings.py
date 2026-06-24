"""Backward-compatible aliases for personal-agent settings.

The primary settings module is now :mod:`jimmoria.core.personal_agent_settings`.
This file remains so older imports and saved scripts keep working during the
rename window.
"""

from jimmoria.core.personal_agent_settings import (
    CompanySettings,
    PersonalAgentSettings,
    company_settings_path_for,
    default_intake_policy,
    default_supervisor_authority,
    legacy_company_settings_path_for,
    load_company_settings,
    load_personal_agent_settings,
    personal_agent_settings_path_for,
    save_company_settings,
    save_personal_agent_settings,
)

__all__ = [
    "PersonalAgentSettings",
    "CompanySettings",
    "default_supervisor_authority",
    "default_intake_policy",
    "personal_agent_settings_path_for",
    "legacy_company_settings_path_for",
    "load_personal_agent_settings",
    "save_personal_agent_settings",
    "company_settings_path_for",
    "load_company_settings",
    "save_company_settings",
]
