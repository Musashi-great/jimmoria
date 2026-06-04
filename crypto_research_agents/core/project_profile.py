from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from crypto_research_agents.storage.paths import resolve_project_path


DEFAULT_PROFILE_DIR = "config/project_profiles"


@dataclass(slots=True)
class ProjectProfile:
    profile_id: str
    display_name: str
    aliases: list[str] = field(default_factory=list)
    website: str | None = None
    chain: str | None = None
    official_x: str | None = None
    search_queries: list[str] = field(default_factory=list)
    identity_hints: list[dict[str, Any]] = field(default_factory=list)
    address_registry: dict[str, Any] = field(default_factory=dict)
    funding: dict[str, Any] = field(default_factory=dict)
    article_notes: list[dict[str, str]] = field(default_factory=list)

    def matches(self, value: str) -> bool:
        normalized = normalize_profile_key(value)
        keys = {normalize_profile_key(self.profile_id), normalize_profile_key(self.display_name)}
        keys.update(normalize_profile_key(alias) for alias in self.aliases)
        return normalized in keys


def normalize_profile_key(value: str) -> str:
    return "".join(ch for ch in str(value).lower().strip() if ch.isalnum())


def load_project_profiles(profile_dir: str | Path = DEFAULT_PROFILE_DIR) -> list[ProjectProfile]:
    root = resolve_project_path(profile_dir)
    if not root.exists():
        return []
    profiles: list[ProjectProfile] = []
    for path in sorted([*root.glob("*.yaml"), *root.glob("*.yml")]):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            continue
        profiles.append(
            ProjectProfile(
                profile_id=str(data.get("profile_id") or path.stem),
                display_name=str(data.get("display_name") or path.stem),
                aliases=[str(item) for item in data.get("aliases", []) if item],
                website=data.get("website"),
                chain=data.get("chain"),
                official_x=data.get("official_x"),
                search_queries=[str(item) for item in data.get("search_queries", []) if item],
                identity_hints=[
                    item for item in data.get("identity_hints", []) if isinstance(item, dict)
                ],
                address_registry=data.get("address_registry") if isinstance(data.get("address_registry"), dict) else {},
                funding=data.get("funding") if isinstance(data.get("funding"), dict) else {},
                article_notes=[
                    item for item in data.get("article_notes", []) if isinstance(item, dict)
                ],
            )
        )
    return profiles


def find_project_profile(value: str, profile_dir: str | Path = DEFAULT_PROFILE_DIR) -> ProjectProfile | None:
    if not value:
        return None
    for profile in load_project_profiles(profile_dir):
        if profile.matches(value):
            return profile
    return None


def find_project_profile_in_text(value: str, profile_dir: str | Path = DEFAULT_PROFILE_DIR) -> ProjectProfile | None:
    normalized = normalize_profile_key(value)
    if not normalized:
        return None
    for profile in load_project_profiles(profile_dir):
        keys = {normalize_profile_key(profile.profile_id), normalize_profile_key(profile.display_name)}
        keys.update(normalize_profile_key(alias) for alias in profile.aliases)
        if any(key and key in normalized for key in keys):
            return profile
    return None


def profile_alias_match(tokens: list[str], profile_dir: str | Path = DEFAULT_PROFILE_DIR) -> str | None:
    profiles = load_project_profiles(profile_dir)
    for token in tokens:
        for profile in profiles:
            if profile.matches(token):
                return token.lower()
    return None
