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

    if _looks_like_small_talk(line.strip(), lowered):
        lines = [
            "안녕하세요. JIMMORIA Supervisor입니다.",
            "리서치 지시, 회사 설정 변경, 상태 확인 중 무엇을 원하는지 말해주면 제가 먼저 분류해서 처리하겠습니다.",
        ]
    elif any(term in line for term in ["보고서", "리포트", "레포트", "한글", "한국어", "세팅", "설정"]):
        lines = []
        if report_language == "ko":
            lines.append("맞습니다. 현재 보고서 출력은 한국어 우선 흐름으로 설정되어 있습니다.")
        else:
            lines.append(f"아직 한국어 우선은 아닙니다. 현재 report_language는 `{report_language}`입니다.")
        lines.append(f"영어 기술 용어 정책은 `{terms_policy}`입니다.")
    elif any(term in line for term in ["슈퍼바이저", "사장", "대표", "권한", "외주"]):
        lines = [
            "Supervisor는 현재 모든 일반 채팅 입력을 먼저 받고 출력 모드를 결정합니다.",
            f"현재 supervisor_mode는 `{settings.supervisor_mode}`입니다.",
        ]
    elif "report" in lowered:
        lines = ["명시적으로 조사/분석/보고서 작성을 요청할 때만 전체 Research Room을 엽니다."]
    else:
        lines = ["아직 구체적인 작업 지시는 아닌 것 같습니다. 리서치, 설정 변경, 상태 확인 중 원하는 방향을 말해주면 제가 바로 이어서 처리하겠습니다."]
    return lines


def build_company_instruction_reply(
    applied: list[str],
    settings: CompanySettings,
    settings_path: object,
) -> list[str]:
    lines = [
        "좋습니다. 이건 리서치 방을 열 일이 아니라 회사 운영 지시로 보고 바로 반영했습니다.",
        f"설정 파일: {settings_path}",
        "",
        "반영한 내용:",
    ]
    lines.extend(f"- {item}" for item in applied)
    lines.extend(
        [
            "",
            f"현재 보고서 언어: {settings.report_language}",
            f"현재 Supervisor mode: {settings.supervisor_mode}",
        ]
    )
    return lines


def build_supervisor_dispatch_reply(decision: SupervisorIntakeDecision, agent_count: int) -> list[str]:
    if decision.intent_type == "source_ingestion":
        return [
            "좋습니다. 이건 전체 리서치가 아니라 소스 저장 작업으로 처리하겠습니다.",
            "작은 Research Room을 열고 Ingestion과 Obsidian 정리만 실행하겠습니다.",
        ]
    return [
        "좋습니다. 이건 리서치 요청으로 판단했습니다.",
        f"Research Room을 열고 {agent_count}개 에이전트에게 작업을 배정하겠습니다.",
        "제가 먼저 목표와 우선순위를 잡고, 이후 각 전문 에이전트가 조사에 들어갑니다.",
    ]


