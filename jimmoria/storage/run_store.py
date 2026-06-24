from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from jimmoria.core.bus import CollaborationBus
from jimmoria.core.room import ResearchRoom
from jimmoria.core.time import utc_now


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
    normalized_events = normalize_event_log(event_log or [])
    (run_dir / "events.json").write_text(
        json.dumps(normalized_events, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return run_dir


def delete_run_snapshot(*, room_id: str, root_dir: str | Path = "data/runs") -> bool:
    run_dir = Path(root_dir) / room_id
    if not run_dir.exists():
        return False
    if not run_dir.is_dir():
        return False
    shutil.rmtree(run_dir)
    return True


def report_index_path(root_dir: str | Path = "data/runs") -> Path:
    return Path(root_dir).parent / "report_index.json"


def load_report_index(root_dir: str | Path = "data/runs") -> list[dict[str, Any]]:
    path = report_index_path(root_dir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def append_report_index(
    *,
    room: ResearchRoom,
    root_dir: str | Path = "data/runs",
) -> Path | None:
    report_path = room.output_paths.get("report")
    if not report_path:
        return None
    path = report_index_path(root_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = load_report_index(root_dir)
    rows = [row for row in rows if str(row.get("room_id") or "") != room.room_id]
    quality = room.project_card.get("research_quality") if isinstance(room.project_card, dict) else {}
    if not isinstance(quality, dict):
        quality = {}
    rows.insert(
        0,
        {
            "room_id": room.room_id,
            "topic": room.topic,
            "status": room.status,
            "created_at": room.created_at,
            "indexed_at": utc_now(),
            "report": report_path,
            "vault": room.output_paths.get("obsidian_vault", ""),
            "quality_status": quality.get("status", ""),
            "quality_reasons": quality.get("reasons", []),
        },
    )
    path.write_text(json.dumps(rows[:200], ensure_ascii=False, indent=2), encoding="utf-8")
    return path


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


def normalize_event_log(events: list[Any]) -> list[dict[str, Any]]:
    """Return event rows with stable AX-style sequence numbers."""

    normalized: list[dict[str, Any]] = []
    for index, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            event = {"type": "unknown", "raw": event}
        row = dict(event)
        try:
            seq = int(row.get("seq", index))
        except (TypeError, ValueError):
            seq = index
        if seq <= 0:
            seq = index
        row["seq"] = seq
        normalized.append(row)
    return normalized


def events_after_seq(events: list[Any], last_seq: int = 0) -> list[dict[str, Any]]:
    normalized = normalize_event_log(events)
    return [event for event in normalized if int(event.get("seq", 0)) > last_seq]


def fork_run_snapshot(
    *,
    src_room_id: str,
    dest_room_id: str | None = None,
    src_seq: int | None = None,
    root_dir: str | Path = "data/runs",
) -> Path:
    """Fork a saved run directory from a checkpoint sequence into a new run snapshot."""

    root = Path(root_dir)
    src_dir = root / src_room_id
    if not src_dir.exists():
        raise FileNotFoundError(src_dir)

    dest_room_id = dest_room_id or f"room_fork_{uuid4().hex[:10]}"
    dest_dir = root / dest_room_id
    if dest_dir.exists():
        raise FileExistsError(dest_dir)

    shutil.copytree(src_dir, dest_dir)

    room_path = dest_dir / "room.json"
    room = json.loads(room_path.read_text(encoding="utf-8")) if room_path.exists() else {}
    output_paths = room.get("output_paths") if isinstance(room.get("output_paths"), dict) else {}
    room.update(
        {
            "room_id": dest_room_id,
            "parent_room_id": src_room_id,
            "forked_from": {
                "room_id": src_room_id,
                "seq": src_seq,
                "created_at": utc_now(),
            },
            "status": "forked",
            "output_paths": output_paths,
        }
    )
    room_path.write_text(json.dumps(room, ensure_ascii=False, indent=2), encoding="utf-8")

    events_path = dest_dir / "events.json"
    events = normalize_event_log(json.loads(events_path.read_text(encoding="utf-8")) if events_path.exists() else [])
    if src_seq is not None:
        if not any(int(event.get("seq", 0)) == src_seq for event in events):
            raise ValueError(f"Source sequence does not exist in {src_room_id}: {src_seq}")
        events = [event for event in events if int(event.get("seq", 0)) <= src_seq]
    next_seq = (max((int(event.get("seq", 0)) for event in events), default=0) + 1)
    events.append(
        {
            "seq": next_seq,
            "type": "run_forked",
            "room_id": dest_room_id,
            "parent_room_id": src_room_id,
            "src_seq": src_seq,
            "summary": "Forked a saved Research Room event log from a checkpoint.",
        }
    )
    events_path.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")

    return dest_dir
