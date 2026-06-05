from __future__ import annotations

import os
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



def user_config_dir() -> Path:
    configured = os.getenv("JIMMORIA_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser()
    xdg_config_home = os.getenv("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home).expanduser() / "jimmoria"
    if os.name == "nt":
        appdata = os.getenv("APPDATA")
        if appdata:
            return Path(appdata).expanduser() / "JIMMORIA"
    return Path.home() / ".config" / "jimmoria"


def user_config_path(path: str | Path) -> Path:
    value = Path(path)
    if value.is_absolute():
        return value.expanduser()
    return user_config_dir() / value

def default_project_path(path: str | Path) -> str:
    return str(project_root() / path)
