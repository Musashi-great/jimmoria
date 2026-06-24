from __future__ import annotations

from pathlib import Path
from typing import Any

from jimmoria.agents.base import AgentResult, BaseAgent
from jimmoria.core.bus import CollaborationBus
from jimmoria.core.memory import SharedMemory
from jimmoria.core.room import ResearchRoom
from jimmoria.storage.obsidian_store import ObsidianStore


class ObsidianCuratorAgent(BaseAgent):
    agent_id = "obsidian_curator_agent"
    name = "Obsidian Curator Agent"
    task_type = "obsidian_sync"

    def run(self, room: ResearchRoom, memory: SharedMemory, bus: CollaborationBus, **kwargs: Any) -> AgentResult:
        vault_dir = Path(kwargs.get("vault_dir", "vault"))
        store = ObsidianStore(vault_dir)

        written: list[str] = []
        for source_id in room.source_inputs:
            source = memory.sources.get(source_id)
            if source:
                written.append(str(store.write_source_note(source)))

        for project in current_room_projects(room, memory):
            written.append(str(store.write_project_note(project)))

        if room.report_draft:
            written.append(str(store.write_report(room.topic, room.report_draft)))

        room.output_paths["obsidian_vault"] = str(vault_dir)
        summary = f"Obsidian sync wrote {len(written)} notes."
        llm_analysis = self.llm_analysis_pass(
            room=room,
            objective="Review the Obsidian sync result and identify follow-up knowledge curation work.",
            evidence={
                "written_paths": written,
                "source_count": len(room.source_inputs),
                "project_count": len(current_room_projects(room, memory)),
                "has_report": bool(room.report_draft),
            },
            fallback_summary=summary,
        )
        finding = self.write_finding(
            room=room,
            memory=memory,
            finding_type="obsidian_sync",
            summary=summary,
            data={"paths": written, "llm_analysis": llm_analysis},
            confidence=0.75,
        )
        bus.update(
            room_id=room.room_id,
            from_agent=self.agent_id,
            summary="Obsidian vault updated.",
            payload={"finding_id": finding.finding_id, "paths": written, "llm_analysis": llm_analysis},
        )
        return AgentResult(self.agent_id, summary, {"finding_id": finding.finding_id, "paths": written, "llm_analysis": llm_analysis}, confidence=0.75)


def current_room_projects(room: ResearchRoom, memory: SharedMemory) -> list[Any]:
    project_ids: list[str] = []
    for finding in memory.get_room_findings(room.room_id):
        if finding.finding_type != "candidate_discovery":
            continue
        for candidate in finding.data.get("candidates", []):
            if isinstance(candidate, dict) and candidate.get("project_id"):
                project_ids.append(str(candidate["project_id"]))
    if project_ids:
        return [
            memory.projects[project_id]
            for project_id in project_ids
            if project_id in memory.projects
        ]
    return [
        project
        for project in memory.projects.values()
        if set(project.sources).intersection(room.source_inputs)
    ]
