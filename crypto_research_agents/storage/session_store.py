from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SEARCH_FILES = [
    "room.json",
    "sources.json",
    "findings.json",
    "candidates.json",
    "report.json",
    "report.md",
]


@dataclass(slots=True)
class SessionSearchResult:
    room_id: str
    path: Path
    matched_file: str
    snippet: str

    def to_dict(self) -> dict[str, str]:
        return {
            "room_id": self.room_id,
            "path": str(self.path),
            "matched_file": self.matched_file,
            "snippet": self.snippet,
        }


def search_sessions(query: str, *, runs_dir: str | Path = "data/runs") -> list[SessionSearchResult]:
    needle = query.lower().strip()
    if not needle:
        return []
    root = Path(runs_dir)
    if not root.exists():
        return []

    results: list[SessionSearchResult] = []
    for run_dir in sorted([path for path in root.iterdir() if path.is_dir()]):
        for filename in SEARCH_FILES:
            path = run_dir / filename
            if not path.exists():
                continue
            haystack = _file_search_text(path)
            lower = haystack.lower()
            if needle not in lower:
                continue
            results.append(
                SessionSearchResult(
                    room_id=run_dir.name,
                    path=run_dir,
                    matched_file=filename,
                    snippet=_snippet(haystack, lower.index(needle), len(query)),
                )
            )
            break
    return results


def _file_search_text(path: Path) -> str:
    if path.suffix == ".md":
        return path.read_text(encoding="utf-8", errors="ignore")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")
    return json.dumps(data, ensure_ascii=False)


def _snippet(text: str, start: int, query_length: int, *, limit: int = 160) -> str:
    left = max(0, start - 60)
    right = min(len(text), start + query_length + 80)
    snippet = text[left:right].replace("\n", " ").strip()
    if len(snippet) <= limit:
        return snippet
    return snippet[: limit - 3].rstrip() + "..."
