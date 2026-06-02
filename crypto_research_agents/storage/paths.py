from __future__ import annotations

import re
from pathlib import Path


def safe_filename(value: str, fallback: str = "untitled") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "", value).strip().replace(" ", "-")
    cleaned = re.sub(r"-+", "-", cleaned)
    return cleaned[:90] or fallback


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_project_path(path: str | Path) -> Path:
    value = Path(path)
    if value.is_absolute():
        return value

    cwd_value = Path.cwd() / value
    if cwd_value.exists():
        return cwd_value

    return project_root() / value


def default_project_path(path: str | Path) -> str:
    return str(project_root() / path)