def build_company_status_reply(settings: CompanySettings) -> list[str]:
    return [
        "현재 회사 설정을 보여드리겠습니다.",
        f"Supervisor mode는 `{settings.supervisor_mode}`이고, 보고서 언어는 `{settings.report_language}`입니다.",
    ]


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

    if _looks_like_small_talk(stripped, lowered):
        return SupervisorIntakeDecision(
            intent_type="supervisor_chat",
            action="answer_directly",
            output_mode="supervisor_reply",
            needs_research_room=False,
            confidence=0.9,
            rationale="The client is greeting or casually talking to the Supervisor.",
            next_step="Reply directly and wait for a concrete research, settings, or status request.",
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

    if _looks_like_report_retrieval_request(stripped, lowered):
        return SupervisorIntakeDecision(
            intent_type="report_retrieval",
            action="show_existing_report",
            output_mode="saved_report",
            needs_research_room=False,
            confidence=0.86,
            rationale="The client is asking to retrieve an existing report, not create a new dossier.",
            next_step="Find the saved report by room id, topic, or report filename and print it.",
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
        intent_type="supervisor_chat",
        action="ask_for_direction",
        output_mode="supervisor_reply",
        needs_research_room=False,
        confidence=0.62,
        rationale="No explicit research, settings, source, or status action was found.",
        next_step="Ask the client what kind of work should be done before opening a room or saving settings.",
        supervisor_authority=authority,
    )


COMPANY_CONFIG_TERMS = [
    "설정",
    "세팅",
    "선택",
    "추가",
    "변경",
    "바꿔",
    "고쳐",
    "수정",
    "반영",
    "업데이트",
    "앞으로",
    "항상",
    "해야지",
    "되게",
    "처럼",
    "느낌",
    "개선",
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

SMALL_TALK_TERMS = [
    "안녕",
    "안녕하세요",
    "하이",
    "ㅎㅇ",
    "hello",
    "hi",
    "hey",
    "yo",
    "반가워",
    "고마워",
    "감사",
    "thanks",
    "thank you",
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

REPORT_RETRIEVAL_TERMS = [
    "보내봐",
    "보내줘",
    "보여줘",
    "꺼내줘",
    "열어줘",
    "출력해줘",
    "들고와",
    "들고와봐",
    "들고 와",
    "들고 와봐",
    "가져와",
    "가져와봐",
    "가져 와",
    "가져 와봐",
    "가지고와",
    "가지고와봐",
    "가지고 와",
    "가지고 와봐",
    "갖고와",
    "갖고와봐",
    "갖고 와",
    "갖고 와봐",
    "불러와",
    "불러와봐",
    "불러 와",
    "불러 와봐",
    "찾아줘",
    "찾아봐",
    "내놔",
    "줘",
    "전체",
    "전부",
    "풀버전",
    "보내봐",
    "보내줘",
    "보여줘",
    "꺼내줘",
    "열어줘",
    "출력해줘",
    "전체",
    "전부",
    "풀버전",
    "show",
    "send",
    "print",
    "view",
    "open",
    "full",
]

REPORT_CREATE_TERMS = [
    "만들어",
    "작성해",
    "생성해",
    "새로",
    "만들어",
    "작성해",
    "생성해",
    "새로",
    "create",
    "write",
    "generate",
]

REPORT_NEW_RESEARCH_TERMS = [
    "새로",
    "신규",
    "다시",
    "업데이트",
    "최신",
    "조사",
    "리서치",
    "리서칭",
    "분석",
    "research",
    "analyze",
    "analyse",
    "investigate",
    "fresh",
    "new",
    "update",
]


def _has_any(original: str, lowered: str, terms: list[str]) -> bool:
    return any(term in lowered for term in terms) or any(term in original for term in terms)


def _is_meta_instruction(text: str) -> bool:
    return any(term in text for term in META_INSTRUCTION_TERMS)


def _looks_like_company_status_request(original: str, lowered: str) -> bool:
    return _has_any(original, lowered, COMPANY_STATUS_TERMS)


def _looks_like_source_only_request(original: str, lowered: str) -> bool:
    return _has_any(original, lowered, SOURCE_ONLY_TERMS)


def _looks_like_report_retrieval_request(original: str, lowered: str) -> bool:
    if _has_any(original, lowered, COMPANY_CONFIG_TERMS) or _is_meta_instruction(original):
        return False
    normal_report_words = ["보고서", "리포트", "레포트", "report"]
    if _has_any(original, lowered, normal_report_words):
        if _has_any(original, lowered, REPORT_CREATE_TERMS):
            return not _has_any(original, lowered, REPORT_NEW_RESEARCH_TERMS)
        if _has_any(original, lowered, REPORT_RETRIEVAL_TERMS):
            return True
    report_words = ["보고서", "리포트", "레포트", "report"]
    if not _has_any(original, lowered, report_words):
        return False
    if _has_any(original, lowered, REPORT_CREATE_TERMS):
        return not _has_any(original, lowered, REPORT_NEW_RESEARCH_TERMS)
    return _has_any(original, lowered, REPORT_RETRIEVAL_TERMS)


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


def _looks_like_small_talk(original: str, lowered: str) -> bool:
    compact = original.strip().lower()
    if compact in SMALL_TALK_TERMS:
        return True
    if len(compact) <= 12 and _has_any(original, lowered, SMALL_TALK_TERMS):
        return True
    return False
