from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from crypto_research_agents.core.company_settings import CompanySettings


@dataclass(slots=True)
class SupervisorIntakeDecision:
    """The company president's first-pass routing decision for a chat input."""

    intent_type: str
    action: str
    output_mode: str
    needs_research_room: bool
    confidence: float
    rationale: str
    next_step: str
    supervisor_authority: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decide_supervisor_intake(line: str, settings: CompanySettings | None = None) -> SupervisorIntakeDecision:
    settings = settings or CompanySettings()
    stripped = line.strip()
    lowered = stripped.lower()
    authority = list(settings.supervisor_authority)

    if not stripped:
        return SupervisorIntakeDecision(
            intent_type="empty",
            action="ignore",
            output_mode="none",
            needs_research_room=False,
            confidence=1.0,
            rationale="Empty input.",
            next_step="Wait for the next client instruction.",
            supervisor_authority=authority,
        )

    if _looks_like_company_status_request(stripped, lowered):
        return SupervisorIntakeDecision(
            intent_type="company_status",
            action="show_company_settings",
            output_mode="settings_panel",
            needs_research_room=False,
            confidence=0.82,
            rationale="The client is asking to inspect the company or current operating state.",
            next_step="Show settings, routing policy, and supervisor authority.",
            supervisor_authority=authority,
        )

    if _looks_like_source_only_request(stripped, lowered):
        return SupervisorIntakeDecision(
            intent_type="source_ingestion",
            action="run_source_ingestion",
            output_mode="source_note",
            needs_research_room=True,
            confidence=0.78,
            rationale="The client wants material stored or remembered without a full dossier.",
            next_step="Open a small source-ingestion room with ingestion and Obsidian curation.",
            supervisor_authority=authority,
        )

    if stripped.startswith(("http://", "https://")):
        return SupervisorIntakeDecision(
            intent_type="research_request",
            action="open_research_room",
            output_mode="research_dossier",
            needs_research_room=True,
            confidence=0.76,
            rationale="A standalone URL is treated as source material for research.",
            next_step="Fetch the URL and dispatch the research agents.",
            supervisor_authority=authority,
        )

    has_config = _has_any(stripped, lowered, COMPANY_CONFIG_TERMS) or _is_meta_instruction(stripped)
    has_research = _has_any(stripped, lowered, RESEARCH_TERMS)
    has_explicit_research_action = _has_any(stripped, lowered, EXPLICIT_RESEARCH_ACTIONS)

    if has_config and not has_explicit_research_action:
        return SupervisorIntakeDecision(
            intent_type="company_config",
            action="apply_company_instruction",
            output_mode="settings_update",
            needs_research_room=False,
            confidence=0.86,
            rationale="The client is changing how the company should operate, not asking for a dossier.",
            next_step="Persist the instruction in company settings.",
            supervisor_authority=authority,
        )

    if has_config and has_research and _is_meta_instruction(stripped):
        return SupervisorIntakeDecision(
            intent_type="company_config",
            action="apply_company_instruction",
            output_mode="settings_update",
            needs_research_room=False,
            confidence=0.8,
            rationale="The instruction talks about report behavior or routing policy rather than a target to investigate.",
            next_step="Persist the policy and avoid opening a Research Room.",
            supervisor_authority=authority,
        )

    if has_research or has_explicit_research_action:
        return SupervisorIntakeDecision(
            intent_type="research_request",
            action="open_research_room",
            output_mode="research_dossier",
            needs_research_room=True,
            confidence=0.84,
            rationale="The client explicitly asked for research, analysis, investigation, or a report.",
            next_step="Open a full Research Room and assign specialist agents.",
            supervisor_authority=authority,
        )

    return SupervisorIntakeDecision(
        intent_type="company_config",
        action="apply_company_instruction",
        output_mode="settings_update",
        needs_research_room=False,
        confidence=0.62,
        rationale="No explicit research action was found; defaulting to company-level instruction handling.",
        next_step="Save the instruction so the company can adapt before future research rooms.",
        supervisor_authority=authority,
    )


COMPANY_CONFIG_TERMS = [
    "설정",
    "변경",
    "바꿔",
    "고쳐",
    "수정",
    "반영",
    "업데이트",
    "앞으로",
    "항상",
    "기본",
    "그러지말고",
    "하지말고",
    "아닐경우",
    "경우엔",
    "보고서는",
    "리포트는",
    "레포트는",
    "한글로",
    "한국어",
    "영어단어",
    "슈퍼바이저",
    "supervisor",
    "사장",
    "대표",
    "ceo",
    "외주",
    "권한",
    "역할",
    "로그",
    "테마",
    "색상",
    "보여주",
    "나오게",
    "작동하게",
    "출력",
    "입력",
    "응답",
]

RESEARCH_TERMS = [
    "조사",
    "리서치",
    "분석",
    "보고서",
    "리포트",
    "레포트",
    "research",
    "analyze",
    "analyse",
    "investigate",
    "report",
]

EXPLICIT_RESEARCH_ACTIONS = [
    "조사해",
    "조사 진행",
    "리서칭",
    "리서치해",
    "분석해",
    "보고서 만들어",
    "보고서를 만들어",
    "리포트 만들어",
    "레포트 만들어",
    "research",
    "analyze",
    "investigate",
]

META_INSTRUCTION_TERMS = [
    "모든 부분",
    "그러지말고",
    "아닐경우",
    "설정 변경",
    "자체 반영",
    "역할",
    "광범위",
    "사장",
    "대표",
    "외주",
    "회사에다가",
    "권한",
    "출력하는게 달라",
]

COMPANY_STATUS_TERMS = [
    "현재 설정",
    "설정 보여",
    "회사 상태",
    "상태 보여",
    "누가 뭘",
    "누가 무엇",
    "뭘 할 수",
    "뭐 할 수",
    "도움말",
    "사용법",
    "settings",
    "company status",
    "agents",
]

SOURCE_ONLY_TERMS = [
    "저장만",
    "소스만",
    "기억만",
    "노트만",
    "add source",
    "ingest only",
    "source only",
]


def _has_any(original: str, lowered: str, terms: list[str]) -> bool:
    return any(term in lowered for term in terms) or any(term in original for term in terms)


def _is_meta_instruction(text: str) -> bool:
    return any(term in text for term in META_INSTRUCTION_TERMS)


def _looks_like_company_status_request(original: str, lowered: str) -> bool:
    return _has_any(original, lowered, COMPANY_STATUS_TERMS)


def _looks_like_source_only_request(original: str, lowered: str) -> bool:
    return _has_any(original, lowered, SOURCE_ONLY_TERMS)
