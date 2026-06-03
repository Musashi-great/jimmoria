from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from crypto_research_agents.core.workflow import WorkflowSpec
from crypto_research_agents.runtime import ResearchRunResult


class ArtifactStore:
    def __init__(self, root_dir: str | Path = "data/runs") -> None:
        self.root_dir = Path(root_dir)

    def archive_workflow_run(
        self,
        *,
        result: ResearchRunResult,
        workflow: WorkflowSpec,
        workflow_trace: list[dict[str, Any]],
        event_log: list[dict[str, Any]] | None = None,
        tool_audit_log: list[dict[str, Any]] | None = None,
        input_payload: dict[str, Any] | None = None,
    ) -> Path:
        room = result.room
        run_dir = self.root_dir / room.room_id
        run_dir.mkdir(parents=True, exist_ok=True)

        write_json(run_dir / "workflow.yaml", workflow.to_dict())
        write_json(
            run_dir / "input.json",
            input_payload
            or {
                "room_id": room.room_id,
                "topic": room.topic,
                "goals": room.goals,
                "source_inputs": room.source_inputs,
            },
        )
        write_json(run_dir / "workflow_trace.json", workflow_trace)
        write_jsonl(run_dir / "events.jsonl", event_log or [])
        write_jsonl(run_dir / "messages.jsonl", result.bus.to_list())
        write_jsonl(run_dir / "tool_calls.jsonl", tool_audit_log or [])

        sources = [
            result.memory.sources[source_id].to_dict()
            for source_id in room.source_inputs
            if source_id in result.memory.sources
        ]
        findings = [finding.to_dict() for finding in result.memory.get_room_findings(room.room_id)]
        candidates = [
            project.to_dict()
            for project in result.memory.projects.values()
            if set(project.sources).intersection(room.source_inputs)
        ]
        write_json(run_dir / "sources.json", sources)
        write_json(run_dir / "findings.json", findings)
        write_json(run_dir / "candidates.json", candidates)

        report_path = room.output_paths.get("report", "")
        report_text = Path(report_path).read_text(encoding="utf-8") if report_path and Path(report_path).exists() else ""
        (run_dir / "report.md").write_text(report_text, encoding="utf-8")
        (run_dir / "report.compact.md").write_text(render_compact_report_stub(report_text), encoding="utf-8")
        write_json(
            run_dir / "report.json",
            {
                "room_id": room.room_id,
                "topic": room.topic,
                "report_path": report_path,
                "quality": room.project_card.get("research_quality", {}),
            },
        )
        return run_dir


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def render_compact_report_stub(report_text: str) -> str:
    if not report_text.strip():
        return "# Compact Report\n\nReport was not generated.\n"
    lines = [line for line in report_text.splitlines() if line.strip()]
    return "\n".join(lines[:40]) + "\n"
