from __future__ import annotations

import re


def safe_filename(value: str, fallback: str = "untitled") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "", value).strip().replace(" ", "-")
    cleaned = re.sub(r"-+", "-", cleaned)
    return cleaned[:90] or fallback
