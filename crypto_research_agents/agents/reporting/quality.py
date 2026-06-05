from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ReportQuality:
    status: str
    evidence_url_count: int
    candidate_count: int
    live_source_backed_count: int
    placeholder_count: int
    reasons: list[str]

    @property
    def is_blocking(self) -> bool:
        return self.status == "insufficient_evidence"

    @property
    def placeholder_only(self) -> bool:
        return self.candidate_count > 0 and self.placeholder_count == self.candidate_count

    @property
    def has_live_source_backed(self) -> bool:
        return self.live_source_backed_count > 0

    def result_summary(self, report_path: Path) -> str:
        if self.is_blocking:
            return f"Research gate blocked completed report: insufficient source-backed evidence. Diagnostic memo written to {report_path}"
        return f"Report written to {report_path}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "evidence_url_count": self.evidence_url_count,
            "candidate_count": self.candidate_count,
            "live_source_backed_count": self.live_source_backed_count,
            "placeholder_count": self.placeholder_count,
            "placeholder_only": self.placeholder_only,
            "has_live_source_backed": self.has_live_source_backed,
            "reasons": self.reasons,
        }


def assess_report_quality(candidates: list[Any]) -> ReportQuality:
    evidence_url_count = 0
    placeholder_count = 0
    live_source_backed_count = 0
    for project in candidates:
        metadata = project.metadata if isinstance(project.metadata, dict) else {}
        evidence_urls = metadata.get("evidence_urls", [])
        if isinstance(evidence_urls, list):
            evidence_url_count += len([url for url in evidence_urls if url])
        origin = candidate_origin(project)
        if origin == "mvp_placeholder":
            placeholder_count += 1
        if origin == "live_source_backed":
            live_source_backed_count += 1

    reasons: list[str] = []
    if not candidates:
        reasons.append("no candidate project was resolved")
    if candidates and placeholder_count == len(candidates):
        reasons.append("all candidates are MVP placeholders")
    if evidence_url_count == 0 and live_source_backed_count == 0:
        reasons.append("no source-backed evidence URLs were collected")

    status = "insufficient_evidence" if reasons else "research_complete"
    return ReportQuality(
        status=status,
        evidence_url_count=evidence_url_count,
        candidate_count=len(candidates),
        live_source_backed_count=live_source_backed_count,
        placeholder_count=placeholder_count,
        reasons=reasons,
    )


def candidate_origin(project: Any) -> str:
    metadata = project.metadata if isinstance(project.metadata, dict) else {}
    origin = metadata.get("candidate_origin")
    if origin:
        return str(origin)
    if metadata.get("discovery_mode") == "live_search":
        return "live_source_backed"
    if metadata.get("mvp_generated"):
        return "mvp_placeholder"
    return "unknown"
