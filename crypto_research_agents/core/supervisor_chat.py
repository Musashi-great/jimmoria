from __future__ import annotations

import json

from crypto_research_agents.core.company_settings import CompanySettings
from crypto_research_agents.core.model_gateway import ModelGateway
from crypto_research_agents.core.supervisor_intake import SupervisorIntakeDecision


def generate_supervisor_chat_reply(
    line: str,
    settings: CompanySettings,
    decision: SupervisorIntakeDecision,
    *,
    history: list[dict[str, str]] | None = None,
    memory_context: list[str] | None = None,
    session_context: list[str] | None = None,
    model_gateway: ModelGateway | None = None,
) -> list[str]:
    """Answer as the front-door Hermes Agent without opening a Research Room."""

    gateway = model_gateway or ModelGateway()
    provider_name = getattr(gateway.provider, "provider_name", "")
    if provider_name != "offline_fallback":
        try:
            response = gateway.complete(
                agent_id="supervisor_agent",
                task_type="supervisor_chat",
                system_prompt=supervisor_chat_system_prompt(settings),
                user_prompt=supervisor_chat_user_prompt(
                    line,
                    settings,
                    decision,
                    history or [],
                    memory_context=memory_context or [],
                    session_context=session_context or [],
                ),
            )
            text = response.text.strip()
            if text:
                return split_reply_lines(text)
        except Exception:
            pass

    return fallback_supervisor_chat_reply(line, settings)


def supervisor_chat_system_prompt(settings: CompanySettings) -> str:
    return "\n".join(
        [
            "You are JIMMORIA Hermes Agent, the personal-agent harness, memory keeper, and central orchestrator for the user.",
            "The internal compatibility id may still be supervisor_agent, but your user-facing identity is Hermes Agent.",
            "The user is the owner/operator, not a client. Talk naturally and directly like a capable personal agent with specialist subroutines.",
            "When relevant, route through Honcho memory, Obsidian vault, QMD local search, CDP browser harness, Tavily search, Codex, and specialist research agents.",
            "Do not sound like a classifier, router, or log system.",
            "Do not mention hidden intent labels unless the user asks about internals.",
            "Do not open or promise a full report unless the user explicitly asks for research, analysis, investigation, or report generation.",
            "If the user is casually chatting or asking a follow-up, answer conversationally and briefly.",
            "If the user asks what reports contain, explain the report structure and how agents contribute.",
            "If the user asks about current settings, answer from the settings context.",
            "Korean is preferred for conversation. English crypto/technical terms may be used when natural.",
            f"Current report_language: {settings.report_language}",
            f"English technical terms allowed: {settings.allow_english_terms}",
            f"Hermes mode: {settings.supervisor_mode}",
            f"Client relationship: {settings.client_relationship}",
        ]
    )


def supervisor_chat_user_prompt(
    line: str,
    settings: CompanySettings,
    decision: SupervisorIntakeDecision,
    history: list[dict[str, str]],
    *,
    memory_context: list[str] | None = None,
    session_context: list[str] | None = None,
) -> str:
    history_tail = history[-8:]
    payload = {
        "user_message": line,
        "recent_dialogue": history_tail,
        "supervisor_memory": memory_context or [],
        "session_context": session_context or [],
        "settings": settings.to_dict(),
        "internal_decision": decision.to_dict(),
        "instruction": (
            "Reply as Hermes Agent in 1-5 concise Korean sentences. "
            "Use supervisor_memory and session_context for continuity, but do not expose hidden labels. "
            "If the user gives executable work, explain that you will dispatch specialist subroutines through the personal-agent stack."
        ),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def fallback_supervisor_chat_reply(line: str, settings: CompanySettings) -> list[str]:
    lowered = line.lower()
    compact = line.strip().lower()

    if compact in {"안녕", "안녕하세요", "하이", "ㅎㅇ", "hello", "hi", "hey"}:
        return [
            "안녕. 나는 JIMMORIA Hermes Agent야.",
            "나한테 편하게 말하면 돼. 리서치가 필요하면 방을 열고, 설정이나 방향 이야기면 여기서 바로 정리할게.",
        ]

    if "보고서" in line or "리포트" in line or "레포트" in line or "report" in lowered:
        if any(term in line for term in ["뭐", "무엇", "구성", "들어", "씀", "써", "어떤"]):
            return [
                "보고서는 보통 TL;DR, 핵심 판단, 소스 요약, 내러티브, 후보 프로젝트, 소셜/KOL, 온체인/컨트랙트, 제품/Docs/GitHub, funding/token 단서, 남은 질문으로 구성해.",
                "각 섹션은 내가 방향을 잡고 전문 에이전트들이 근거를 채운 다음, report_agent가 사람이 읽을 수 있는 dossier로 정리하는 방식이야.",
            ]
        if settings.report_language == "ko":
            return ["응, 현재 보고서는 한국어 우선으로 작성되도록 설정돼 있어. crypto 용어는 필요하면 영어 그대로 섞어서 쓰는 정책이야."]
        return [f"아직 한국어 우선은 아니야. 현재 report_language는 `{settings.report_language}`이고, 바꾸려면 '보고서는 한글로 만들어'라고 말하면 돼."]

    if any(term in line for term in ["Hermes", "hermes", "헤르메스", "슈퍼바이저", "사장", "보스", "대표", "권한", "외주"]):
        return [
            "맞아. 나는 JIMMORIA의 Hermes Agent, 개인 에이전트 하네스이자 중앙 오케스트라야.",
            "너는 owner/operator로 편하게 지시하면 되고, 나는 필요한 경우에만 기억·검색·브라우저·코덱스·전문 에이전트를 묶어서 실행할게.",
        ]

    if any(term in line for term in ["한글", "한국어", "세팅", "설정"]):
        return [
            f"현재 report_language는 `{settings.report_language}`야.",
            "한국어 우선으로 바꾸고 싶으면 '앞으로 보고서는 한글 위주로 만들어'라고 말하면 설정으로 반영할게.",
        ]

    return [
        "응, 편하게 말해도 돼.",
        "리서치로 넘길지, 설정으로 반영할지, 그냥 나랑 이야기할지는 내가 먼저 판단해서 처리할게.",
    ]


def split_reply_lines(text: str) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return []
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    return lines or [cleaned]
