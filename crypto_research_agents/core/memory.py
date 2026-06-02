from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4
from urllib.parse import urlparse, urlunparse

from .time import utc_now


@dataclass(slots=True)
class SourceRecord:
    title: str
    content: str
    source_type: str = "article"
    url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    content_hash: str | None = None
    canonical_url: str | None = None
    captured_at: str = field(default_factory=utc_now)
    raw_path: str | None = None
    source_quality_score: float = 0.5
    source_id: str = field(default_factory=lambda: f"src_{uuid4().hex[:10]}")
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.content_hash is None:
            self.content_hash = _content_hash(self.content)
        if self.canonical_url is None and self.url:
            self.canonical_url = _canonicalize_url(self.url)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ProjectCandidate:
    name: str
    reason_found: str
    project_id: str = field(default_factory=lambda: f"proj_{uuid4().hex[:10]}")
    website: str | None = None
    x_account: str | None = None
    chain: str | None = None
    token_status: str = "unknown"
    narratives: list[str] = field(default_factory=list)
    score: float = 0.0
    sources: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FindingRecord:
    room_id: str
    agent_id: str
    finding_type: str
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    confidence: float = 0.5
    finding_id: str = field(default_factory=lambda: f"finding_{uuid4().hex[:10]}")
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SharedMemory:
    """Simple in-memory store. Persistence is handled by storage/json_store.py."""

    def __init__(self) -> None:
        self.sources: dict[str, SourceRecord] = {}
        self.projects: dict[str, ProjectCandidate] = {}
        self.findings: dict[str, FindingRecord] = {}
        self.entity_graph: dict[str, set[str]] = {}

    def add_source(self, source: SourceRecord) -> SourceRecord:
        existing = self.find_duplicate_source(source)
        if existing is not None:
            existing.metadata.update({key: value for key, value in source.metadata.items() if key not in existing.metadata})
            return existing
        self.sources[source.source_id] = source
        return source

    def find_duplicate_source(self, source: SourceRecord) -> SourceRecord | None:
        for existing in self.sources.values():
            if source.canonical_url and existing.canonical_url == source.canonical_url:
                return existing
            if source.content.strip() and existing.content_hash == source.content_hash:
                return existing
        return None

    def upsert_project(self, project: ProjectCandidate) -> ProjectCandidate:
        self.projects[project.project_id] = project
        for narrative in project.narratives:
            self.link_entity(project.name, narrative)
        return project

    def add_finding(self, finding: FindingRecord) -> FindingRecord:
        self.findings[finding.finding_id] = finding
        return finding

    def link_entity(self, left: str, right: str) -> None:
        self.entity_graph.setdefault(left, set()).add(right)
        self.entity_graph.setdefault(right, set()).add(left)

    def get_room_findings(self, room_id: str) -> list[FindingRecord]:
        return [finding for finding in self.findings.values() if finding.room_id == room_id]

    def search_sources(self, query: str, limit: int = 5) -> list[SourceRecord]:
        tokens = _tokens(query)
        scored: list[tuple[int, SourceRecord]] = []
        for source in self.sources.values():
            haystack = f"{source.title} {source.content}".lower()
            score = sum(1 for token in tokens if token in haystack)
            if score:
                scored.append((score, source))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [source for _, source in scored[:limit]]

    def search_projects(self, query: str, limit: int = 10) -> list[ProjectCandidate]:
        tokens = _tokens(query)
        scored: list[tuple[int, ProjectCandidate]] = []
        for project in self.projects.values():
            haystack = " ".join(
                [
                    project.name,
                    project.reason_found,
                    project.chain or "",
                    project.token_status,
                    " ".join(project.narratives),
                ]
            ).lower()
            score = sum(1 for token in tokens if token in haystack)
            if score:
                scored.append((score, project))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [project for _, project in scored[:limit]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sources": {key: value.to_dict() for key, value in self.sources.items()},
            "projects": {key: value.to_dict() for key, value in self.projects.items()},
            "findings": {key: value.to_dict() for key, value in self.findings.items()},
            "entity_graph": {key: sorted(value) for key, value in self.entity_graph.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SharedMemory":
        memory = cls()
        for source_data in data.get("sources", {}).values():
            source = SourceRecord(**source_data)
            memory.sources[source.source_id] = source
        for project_data in data.get("projects", {}).values():
            project = ProjectCandidate(**project_data)
            memory.projects[project.project_id] = project
        for finding_data in data.get("findings", {}).values():
            finding = FindingRecord(**finding_data)
            memory.findings[finding.finding_id] = finding
        memory.entity_graph = {
            key: set(values) for key, values in data.get("entity_graph", {}).items()
        }
        return memory


def _tokens(text: str) -> set[str]:
    return {part.strip(".,:;!?()[]{}").lower() for part in text.split() if len(part) > 2}


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _canonicalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return url.strip()
    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        fragment="",
    )
    return urlunparse(normalized).rstrip("/")
