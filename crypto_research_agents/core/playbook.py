from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from crypto_research_agents.core.workflow import WorkflowSpec
from crypto_research_agents.storage.paths import resolve_project_path


@dataclass(slots=True)
class ResearchPlaybook:
    playbook_id: str
    title: str
    body: str
    path: Path

    def to_dict(self) -> dict[str, str]:
        return {
            "playbook_id": self.playbook_id,
            "title": self.title,
            "body": self.body,
            "path": str(self.path),
        }


class ResearchPlaybookRegistry:
    def __init__(self, playbooks: dict[str, ResearchPlaybook] | None = None) -> None:
        self.playbooks = playbooks or {}

    @classmethod
    def load_dir(cls, directory: str | Path = "research_playbooks") -> "ResearchPlaybookRegistry":
        root = resolve_project_path(directory)
        playbooks: dict[str, ResearchPlaybook] = {}
        if not root.exists():
            return cls(playbooks)
        for path in sorted(root.glob("*.md")):
            playbook = load_playbook_file(path)
            playbooks[playbook.playbook_id] = playbook
        return cls(playbooks)

    def get(self, playbook_id: str) -> ResearchPlaybook | None:
        return self.playbooks.get(playbook_id)

    def attach_to_workflow(self, workflow: WorkflowSpec, playbook_ids: list[str]) -> list[ResearchPlaybook]:
        attached: list[ResearchPlaybook] = []
        for playbook_id in playbook_ids:
            playbook = self.get(playbook_id)
            if playbook is not None:
                attached.append(playbook)
        workflow.metadata["attached_playbooks"] = [item.playbook_id for item in attached]
        return attached


def load_playbook_file(path: str | Path) -> ResearchPlaybook:
    target = Path(path)
    body = target.read_text(encoding="utf-8")
    title = ""
    for line in body.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    return ResearchPlaybook(
        playbook_id=target.stem,
        title=title or target.stem.replace("_", " ").title(),
        body=body,
        path=target,
    )
