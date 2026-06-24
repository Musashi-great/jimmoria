from __future__ import annotations

from pathlib import Path

from jimmoria.core.memory import ProjectCandidate, SourceRecord
from jimmoria.storage.paths import safe_filename


class ObsidianStore:
    def __init__(self, vault_dir: str | Path) -> None:
        self.vault_dir = Path(vault_dir)
        self.projects_dir = self.vault_dir / "10_Projects"
        self.sources_dir = self.vault_dir / "20_Sources"
        self.narratives_dir = self.vault_dir / "30_Narratives"
        self.reports_dir = self.vault_dir / "50_Reports"
        for directory in [self.projects_dir, self.sources_dir, self.narratives_dir, self.reports_dir]:
            directory.mkdir(parents=True, exist_ok=True)

    def write_source_note(self, source: SourceRecord) -> Path:
        path = self.sources_dir / f"{safe_filename(source.title)}.md"
        body = [
            "---",
            "type: source",
            f"source_id: {source.source_id}",
            f"source_type: {source.source_type}",
            f"title: {source.title}",
            f"url: {source.url or ''}",
            f"created_at: {source.created_at}",
            "---",
            "",
            f"# {source.title}",
            "",
            "## Extracted Metadata",
            f"- Entities: {', '.join(source.metadata.get('entities', [])) or 'none'}",
            f"- Keywords: {', '.join(source.metadata.get('keywords', [])) or 'none'}",
            "",
            "## Content",
            source.content,
        ]
        path.write_text("\n".join(body), encoding="utf-8")
        return path

    def write_project_note(self, project: ProjectCandidate) -> Path:
        path = self.projects_dir / f"{safe_filename(project.name)}.md"
        narratives = "\n".join(f"  - {item}" for item in project.narratives) or "  - unknown"
        source_links = "\n".join(f"- [[{source_id}]]" for source_id in project.sources) or "- none"
        candidate_origin = str(project.metadata.get("candidate_origin") or "unknown")
        source_backing = str(project.metadata.get("source_backing") or "unknown")
        body = [
            "---",
            "type: project",
            f"project_id: {project.project_id}",
            f"project_name: {project.name}",
            f"candidate_origin: {candidate_origin}",
            f"source_backing: {source_backing}",
            f"chain: {project.chain or 'unknown'}",
            "narrative:",
            narratives,
            f"token_status: {project.token_status}",
            f"early_radar_score: {project.score:.0f}",
            f"created_at: {project.created_at}",
            "---",
            "",
            f"# {project.name}",
            "",
            "## Summary",
            project.reason_found,
            "",
            "## Candidate Origin",
            f"- Origin: `{candidate_origin}`",
            f"- Source backing: `{source_backing}`",
            "- Interpretation: MVP placeholders are planning leads, not verified live project discoveries."
            if candidate_origin == "mvp_placeholder"
            else "- Interpretation: This candidate has source-backed discovery metadata.",
            "",
            "## Related Narratives",
        ]
        body.extend(f"- [[{narrative}]]" for narrative in project.narratives)
        body.extend(["", "## Related Sources", source_links])
        path.write_text("\n".join(body), encoding="utf-8")
        for narrative in project.narratives:
            self.write_narrative_note(narrative)
        return path

    def write_narrative_note(self, narrative: str) -> Path:
        path = self.narratives_dir / f"{safe_filename(narrative)}.md"
        if not path.exists():
            path.write_text(
                "\n".join(
                    [
                        "---",
                        "type: narrative",
                        f"name: {narrative}",
                        "---",
                        "",
                        f"# {narrative}",
                        "",
                        "## Linked Projects",
                    ]
                ),
                encoding="utf-8",
            )
        return path

    def write_report(self, topic: str, markdown: str) -> Path:
        path = self.reports_dir / f"{safe_filename(topic)}.md"
        path.write_text(markdown, encoding="utf-8")
        return path
