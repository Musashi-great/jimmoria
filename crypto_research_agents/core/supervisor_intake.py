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


def build_supervisor_reply(
    line: str,
    settings: CompanySettings,
    decision: SupervisorIntakeDecision,
) -> list[str]:
    """Build a direct CEO-style reply when no agent room is needed."""

    lowered = line.lower()
    report_language = settings.report_language
    terms_policy = "allowed" if settings.allow_english_terms else "restricted"
    lines = [
        "Research Room은 열지 않았습니다.",
        "이 입력은 리서치 지시가 아니라 슈퍼바이저에게 하는 확인/대화로 처리했습니다.",
    ]

    if any(term in line for term in ["보고서", "리포트", "레포트", "한글", "한국어", "세팅", "설정"]):
        if report_language == "ko":
            lines.append("맞습니다. 현재 보고서 출력은 한국어 우선 흐름으로 설정되어 있습니다.")
        else:
            lines.append(f"아직 한국어 우선은 아닙니다. 현재 report_language는 `{report_language}`입니다.")
        lines.append(f"영어 기술 용어 정책은 `{terms_policy}`입니다.")
    elif any(term in line for term in ["슈퍼바이저", "사장", "대표", "권한", "외주"]):
        lines.append("Supervisor는 현재 모든 일반 채팅 입력을 먼저 받고 출력 모드를 결정합니다.")
        lines.append(f"현재 supervisor_mode는 `{settings.supervisor_mode}`입니다.")
    elif "report" in lowered:
        lines.append("명시적으로 조사/분석/보고서 작성을 요청할 때만 전체 Research Room을 엽니다.")
    else:
        lines.append("필요하면 이 내용을 회사 운영 설정으로 저장할 수도 있고, 리서치 지시로 바꿔 Research Room을 열 수도 있습니다.")

    lines.extend(
        [
            "",
            f"Intent: {decision.intent_type}",
            f"Output mode: {decision.output_mode}",
            f"Reason: {decision.rationale}",
        ]
    )
    return lines


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

    if _looks_like_supervisor_chat(stripped, lowered):
        return SupervisorIntakeDecision(
            intent_type="supervisor_chat",
            action="answer_directly",
            output_mode="supervisor_reply",
            needs_research_room=False,
            confidence=0.84,
            rationale="The client is asking the Supervisor a confirmation or operating question.",
            next_step="Reply directly without opening a Research Room or writing a report.",
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

    if _looks_like_supervisor_chat(stripped, lowered) and not has_explicit_research_action:
        return SupervisorIntakeDecision(
            intent_type="supervisor_chat",
            action="answer_directly",
            output_mode="supervisor_reply",
            needs_research_room=False,
            confidence=0.8,
            rationale="The input is phrased as a conversation with the Supervisor rather than a task dispatch.",
            next_step="Answer as the company president and keep the room closed.",
            supervisor_authority=authority,
        )

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
    "세팅",
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
    "보고서 작성",
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
    "현재 세팅",
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

SUPERVISOR_CHAT_TERMS = [
    "맞지",
    "맞아",
    "맞나요",
    "맞냐",
    "맞습니까",
    "확인",
    "지금",
    "현재",
    "세팅된",
    "세팅된게",
    "설정된",
    "설정된게",
    "되어있",
    "돼있",
    "왜",
    "어떻게",
    "뭐야",
    "뭔데",
    "가능해",
    "가능한",
    "?",
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


def _looks_like_supervisor_chat(original: str, lowered: str) -> bool:
    if _has_any(original, lowered, EXPLICIT_RESEARCH_ACTIONS):
        return False
    if not _has_any(original, lowered, SUPERVISOR_CHAT_TERMS):
        return False
    if _has_any(
        original,
        lowered,
        [
            "보고서",
            "리포트",
            "레포트",
            "설정",
            "세팅",
            "슈퍼바이저",
            "사장",
            "대표",
            "권한",
            "company",
            "settings",
            "report",
        ],
    ):
        return True
    return original.endswith("?")
