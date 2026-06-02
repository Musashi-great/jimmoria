from __future__ import annotations

from pathlib import Path
from typing import Any

from crypto_research_agents.agents.base import AgentResult, BaseAgent
from crypto_research_agents.core.bus import CollaborationBus
from crypto_research_agents.core.memory import FindingRecord, SharedMemory
from crypto_research_agents.core.model_gateway import ModelGateway
from crypto_research_agents.core.room import ResearchRoom
from crypto_research_agents.storage.paths import safe_filename


class ReportAgent(BaseAgent):
    agent_id = "report_agent"
    name = "Report Agent"
    task_type = "report_writing"

    def run(self, room: ResearchRoom, memory: SharedMemory, bus: CollaborationBus, **kwargs: Any) -> AgentResult:
        reports_dir = Path(kwargs.get("reports_dir", "reports"))
        reports_dir.mkdir(parents=True, exist_ok=True)
        decision = self.model_gateway.select(agent_id=self.agent_id, task_type=self.task_type)
        findings = memory.get_room_findings(room.room_id)
        llm_summary = self._write_llm_summary(room, memory, findings)
        report = render_project_dossier(room, memory, findings, decision.selected_model, llm_summary)
        report_path = reports_dir / f"{safe_filename(room.topic)}-{room.room_id}.md"
        report_path.write_text(report, encoding="utf-8")
        room.report_draft = report
        room.output_paths["report"] = str(report_path)

        finding = self.write_finding(
            room=room,
            memory=memory,
            finding_type="report",
            summary=f"Report written to {report_path}",
            data={"report_path": str(report_path), "model": decision.selected_model, "llm_summary": llm_summary},
            confidence=0.7,
        )
        bus.handoff(
            room_id=room.room_id,
            from_agent=self.agent_id,
            to_agent="obsidian_curator_agent",
            summary="Final report is ready for Obsidian sync.",
            payload={"report_path": str(report_path), "finding_id": finding.finding_id},
        )
        return AgentResult(
            self.agent_id,
            f"Report written to {report_path}",
            {"finding_id": finding.finding_id, "report_path": str(report_path)},
            confidence=0.7,
        )

    def _write_llm_summary(
        self,
        room: ResearchRoom,
        memory: SharedMemory,
        findings: list[FindingRecord],
    ) -> str:
        candidate_lines = []
        for project in memory.projects.values():
            candidate_lines.append(
                f"- {project.name}: {', '.join(project.narratives)}; {project.reason_found}"
            )
        finding_lines = [f"- {finding.agent_id}: {finding.summary}" for finding in findings]
        response = self.model_gateway.complete(
            agent_id=self.agent_id,
            task_type="final_synthesis",
            system_prompt=self.system_prompt(
                "You are the Report Agent for a crypto research company. "
                "Write a concise research TL;DR. Do not invent live data. "
                "Clearly mention when connector data is not configured."
            ),
            user_prompt=(
                f"Topic: {room.topic}\n\n"
                f"Goals:\n" + "\n".join(f"- {goal}" for goal in room.goals) + "\n\n"
                f"Candidates:\n" + "\n".join(candidate_lines) + "\n\n"
                f"Agent findings:\n" + "\n".join(finding_lines)
            ),
        )
        return response.text.strip()


def render_project_dossier(
    room: ResearchRoom,
    memory: SharedMemory,
    findings: list[FindingRecord],
    model_name: str,
    llm_summary: str,
) -> str:
    candidates = [
        project
        for project in memory.projects.values()
        if set(project.sources).intersection(room.source_inputs)
    ]
    sources = [memory.sources[source_id] for source_id in room.source_inputs if source_id in memory.sources]

    lines: list[str] = [
        f"# Project Research Dossier: {room.topic}",
        "",
        "## 1. TL;DR",
        f"- Room ID: `{room.room_id}`",
        "- Current judgment: Research More",
        f"- LLM synthesis: {llm_summary or 'No LLM synthesis available.'}",
        "- Note: This MVP uses local placeholders for live social/on-chain/product checks until connectors are configured.",
        "",
        "## 2. Goals",
    ]
    lines.extend(f"- {goal}" for goal in room.goals)

    lines.extend(["", "## 3. Sources"])
    if sources:
        for source in sources:
            url = f" ({source.url})" if source.url else ""
            lines.append(f"- `{source.source_id}` - {source.title}{url}")
    else:
        lines.append("- No sources attached.")

    lines.extend(["", "## 4. Candidate Projects"])
    if candidates:
        lines.extend(
            [
                "| Project | Narrative | Token Status | Score | Why Found |",
                "|---|---|---|---:|---|",
            ]
        )
        for project in candidates:
            narrative = ", ".join(project.narratives) or "unknown"
            lines.append(
                f"| {project.name} | {narrative} | {project.token_status} | {project.score:.0f} | {project.reason_found} |"
            )
    else:
        lines.append("- No candidates discovered.")

    lines.extend(["", "## 5. Agent Findings"])
    for finding in findings:
        lines.extend(
            [
                f"### {finding.agent_id}",
                f"- Type: `{finding.finding_type}`",
                f"- Summary: {finding.summary}",
                f"- Confidence: {finding.confidence:.2f}",
            ]
        )
        if finding.sources:
            lines.append(f"- Sources: {', '.join(finding.sources)}")
        lines.append("")

    lines.extend(
        [
            "## 6. Open Questions",
            "- Configure live X/Twitter, Telegram, GitHub, Docs, Explorer, and funding connectors.",
            "- Replace MVP-generated candidates with live discovery results.",
            "- Add source-backed KOL mention history and social momentum scores.",
            "",
            "## 7. Runtime Metadata",
            f"- Report model route: `{model_name}`",
        ]
    )
    return "\n".join(lines)
