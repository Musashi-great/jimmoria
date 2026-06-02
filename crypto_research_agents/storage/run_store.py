from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from crypto_research_agents.core.bus import CollaborationBus
from crypto_research_agents.core.room import ResearchRoom


def save_run_snapshot(
    *,
    room: ResearchRoom,
    bus: CollaborationBus,
    audit_log: list[dict[str, Any]],
    llm_call_log: list[dict[str, Any]] | None = None,
    event_log: list[dict[str, Any]] | None = None,
    root_dir: str | Path = "data/runs",
) -> Path:
    run_dir = Path(root_dir) / room.room_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "room.json").write_text(
        json.dumps(room.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "messages.json").write_text(
        json.dumps(bus.to_list(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "tool_audit_log.json").write_text(
        json.dumps(audit_log, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "llm_call_log.json").write_text(
        json.dumps(llm_call_log or [], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "events.json").write_text(
        json.dumps(event_log or [], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return run_dir


def list_run_summaries(root_dir: str | Path = "data/runs") -> list[dict[str, Any]]:
    root = Path(root_dir)
    if not root.exists():
        return []
    summaries: list[dict[str, Any]] = []
    for room_path in sorted(root.glob("*/room.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        data = load_run_json(room_path)
        summaries.append(
            {
                "room_id": data.get("room_id", room_path.parent.name),
                "topic": data.get("topic", ""),
                "status": data.get("status", ""),
                "created_at": data.get("created_at", ""),
                "report": data.get("output_paths", {}).get("report", ""),
                "vault": data.get("output_paths", {}).get("obsidian_vault", ""),
            }
        )
    return summaries


def load_run_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_run_file(room_id: str, filename: str, root_dir: str | Path = "data/runs") -> dict[str, Any] | list[Any]:
    path = Path(root_dir) / room_id / filename
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))
