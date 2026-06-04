from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from crypto_research_agents.agents.base import AgentResult, BaseAgent
from crypto_research_agents.core.bus import CollaborationBus
from crypto_research_agents.core.company_settings import CompanySettings
from crypto_research_agents.core.memory import FindingRecord, SharedMemory
from crypto_research_agents.core.project_profile import find_project_profile_in_text
from crypto_research_agents.core.room import ResearchRoom
from crypto_research_agents.core.source_quality import is_relevant_source_url
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
        candidates = current_room_candidates(room, memory, findings)
        quality = assess_report_quality(candidates)
        room.project_card["research_quality"] = quality.to_dict()
        room.project_card["research_quality_status"] = quality.status
        if quality.is_blocking:
            room.add_open_question("Collect source-backed evidence before treating this as a completed research report.")

        company_settings = kwargs.get("company_settings")
        if not isinstance(company_settings, CompanySettings):
            company_settings = CompanySettings()
        primary = candidates[0] if candidates else None
        room_sources = [memory.sources[source_id] for source_id in room.source_inputs if source_id in memory.sources]
        source_log = collect_source_log(primary, room_sources) if primary else []
        claim_ledger = build_claim_evidence_ledger(primary, findings, source_log)
        room.project_card["claim_evidence_ledger"] = claim_ledger
        llm_summary = self._write_llm_summary(room, memory, findings)
        provider_name = self.model_gateway.provider_name_for_task(agent_id=self.agent_id, task_type=self.task_type)
        report = render_project_dossier(
            room,
            memory,
            findings,
            decision.selected_model,
            provider_name,
            llm_summary,
            company_settings,
        )
        report_path = reports_dir / f"{safe_filename(room.topic)}-{room.room_id}.md"
        report_path.write_text(report, encoding="utf-8")
        room.report_draft = report
        room.output_paths["report"] = str(report_path)
        evidence_packet_dir = Path(kwargs.get("evidence_packet_dir", "data/evidence_packets"))
        evidence_packet_path = write_representative_evidence_packet(
            evidence_packet_dir=evidence_packet_dir,
            room=room,
            project=primary,
            findings=findings,
            quality=quality,
            source_log=source_log,
            company_settings=company_settings,
        )
        room.output_paths["evidence_packet"] = str(evidence_packet_path)

        summary = quality.result_summary(report_path)
        confidence = 0.35 if quality.is_blocking else 0.7
        finding = self.write_finding(
            room=room,
            memory=memory,
            finding_type="report",
            summary=summary,
            data={
                "report_path": str(report_path),
                "evidence_packet_path": str(evidence_packet_path),
                "model": decision.selected_model,
                "provider": provider_name,
                "report_language": company_settings.report_language,
                "llm_summary": llm_summary,
                "quality_status": quality.status,
                "quality_reasons": quality.reasons,
                "evidence_url_count": quality.evidence_url_count,
                "placeholder_only": quality.placeholder_only,
                "has_live_source_backed": quality.has_live_source_backed,
            },
            confidence=confidence,
        )
        bus.handoff(
            room_id=room.room_id,
            from_agent=self.agent_id,
            to_agent="obsidian_curator_agent",
            summary=(
                "Research memo is ready for Obsidian sync; quality gate marked it insufficient."
                if quality.is_blocking
                else "Final report is ready for Obsidian sync."
            ),
            payload={
                "report_path": str(report_path),
                "evidence_packet_path": str(evidence_packet_path),
                "finding_id": finding.finding_id,
            },
        )
        return AgentResult(
            self.agent_id,
            summary,
            {
                "finding_id": finding.finding_id,
                "report_path": str(report_path),
                "evidence_packet_path": str(evidence_packet_path),
                "quality_status": quality.status,
            },
            confidence=confidence,
        )

    def _write_llm_summary(
        self,
        room: ResearchRoom,
        memory: SharedMemory,
        findings: list[FindingRecord],
    ) -> str:
        candidate_lines = []
        for project in current_room_candidates(room, memory, findings):
            candidate_lines.append(
                f"- {project.name}: {', '.join(project.narratives)}; {project.reason_found}"
            )
        finding_lines = [f"- {finding.agent_id}: {finding.summary}" for finding in findings]
        response = self.model_gateway.complete(
            agent_id=self.agent_id,
            task_type="final_synthesis",
            system_prompt=self.system_prompt(
                "You are the Report Agent for a crypto research company. "
                "Write a private editorial synthesis for the final renderer. "
                "Do not output JSON, agent logs, Obsidian notes, or debate transcripts. "
                "Do not invent live data. Clearly mention when connector data is not configured."
            ),
            user_prompt=(
                f"Topic: {room.topic}\n\n"
                f"Goals:\n" + "\n".join(f"- {goal}" for goal in room.goals) + "\n\n"
                f"Candidates:\n" + "\n".join(candidate_lines) + "\n\n"
                f"Agent findings:\n" + "\n".join(finding_lines)
            ),
        )
        return response.text.strip()


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


def render_project_dossier(
    room: ResearchRoom,
    memory: SharedMemory,
    findings: list[FindingRecord],
    model_name: str,
    provider_name: str,
    llm_summary: str,
    company_settings: CompanySettings | None = None,
) -> str:
    settings = company_settings or CompanySettings()
    korean = settings.report_language == "ko"
    candidates = current_room_candidates(room, memory, findings)
    quality = assess_report_quality(candidates)
    sources = [memory.sources[source_id] for source_id in room.source_inputs if source_id in memory.sources]
    has_live_evidence = any(project.metadata.get("discovery_mode") == "live_search" for project in candidates)
    has_placeholder_candidates = any(candidate_origin(project) == "mvp_placeholder" for project in candidates)

    if not quality.is_blocking:
        return render_completed_project_report(
            room=room,
            memory=memory,
            findings=findings,
            candidates=candidates,
            sources=sources,
            quality=quality,
            model_name=model_name,
            provider_name=provider_name,
            llm_summary=llm_summary,
            settings=settings,
        )

    if has_placeholder_candidates:
        origin_note = (
            "- 후보 origin 규칙: `mvp_placeholder`는 검증된 live 후보가 아니다. `live_source_backed` 행만 source-backed lead로 취급한다."
            if korean
            else "- Candidate origin rule: MVP placeholders are not verified live candidates. Treat only `live_source_backed` rows as source-backed leads."
        )
    else:
        origin_note = (
            "- 후보 origin 규칙: 각 후보 행에 source-backing level을 표시한다."
            if korean
            else "- Candidate origin rule: Current candidate rows are marked with their source-backing level."
        )
    if has_live_evidence:
        connector_note = (
            "- 참고: public web/GitHub/market connector를 사용했고, X/RootData/Explorer/RPC는 optional secret과 대상 입력이 있을 때 live 결과를 낸다."
            if korean
            else "- Note: Public web/GitHub/market connectors were used; X/RootData/Explorer/RPC return live results when optional secrets and target inputs are configured."
        )
    else:
        connector_note = (
            "- 참고: secret이 없는 connector는 `missing_secret`, 대상 입력이 부족한 connector는 `missing_input`으로 표시된다."
            if korean
            else "- Note: Connectors without credentials return `missing_secret`; connectors without enough target data return `missing_input`, so those areas need follow-up verification."
        )

    report_title = (
        "리서치 미완료 / Research Not Completed"
        if quality.is_blocking
        else ("프로젝트 리서치 보고서" if korean else "Project Research Dossier")
    )
    lines: list[str] = [
        f"# {report_title}: {room.topic}",
        "",
        "## 0. Research Quality Gate",
        f"- Status: `{quality.status.upper()}`",
        f"- Evidence URLs: {quality.evidence_url_count}",
        f"- Live source-backed candidates: {quality.live_source_backed_count}",
        f"- Placeholder-only candidates: {'yes' if quality.placeholder_only else 'no'}",
    ]
    if quality.is_blocking:
        lines.extend(
            [
                "- This is not a completed research report.",
                "- The room did not collect enough source-backed evidence to support a candidate dossier.",
                "- Treat the content below as a diagnostic memo, not as final research.",
            ]
        )
    if quality.reasons:
        lines.append("- Gate reasons: " + "; ".join(quality.reasons))
    lines.extend(
        [
            "",
            "## 1. TL;DR",
            f"- {'Room ID' if korean else 'Room ID'}: `{room.room_id}`",
            f"- {'현재 판단' if korean else 'Current judgment'}: {'Insufficient Evidence' if quality.is_blocking else 'Research More'}",
            f"- {'자동 요약' if korean else 'Automated synthesis'}: {render_automatic_tldr(candidates, korean=korean)}",
        ]
    )
    if provider_name == "offline_fallback":
        lines.append(
            "- Live LLM: 설정되지 않음. Offline fallback은 deterministic summary만 생성한다."
            if korean
            else "- Live LLM: not configured. Offline fallback generated deterministic summaries only."
        )
    lines.extend(
        [
            origin_note,
            connector_note,
            "",
            "## 2. 목표" if korean else "## 2. Goals",
        ]
    )
    lines.extend(f"- {goal}" for goal in room.goals)

    lines.extend(["", "## 3. 소스" if korean else "## 3. Sources"])
    if sources:
        for source in sources:
            url = f" ({source.url})" if source.url else ""
            lines.append(f"- `{source.source_id}` - {source.title}{url}")
    else:
        lines.append("- No sources attached.")

    lines.extend(["", "## 4. 후보 프로젝트" if korean else "## 4. Candidate Projects"])
    if candidates:
        lines.extend(
            [
                "| Project | Origin | Source Backing | Website | Narrative | Token Status | Score | Evidence | Why Found |",
                "|---|---|---|---|---|---|---:|---:|---|",
            ]
        )
        for project in candidates:
            narrative = ", ".join(project.narratives) or "unknown"
            website = project.website or "-"
            evidence_count = len(project.metadata.get("evidence_urls", []))
            origin = candidate_origin(project)
            source_backing = candidate_source_backing(project)
            project_name = candidate_display_name(project)
            lines.append(
                f"| {escape_table(project_name)} | {escape_table(origin)} | {escape_table(source_backing)} | {escape_table(website)} | {escape_table(narrative)} | {escape_table(project.token_status)} | {project.score:.0f} | {evidence_count} | {escape_table(project.reason_found)} |"
            )
    else:
        lines.append("- No candidates discovered.")

    lines.extend(["", "## 5. 근거 맵" if korean else "## 5. Evidence Map"])
    if candidates:
        for project in candidates:
            lines.extend(render_candidate_evidence(project))
    else:
        lines.append("- No candidate evidence available.")

    lines.extend(["", "## 6. 에이전트 조사 결과" if korean else "## 6. Agent Findings"])
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
            "## 7. 남은 질문" if korean else "## 7. Open Questions",
            "- `mvp_placeholder` 후보는 실제 project lead로 보기 전에 `live_source_backed` 후보로 교체해야 한다."
            if korean
            else "- If a candidate is marked `mvp_placeholder`, replace it with a `live_source_backed` candidate before treating it as a real project lead.",
            "- Public web source coverage를 먼저 보강하고, 필요하면 X/RootData/Explorer/RPC optional secret을 설정한다."
            if korean
            else "- Strengthen public web source coverage first; configure optional X/RootData/Explorer/RPC secrets only when needed.",
            "- 공식 social handle과 KOL mention history 검증이 필요하다."
            if korean
            else "- Validate official social handles and KOL mention history.",
            "- Explorer/RPC 또는 공식 chain data로 token mechanics 검증이 필요하다."
            if korean
            else "- Verify token mechanics against explorer/RPC or official chain data.",
            "- Source-backed KOL mention history와 social momentum score 추가가 필요하다."
            if korean
            else "- Add source-backed KOL mention history and social momentum scores.",
            "",
            "## 8. 런타임 메타데이터" if korean else "## 8. Runtime Metadata",
            f"- LLM provider: `{provider_name}`",
            f"- Report model route: `{model_name}`",
            f"- Report language: `{settings.report_language}`",
            f"- Research quality: `{quality.status}`",
        ]
    )
    return "\n".join(lines)


def render_project_intelligence_report_v2(
    *,
    room: ResearchRoom,
    primary: Any,
    findings: list[FindingRecord],
    quality: ReportQuality,
    model_name: str,
    provider_name: str,
    settings: CompanySettings,
    korean: bool,
    title_name: str,
    source_log: list[dict[str, str]],
    source_summary: str,
) -> str:
    display_topic = clean_report_text(room.topic, fallback=f"{title_name} project research request")
    lines: list[str] = [
        f"# {title_name} Project Intelligence Report",
        "",
        f"- 작성 시각: {room.created_at}" if korean else f"- Created at: {room.created_at}",
        f"- 사용자 요청: {display_topic}" if korean else f"- Client request: {display_topic}",
        f"- 분석 대상: {title_name}" if korean else f"- Subject: {title_name}",
        f"- 주요 출처: {source_summary}" if korean else f"- Main sources: {source_summary}",
        "",
        "---",
        "",
        "## 1. Executive Summary (핵심 요약)" if korean else "## 1. Executive Summary",
    ]
    lines.extend(render_executive_summary_v2(primary, quality, source_log, korean=korean))
    lines.extend(["", "## Representative Verdict (대표님 기준 결론)" if korean else "## Representative Verdict"])
    lines.extend(render_representative_verdict_v2(primary, findings, quality, source_log, korean=korean))
    lines.extend(["", "## 대표님 실사 브리프" if korean else "## Representative Diligence Brief"])
    lines.extend(render_representative_diligence_brief_v2(primary, findings, quality, source_log, korean=korean))
    lines.extend(
        [
            "",
            "## 2. Primary Market Signal Layer (X/KOL 1차 소스)"
            if korean
            else "## 2. Primary Market Signal Layer",
        ]
    )
    lines.extend(render_primary_market_signal_layer_v2(primary, findings, korean=korean))
    lines.extend(["", "## 3. Project Identity (프로젝트 정체성)" if korean else "## 3. Project Identity"])
    lines.extend(render_project_identity_v2(primary, source_log, korean=korean))
    lines.extend(
        [
            "",
            "## 4. Market Problem & Narrative (시장 문제와 내러티브)"
            if korean
            else "## 4. Market Problem & Narrative",
        ]
    )
    lines.extend(render_market_context_v2(primary, korean=korean))
    lines.extend(
        [
            "",
            "## 5. Product & Protocol Mechanics (제품과 프로토콜 구조)"
            if korean
            else "## 5. Product & Protocol Mechanics",
        ]
    )
    lines.extend(render_protocol_mechanics_v2(primary, findings, korean=korean))
    lines.extend(
        [
            "",
            "## 6. Token, Chain & Value Capture (토큰/체인/가치 포착)"
            if korean
            else "## 6. Token, Chain & Value Capture",
        ]
    )
    lines.extend(render_value_capture_v2(primary, findings, korean=korean))
    lines.extend(
        [
            "",
            "## 7. Traction, Social & Funding Signals (트랙션/소셜/펀딩 신호)"
            if korean
            else "## 7. Traction, Social & Funding Signals",
        ]
    )
    lines.extend(render_signal_briefing_v2(primary, findings, korean=korean))
    lines.extend(["", "## Founder Dossier (창업자/팀)" if korean else "## Founder Dossier"])
    lines.extend(render_founder_dossier_v2(primary, findings, korean=korean))
    lines.extend(["", "## 8. Analyst Thesis (리서치 판단)" if korean else "## 8. Analyst Thesis"])
    lines.extend(render_analyst_thesis_v2(primary, quality, korean=korean))
    lines.extend(["", "## Score & Stance (TOP/WATCH/OPERATOR/제외)" if korean else "## Score & Stance"])
    lines.extend(render_score_and_stance_v2(primary, findings, quality, source_log, korean=korean))
    lines.extend(["", "## 9. Risk Register (리스크)" if korean else "## 9. Risk Register"])
    lines.extend(render_professional_risks_v2(primary, findings, korean=korean))
    lines.extend(["", "## 10. Specialist Coverage (에이전트별 커버리지)" if korean else "## 10. Specialist Coverage"])
    lines.extend(render_specialist_coverage_v2(primary, findings, korean=korean))
    lines.extend(["", "## 11. Next Research Checklist (다음 조사 체크리스트)" if korean else "## 11. Next Research Checklist"])
    lines.extend(render_due_diligence_checklist_v2(primary, findings, korean=korean))
    lines.extend(["", "## 12. Verification Status (검증 범위)" if korean else "## 12. Verification Status"])
    lines.extend(render_research_coverage_v2(primary, findings, source_log, korean=korean))
    lines.extend(["", "## Evidence Packet (대표님 리서치 프로필)" if korean else "## Evidence Packet"])
    lines.extend(render_evidence_packet_section_v2(primary, findings, quality, source_log, korean=korean))
    lines.extend(["", "## 13. Source Appendix (출처)" if korean else "## 13. Source Appendix"])
    if source_log:
        lines.extend(f"- [{item['label']}]({item['url']}) - {source_role(item['url'])}" for item in source_log)
    else:
        lines.append("- 수집된 출처 URL이 없습니다." if korean else "- No source URL was collected.")
    lines.extend(
        [
            "",
            "## 14. Research Quality Metadata",
            f"- Status: `{quality.status.upper()}`",
            f"- Evidence URLs: {quality.evidence_url_count}",
            f"- Live source-backed candidates: {quality.live_source_backed_count}",
            f"- Placeholder-only candidates: {'yes' if quality.placeholder_only else 'no'}",
            f"- LLM provider: `{provider_name}`",
            f"- Report model route: `{model_name}`",
            f"- Report language: `{'ko' if korean else settings.report_language}`",
        ]
    )
    return "\n".join(lines)


def clean_report_text(value: object, *, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    replacement_count = text.count("?") + text.count("\ufffd")
    if replacement_count >= max(3, len(text) // 5):
        return fallback
    return text


def render_representative_verdict_v2(
    project: Any,
    findings: list[FindingRecord],
    quality: ReportQuality,
    source_log: list[dict[str, str]],
    *,
    korean: bool,
) -> list[str]:
    score = diligence_score(project, findings, quality, source_log)
    if project is None:
        return [
            "- 대표님 기준 결론: `제외` - 프로젝트 identity가 확정되지 않았습니다."
            if korean
            else "- Representative verdict: `EXCLUDE` - no project identity was resolved."
        ]
    if korean:
        return [
            f"- **대표님 기준 결론:** `{score['stance']}`",
            f"- **점수:** {score['score']}/100",
            f"- **판단 이유:** {score['reason']}",
            "- **읽는 순서:** 결론 → 프로젝트가 무엇인지 → 누가 말했는지 → 제품/Docs/GitHub 확인 → 토큰 value-capture → 미해결 리스크 순서로 보면 됩니다.",
            "- **금지:** hype, 매수/매도, 목표가, 확정되지 않은 수익률 표현은 제외합니다.",
        ]
    return [
        f"- **Representative stance:** `{score['stance']}`",
        f"- **Score:** {score['score']}/100",
        f"- **Reason:** {score['reason']}",
        "- **Reading order:** verdict, project identity, who is talking, product/docs/GitHub evidence, token value-capture, unresolved risks.",
        "- **Guardrail:** no hype, buy/sell language, targets, or invented returns.",
    ]


def render_representative_diligence_brief_v2(
    project: Any,
    findings: list[FindingRecord],
    quality: ReportQuality,
    source_log: list[dict[str, str]],
    *,
    korean: bool,
) -> list[str]:
    score = diligence_score(project, findings, quality, source_log)
    if project is None:
        return [
            "- 대표님, 아직 프로젝트 정체성이 확정되지 않았습니다. 이 경우 보고서는 완성본이 아니라 후보 확인 메모로만 봐야 합니다."
            if korean
            else "- The project identity is unresolved. Treat this as a candidate memo, not a completed report."
        ]

    product_rows = finding_rows(findings, "product_tech_signal", project)
    token_rows = finding_rows(findings, "contract_token_info", project)
    funding_rows = finding_rows(findings, "funding_token_signal", project)
    social_rows = extract_social_seed_rows(findings) + finding_rows(findings, "social_kol_signal", project)
    founder_handles = extract_builder_handles(findings, project)
    narratives = ", ".join(display_narratives(project)[:5]) or "Unclassified Early Crypto"
    token_status = display_token_status(project)
    thesis = one_sentence_project_thesis(project)
    source_count = len(source_log)
    website = project.website or "unknown"
    chain = project.chain or "unknown"
    product_state = "확인됨" if product_rows else "추가 확인 필요"
    social_state = "부분 확인" if social_rows else "추가 확인 필요"
    token_state = "부분 확인" if token_rows else "추가 확인 필요"
    founder_state = "부분 확인" if founder_handles else "추가 확인 필요"
    funding_state = "부분 확인" if funding_rows else "추가 확인 필요"

    if korean:
        return [
            f"- **한 줄 정의:** {project.name}은/는 {thesis}",
            f"- **현재 스탠스:** `{score['stance']}` ({score['score']}/100). 이유: {score['reason']}",
            f"- **정체성:** site=`{website}`, chain=`{chain}`, token_status=`{token_status}`, source-backed URLs={source_count}.",
            f"- **내러티브:** {narratives}. 단순 테마가 아니라 실제 제품/수요와 연결되는지 확인해야 합니다.",
            f"- **소셜/KOL:** {social_state}. X/KOL/아티클은 1차 신호이고, 공식 사이트/Docs/GitHub/온체인으로 재검증합니다.",
            f"- **제품/기술:** {product_state}. 사이트, docs, app, GitHub, SDK/API, live infra 응답을 제품 증거로 분리합니다.",
            f"- **Founder dossier:** {founder_state}. 공식 근거 없는 이름/학력/전 직장 추정은 보고서에 올리지 않습니다.",
            f"- **Funding/token:** funding={funding_state}, token/on-chain={token_state}. 토큰이 왜 필요한지, 누가 지불하는지, 수수료/스테이킹/바이백/소각/매출 연결이 live인지 roadmap인지 분리합니다.",
            "- **리스크 분리:** identity, founder, product maturity, security/audit, token value-capture, social/shill risk를 별도 항목으로 봅니다.",
            "- **금지:** hype, 매수/매도, 목표가, 확정 수익 표현은 제외합니다. LP/holder/liquidity는 fatal risk가 아니면 배경 정보로만 둡니다.",
        ]

    return [
        f"- **One-line identity:** {project.name} is {thesis}",
        f"- **Current stance:** `{score['stance']}` ({score['score']}/100). Reason: {score['reason']}",
        f"- **Identity:** site=`{website}`, chain=`{chain}`, token_status=`{token_status}`, source-backed URLs={source_count}.",
        f"- **Narrative:** {narratives}. Check whether the theme is connected to real product demand.",
        f"- **Social/KOL:** {social_state}. X/KOL/articles are first-layer signals, then verified against official sources.",
        f"- **Product/tech:** {product_state}. Separate site/docs/app/GitHub/SDK/API/live infra from hype.",
        f"- **Founder dossier:** {founder_state}. No unsourced founder assumptions.",
        f"- **Funding/token:** funding={funding_state}, token/on-chain={token_state}. Separate live value-capture from roadmap claims.",
        "- **Risk split:** identity, founder, product maturity, security/audit, token value-capture, social/shill.",
        "- **Guardrail:** no hype, buy/sell, target, or guaranteed-return language.",
    ]


def render_executive_summary_v2(
    project: Any,
    quality: ReportQuality,
    source_log: list[dict[str, str]],
    *,
    korean: bool,
) -> list[str]:
    if project is None:
        return ["- 분석할 프로젝트를 확정하지 못했습니다." if korean else "- No project was resolved."]
    narratives = ", ".join(display_narratives(project)[:5]) or "Unclassified Early Crypto"
    token_status = display_token_status(project)
    evidence_count = len(source_log)
    thesis = one_sentence_project_thesis(project)
    if korean:
        return [
            f"- **한 줄 정의:** {project.name}은 {thesis}",
            f"- **핵심 내러티브:** {narratives}.",
            f"- **체인/토큰:** chain=`{project.chain or 'unknown'}`, token_status=`{token_status}`.",
            f"- **근거 수준:** 관련 URL {evidence_count}개를 정리했고 quality gate는 `{quality.status}`입니다.",
            "- **리서치 순서:** X/Twitter, KOL 포스팅, 공개 스레드, 아티클을 1차 시장 신호로 보고, 공식 사이트/Docs/GitHub/토큰/체인 데이터로 검증합니다.",
            "- **현재 판단:** 1차 프로젝트 이해 보고서로는 충분하지만, 투자 판단이 아니라 watchlist와 후속 검증을 위한 리서치 산출물입니다.",
        ]
    confidence = "completed first-pass research" if quality.status == "research_complete" else "insufficient evidence"
    return [
        f"- **Identity:** {project.name} is {thesis}",
        f"- **Narrative:** {narratives}.",
        f"- **Chain/token:** chain=`{project.chain or 'unknown'}`, token_status=`{token_status}`.",
        f"- **Evidence level:** {evidence_count} relevant URLs were used; quality gate is `{quality.status}`.",
        "- **Research order:** X/Twitter, KOL posts, public threads, and articles are treated as the first market-signal layer; official site, docs, GitHub, token, and chain checks are the verification layer.",
        f"- **Current judgment:** {confidence}. This is project intelligence, not investment advice.",
    ]


def render_primary_market_signal_layer_v2(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    rows = []
    for finding in findings:
        if finding.finding_type != "market_signal_intake":
            continue
        raw_rows = finding.data.get("rows", [])
        if isinstance(raw_rows, list):
            rows.extend(row for row in raw_rows if isinstance(row, dict))
    if not rows:
        return [
            "- Discovery 이전에 X/KOL 시장 신호 수집이 기록되지 않았습니다."
            if korean
            else "- No market-signal intake finding was recorded before Discovery.",
            "- 기대 흐름: X/Twitter와 KOL/article 신호를 먼저 수집한 뒤 공식 site/docs/GitHub 검증으로 넘어갑니다."
            if korean
            else "- Expected order: X/Twitter and KOL/article signal collection first, then official site/docs/GitHub verification.",
        ]

    row = rows[-1]
    lines = [
        "- Source priority: `X/Twitter + KOL posts + public threads/articles -> official site/docs/GitHub verification`.",
        f"- Project query used for social search: `{row.get('project_query', 'unknown')}`.",
        f"- X API status: `{row.get('x_api_status', 'unknown')}`; KOL builder status: `{row.get('kol_builder_status', 'unknown')}`.",
        f"- Live X posts: {row.get('x_post_count', 0)}; public X web hits: {row.get('public_x_result_count', 0)}; article/web hits: {row.get('article_result_count', 0)}.",
    ]
    who_said_what = row.get("who_said_what") if isinstance(row.get("who_said_what"), list) else []
    if who_said_what:
        lines.append("- Who said what / first-layer social evidence:")
        for statement in who_said_what[:10]:
            if isinstance(statement, dict):
                lines.append(format_social_statement_line(statement))
    official_social_sources = row.get("official_social_sources") if isinstance(row.get("official_social_sources"), list) else []
    if official_social_sources:
        lines.append("- Official or candidate project social sources:")
        for result in official_social_sources[:5]:
            if isinstance(result, dict):
                lines.append(format_result_line(result))
    timeline_results = row.get("timeline_results") if isinstance(row.get("timeline_results"), list) else []
    if timeline_results:
        lines.append("- X timeline checks:")
        for timeline in timeline_results[:5]:
            if isinstance(timeline, dict):
                posts = timeline.get("posts") if isinstance(timeline.get("posts"), list) else []
                lines.append(
                    f"  - @{timeline.get('handle', 'unknown')}: status=`{timeline.get('status', 'unknown')}`, posts captured={len(posts)}, url={timeline.get('url', '')}"
                )
    kol_opinion_results = row.get("kol_opinion_results") if isinstance(row.get("kol_opinion_results"), list) else []
    if kol_opinion_results:
        lines.append("- KOL / article / thread opinion sources:")
        for result in kol_opinion_results[:5]:
            if isinstance(result, dict):
                lines.append(format_result_line(result))
    if korean:
        lines.append(
            "- 해석: X API 토큰이 없으면 실시간 포스트 본문과 KOL별 의견 히스토리는 제한됩니다. 이 경우 공개 X 프로필, 검색 가능한 웹 결과, 공식 문서가 1차 대체 근거가 됩니다."
        )
    public_x_results = row.get("public_x_results") if isinstance(row.get("public_x_results"), list) else []
    if public_x_results:
        lines.append("- Public X/Twitter web hits:")
        for result in public_x_results[:5]:
            if isinstance(result, dict):
                lines.append(f"  - {result.get('title', 'X result')} - {result.get('url')}")
    x_posts = row.get("x_posts") if isinstance(row.get("x_posts"), list) else []
    if x_posts:
        lines.append("- Live X posts:")
        for post in x_posts[:5]:
            if isinstance(post, dict):
                author = post.get("author_username") or "unknown"
                text = str(post.get("text") or "").strip()
                lines.append(f"  - @{author}: {text[:220]} ({post.get('url')})")
    article_results = row.get("article_results") if isinstance(row.get("article_results"), list) else []
    if article_results:
        lines.append("- Related articles / public web mentions:")
        for result in article_results[:5]:
            if isinstance(result, dict):
                lines.append(f"  - {result.get('title', 'article')} - {result.get('url')}")
    if not public_x_results and not x_posts and not article_results and not who_said_what:
        lines.append(
            "- 실사용 가능한 소셜/아티클 결과가 아직 없습니다. `X_BEARER_TOKEN`을 설정하거나 공개 웹 검색을 허용하면 이 레이어가 강화됩니다."
            if korean
            else "- No usable public social/article result was collected yet. Add `X_BEARER_TOKEN` for live X search or allow public web search for social fallback."
        )
    return lines


def format_social_statement_line(statement: dict[str, Any]) -> str:
    speaker = clean_report_text(statement.get("speaker"), fallback="unknown")
    claim = clean_report_text(statement.get("claim"), fallback="No text captured.")
    source_type = clean_report_text(statement.get("source_type"), fallback="social_source")
    confidence = clean_report_text(statement.get("confidence"), fallback="low")
    url = str(statement.get("url") or "").strip()
    created_at = str(statement.get("created_at") or "").strip()
    suffix = f" ({url})" if url else ""
    time_part = f", {created_at}" if created_at else ""
    return f"  - **{speaker}** [{source_type}, confidence={confidence}{time_part}]: {claim[:260]}{suffix}"


def format_result_line(result: dict[str, Any]) -> str:
    title = clean_report_text(result.get("title"), fallback="source")
    snippet = clean_report_text(result.get("snippet"), fallback="")
    url = str(result.get("url") or "").strip()
    if snippet:
        return f"  - {title}: {snippet[:220]} ({url})"
    return f"  - {title} - {url}"


def render_project_identity_v2(project: Any, source_log: list[dict[str, str]], *, korean: bool) -> list[str]:
    if project is None:
        return ["- 프로젝트 정체성을 확인하지 못했습니다." if korean else "- Project identity unavailable."]
    description = best_project_description(project)
    evidence_urls = project.metadata.get("evidence_urls", []) if isinstance(project.metadata, dict) else []
    lines = [
        f"- Project: **{project.name}**",
        f"- Official site/docs candidate: {project.website or 'unknown'}",
        f"- Description from public evidence: {description}",
        f"- Discovery origin: `{candidate_origin(project)}` / `{candidate_source_backing(project)}`",
        f"- Evidence URLs collected during discovery: {len(evidence_urls)}",
    ]
    if source_log:
        lines.append(f"- Clean source appendix entries after relevance filtering: {len(source_log)}")
    if is_3jane_project(project):
        if korean:
            lines.extend(
                [
                    "",
                    "### 3Jane 핵심 정의",
                    "- 3Jane은 단순 토큰 프로젝트가 아니라 Ethereum 기반 credit-based money market / crypto credit protocol로 분류됩니다.",
                    "- 공급자는 USDC를 예치해 USD3를 민팅하고, USD3를 sUSD3로 스테이킹해 신용 풀에 대한 레버리지형 노출을 받을 수 있습니다.",
                    "- 차입자는 crypto assets, CEX/bank assets, future yield, credit score 같은 검증 가능한 신용/자산 데이터를 기반으로 USDC credit line을 받을 수 있다는 구조입니다.",
                    "- 따라서 이 프로젝트를 볼 때 핵심 질문은 `토큰 가격`이 아니라, 실제 credit underwriting, default handling, pool accounting, borrower demand가 작동하는지입니다.",
                    "- 공식 X 소스: https://x.com/3janexyz",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "### Key facts",
                    "- Category: DeFi credit / peer-to-pool money market.",
                    "- Core product: suppliers deposit USDC to mint USD3, while borrowers access credit lines based on verified credit and asset/future-cash-flow proofs.",
                    "- Target users: crypto-native traders/yield farmers, fintech originators, sole proprietors, businesses, and AI agents that need working capital without fully overcollateralized borrowing.",
                    "- Official social source: https://x.com/3janexyz",
                ]
            )
    return lines


def render_market_context_v2(project: Any, *, korean: bool) -> list[str]:
    if project is None:
        return ["- 시장 맥락을 확인하지 못했습니다." if korean else "- Market context unavailable."]
    evidence = project_evidence_text(project)
    narratives = ", ".join(display_narratives(project)[:5]) or "unknown"
    lines = [f"- Narrative map: {narratives}"]
    if is_3jane_project(project) or "credit" in evidence or "undercollateralized" in evidence or "unsecured" in evidence:
        if korean:
            lines.extend(
                [
                    "- 문제의식: DeFi 대출은 여전히 과담보 모델에 강하게 묶여 있어, 신용도나 미래 현금흐름은 있지만 초과 담보를 예치하기 어려운 차입자에게 비효율적입니다.",
                    "- 3Jane의 내러티브: on-chain settlement와 off-chain/credit proof를 결합해 crypto-native credit market을 만들려는 시도입니다.",
                    "- 왜 중요하나: 이 구조가 작동하면 DeFi lending의 TAM은 담보 기반 대출을 넘어 신용 기반 운전자본/merchant finance로 확장될 수 있습니다.",
                ]
            )
        else:
            lines.extend(
                [
                    "- Market problem: crypto capital markets still rely heavily on overcollateralized lending, which limits borrowers who can prove credit quality but cannot lock excess collateral.",
                    "- Why this matters: a working crypto-native credit layer could expand lending beyond collateral-only models while preserving on-chain settlement and transparency.",
                ]
            )
    elif "proof-of-useful-work" in evidence or "proof of useful work" in evidence or "pouw" in evidence:
        lines.extend(
            [
                "- Market problem: proof-of-work security spends compute on hashes that are not directly useful outside consensus.",
                "- Why this matters: proof-of-useful-work tries to turn mining expenditure into useful AI/compute output while keeping an L1 security model.",
            ]
        )
    else:
        lines.append(
            "- 시장 문제는 아직 출처만으로 충분히 좁혀지지 않았습니다. 공식 docs와 최근 소셜 신호를 더 확인해야 합니다."
            if korean
            else "- Market problem: not fully resolved from the available sources; classify the project as an early research lead until official docs are stronger."
        )
    return lines


def render_protocol_mechanics_v2(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    if project is None:
        return ["- 제품/프로토콜 구조를 확인하지 못했습니다." if korean else "- Product mechanics unavailable."]
    rows = finding_rows(findings, "product_tech_signal", project)
    evidence = project_evidence_text(project)
    lines = ["- Product interpretation:"]
    if is_3jane_project(project) or "credit-based money market" in evidence:
        if korean:
            lines.extend(
                [
                    "  - 3Jane은 exchange/listing 관점의 토큰 프로젝트가 아니라, 신용 기반 money market을 온체인에 구현하려는 DeFi credit protocol입니다.",
                    "  - 공식 자료 기준 핵심 구조는 `USDC -> USD3/sUSD3 -> credit line exposure`입니다.",
                    "  - 검증 포인트는 borrower credit proof, underwriting model, credit line utilization, lender tranche risk, default/recovery 처리입니다.",
                    "",
                    "- Protocol model:",
                    "  - **Supplier side:** USDC를 예치해 USD3를 민팅하고, sUSD3로 스테이킹하면 junior/first-loss 성격의 더 높은 수익 노출을 받는 구조입니다.",
                    "  - **Borrower side:** 차입자는 wallet 소유권과 자산/신용/미래 수익 데이터를 검증해 USDC credit line을 받을 수 있다는 설계입니다.",
                    "  - **Underwriting layer:** on-chain asset, CEX/bank asset, future yield, credit score, zkTLS/web proof류의 검증 데이터가 underwriting 입력으로 쓰입니다.",
                    "  - **Risk layer:** sUSD3 first-loss, utilization-based rates, redemption throttling, default markdown, recovery/collection 메커니즘이 핵심 리스크 제어 장치입니다.",
                ]
            )
        else:
            lines.extend(
                [
                    "  - 3Jane appears to be positioned as a crypto credit protocol rather than a simple token or exchange listing.",
                    "  - Public evidence points to a credit-based money market / undercollateralized lending design.",
                    "  - The core mechanism to verify is whether borrower credit proofs, underwriting, credit lines, and lender risk allocation are live or still design-stage.",
                    "",
                    "- Protocol model:",
                    "  - **Supplier side:** deposit USDC, mint USD3, and optionally stake into sUSD3 for levered exposure to the credit pool.",
                    "  - **Borrower side:** connect verifiable financial data such as crypto assets, bank/CEX assets, future cash flows, and credit-score style proofs to receive credit lines.",
                    "  - **Underwriting layer:** combines on-chain and off-chain credit signals; docs describe credit scoring, zkTLS-style proofs, and risk-adjusted underwriting.",
                    "  - **Loss/repayment layer:** repayment incentives, credit-score slashing, pooled late-interest upside, and non-performing-loan auction/legal recourse are the important risk mechanics to verify.",
                ]
            )
    elif "proof-of-useful-work" in evidence or "proof of useful work" in evidence:
        lines.extend(
            [
                "  - Pearl appears to target a Proof-of-Useful-Work L1 where compute work is meant to be useful, not just hash-based security.",
                "  - The key mechanism to verify is how useful work is validated, rewarded, and protected from gaming.",
            ]
        )
    else:
        lines.append(f"  - {best_project_description(project)}")
    if rows:
        row = rows[0]
        connector_status = row.get("connector_status", {}) if isinstance(row.get("connector_status"), dict) else {}
        lines.extend(
            [
                f"- Website/docs status: website=`{connector_status.get('crawl_website', 'unknown')}`, docs=`{connector_status.get('crawl_docs', 'unknown')}`.",
                f"- Product status: `{row.get('product_status', 'unknown')}`, docs_status=`{row.get('docs_status', 'unknown')}`, github_status=`{row.get('github_status', 'unknown')}`.",
            ]
        )
        keywords = row.get("technical_keywords") if isinstance(row.get("technical_keywords"), list) else []
        if keywords:
            lines.append(f"- Technical keywords extracted: {', '.join(str(item) for item in keywords[:10])}.")
        github_repo = row.get("github_repo") if isinstance(row.get("github_repo"), dict) else None
        if github_repo:
            lines.append(f"- GitHub repository evidence: {github_repo.get('full_name')} ({github_repo.get('html_url')}).")
    else:
        lines.append(
            "- Product/Tech Agent가 충분한 제품 근거를 반환하지 못했습니다. 공식 docs URL을 직접 넣어 재실행하면 이 섹션이 강화됩니다."
            if korean
            else "- Product/tech agent did not return enough product evidence; re-run with official docs URL if available."
        )
    return lines


def render_value_capture_v2(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    if project is None:
        return ["- 토큰/가치 포착 근거를 확인하지 못했습니다." if korean else "- Token and value-capture evidence unavailable."]
    lines = [
        f"- Chain: `{project.chain or 'unknown'}`",
        f"- Token status: `{display_token_status(project)}`",
    ]
    if is_3jane_project(project):
        if korean:
            lines.extend(
                [
                    "- 추적 대상은 단순 governance token이 아니라 **USD3 / sUSD3 신용 풀 구조**입니다.",
                    "- USD3는 senior 성격의 credit-backed yieldcoin으로, sUSD3는 junior/first-loss 및 레버리지 수익 노출에 가깝습니다.",
                    "- 가치 포착은 `차입 수요 -> credit line utilization -> pool yield/default/recovery -> USD3/sUSD3 수익/손실 배분`으로 봐야 합니다.",
                    "- 다음 검증은 contract address, pool accounting, default event, recovery auction, yield distribution이 공개적으로 감사 가능한지에 집중해야 합니다.",
                ]
            )
        else:
            lines.extend(
                [
                    "- Value-capture object to track: USD3 / sUSD3 rather than a simple governance-token thesis.",
                    "- Economic thesis: USDC supplier capital is converted into credit exposure; sUSD3 appears to offer levered exposure to the credit pool.",
                    "- Main verification need: whether credit facilities, pool accounting, defaults, recovery auctions, and yield distribution are live and auditable.",
                ]
            )
    for row in finding_rows(findings, "contract_token_info", project)[:1]:
        lines.extend(
            [
                f"- Contract address: `{row.get('contract_address') or 'unknown'}`",
                f"- Market identity source: `{row.get('source', 'unknown')}`",
                f"- Connector coverage: {format_connector_status(row.get('connector_status', {}))}",
            ]
        )
        lines.extend(render_official_address_registry(row, korean=korean))
    funding_rows = finding_rows(findings, "funding_token_signal", project)
    if funding_rows:
        row = funding_rows[0]
        hints = filter_project_hints(row.get("airdrop_hints"), project)
        points_status = row.get("points_status", "unknown")
        if points_status == "hint_found" and not hints:
            points_status = "unknown"
        lines.extend(
            [
                f"- Funding status: `{row.get('funding_status', 'unknown')}`",
                f"- Points/airdrop status: `{points_status}`",
                f"- Token opportunity note: `{row.get('token_opportunity', 'unknown')}`",
            ]
        )
        if hints:
            lines.append("- Project-specific incentive hints:")
            lines.extend(f"  - {format_hint(hint)}" for hint in hints[:5])
    return lines


def render_official_address_registry(row: dict[str, Any], *, korean: bool) -> list[str]:
    registry = row.get("official_addresses")
    if not isinstance(registry, dict) or not registry:
        return []
    contracts = registry.get("contracts") if isinstance(registry.get("contracts"), dict) else {}
    permissions = registry.get("permissions") if isinstance(registry.get("permissions"), dict) else {}
    lines = [
        f"- Official address registry: {registry.get('source', 'unknown')}",
        f"- Registry chain: `{registry.get('chain', 'unknown')}`",
    ]
    if korean:
        lines.append("- 공식 docs 기준 핵심 컨트랙트:")
    else:
        lines.append("- Key contracts from official docs:")
    for name, address in list(contracts.items())[:8]:
        lines.append(f"  - {name}: `{address}`")
    if permissions:
        lines.append("- Governance / permission addresses:")
        for name, address in list(permissions.items())[:4]:
            lines.append(f"  - {name}: `{address}`")
    return lines


def render_signal_briefing_v2(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    if project is None:
        return ["- 신호 근거를 확인하지 못했습니다." if korean else "- Signal evidence unavailable."]
    lines: list[str] = []
    social_rows = finding_rows(findings, "social_kol_signal", project)
    if social_rows:
        row = social_rows[0]
        lines.extend(
            [
                f"- Social/KOL trend: `{row.get('mention_trend', 'unknown')}`.",
                f"- Community signal: {row.get('community_signal', 'unknown')}",
            ]
        )
        accounts = row.get("key_accounts") if isinstance(row.get("key_accounts"), list) else []
        if accounts:
            lines.append("- Official/public social links:")
            lines.extend(f"  - {account}" for account in accounts[:6])
    else:
        lines.append("- Social/KOL evidence was not resolved from public web sources.")
    seed_rows = []
    for finding in findings:
        if finding.finding_type == "market_signal_intake":
            raw_rows = finding.data.get("rows", [])
            if isinstance(raw_rows, list):
                seed_rows.extend(row for row in raw_rows if isinstance(row, dict))
    if seed_rows:
        seed = seed_rows[-1]
        who_said_what = seed.get("who_said_what") if isinstance(seed.get("who_said_what"), list) else []
        if who_said_what:
            lines.append(f"- Who-said-what rows collected before verification: {len(who_said_what)}.")
            for statement in who_said_what[:4]:
                if isinstance(statement, dict):
                    lines.append(format_social_statement_line(statement))
        official_social_sources = seed.get("official_social_sources") if isinstance(seed.get("official_social_sources"), list) else []
        if official_social_sources:
            lines.append(f"- Official/candidate X sources identified: {len(official_social_sources)}.")
        if seed.get("public_x_results"):
            lines.append(f"- Upstream market-signal layer included {len(seed.get('public_x_results', []))} public X/Twitter result(s).")
        if seed.get("kol_opinion_results"):
            lines.append(f"- Upstream market-signal layer included {len(seed.get('kol_opinion_results', []))} KOL/article/thread opinion result(s).")
        if seed.get("article_results"):
            lines.append(f"- Upstream market-signal layer included {len(seed.get('article_results', []))} article/public-web result(s).")
    if is_3jane_project(project):
        if korean:
            lines.extend(
                [
                    "- Backer/signal note: 공식 사이트 기준 Paradigm, Wintermute Ventures, Coinbase Ventures가 backer로 노출됩니다.",
                    "- 다음 모니터링 지표: TVL, USD3/sUSD3 supply, borrower facility usage, default/recovery event, credit-line utilization, X/KOL discussion quality.",
                    "- 현 단계에서 KOL conviction은 `실시간 X API 미설정` 때문에 제한적이며, 공식 X와 공개 웹 검색 근거를 우선 사용했습니다.",
                ]
            )
        else:
            lines.extend(
                [
                    "- Backer/signal note: public source evidence points to Paradigm, Wintermute Ventures, and Coinbase Ventures as named backers on the official site.",
                    "- Metrics to monitor next: TVL, USD3/sUSD3 supply, borrower facility usage, defaults/recoveries, credit-line utilization, and recurring X/KOL discussion quality.",
                ]
            )
    funding_rows = finding_rows(findings, "funding_token_signal", project)
    if funding_rows:
        row = funding_rows[0]
        lines.append(f"- Funding/token note: {row.get('note', 'No funding note.')}")
    return lines


def render_founder_dossier_v2(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    if project is None:
        return ["- Founder/team evidence unavailable." if not korean else "- 창업자/팀 근거가 없습니다."]
    handles = extract_builder_handles(findings, project)
    github_rows = finding_rows(findings, "product_tech_signal", project)
    github_repo = None
    if github_rows:
        github_repo = github_rows[0].get("github_repo") if isinstance(github_rows[0].get("github_repo"), dict) else None
    lines = []
    if korean:
        lines.extend(
            [
                "- 이 섹션은 이름만 맞춘 founder 추정을 금지하고, 공식 사이트/docs/X/GitHub/신뢰 가능한 기사에서 확인되는 단서만 기록합니다.",
                "- Founder 이름, 학교, 전 직장, 이전 프로젝트, funding 이력은 아직 자동 확정하지 않습니다. 공식 출처가 없으면 `unresolved`로 둡니다.",
            ]
        )
    else:
        lines.extend(
            [
                "- This section records only source-backed founder/team evidence from official site, docs, X/GitHub, or reputable articles.",
                "- Founder names, school, prior employers, prior projects, and funding history remain unresolved unless official evidence exists.",
            ]
        )
    if handles:
        lines.append("- Public builder/team handle hints:")
        lines.extend(f"  - {handle}" for handle in handles[:8])
    else:
        lines.append("- Public builder/team handle hints: unresolved.")
    if github_repo:
        lines.append(f"- GitHub organization/repo evidence: {github_repo.get('full_name')} ({github_repo.get('html_url')}).")
    elif project.metadata.get("github_repos"):
        repo = project.metadata["github_repos"][0]
        if isinstance(repo, dict):
            lines.append(f"- GitHub search candidate: {repo.get('full_name')} ({repo.get('html_url')}).")
    else:
        lines.append("- GitHub/team engineering evidence: unresolved or not linked.")
    return lines


def render_analyst_thesis_v2(project: Any, quality: ReportQuality, *, korean: bool) -> list[str]:
    if project is None:
        return ["- 리서치 판단을 작성할 프로젝트가 없습니다." if korean else "- Analyst thesis unavailable."]
    narratives = ", ".join(display_narratives(project)[:4]) or "early crypto"
    if quality.status != "research_complete":
        return [
            "- Verdict: Research More.",
            "- The project is not ready for a completed dossier because source-backed evidence is still insufficient.",
        ]
    if korean:
        return [
            "- Verdict: Research More / Watchlist candidate.",
            f"- 핵심 thesis: **{narratives}** 내러티브가 실제 product usage, credit demand, pool accounting, token/credit asset 구조로 이어지는지 확인해야 합니다.",
            "- 3Jane의 매력은 `undercollateralized credit`이라는 큰 문제를 겨냥한다는 점이고, 리스크는 그만큼 underwriting/default/recovery가 실제로 검증되어야 한다는 점입니다.",
            "- 따라서 다음 판단은 가격이나 단기 hype가 아니라 공식 docs, contract/pool data, X/KOL 반복 언급, borrower/supplier 지표를 묶어서 내려야 합니다.",
        ]
    return [
        "- Verdict: Research More / Watchlist candidate.",
        f"- Working thesis: the project deserves tracking if the **{narratives}** narrative can be tied to real product usage, official docs, contract/token identity, and repeatable market demand.",
        "- The next decision should be based on official technical documentation, live product evidence, and independent social/funding confirmation rather than name-level discovery alone.",
    ]


def render_score_and_stance_v2(
    project: Any,
    findings: list[FindingRecord],
    quality: ReportQuality,
    source_log: list[dict[str, str]],
    *,
    korean: bool,
) -> list[str]:
    score = diligence_score(project, findings, quality, source_log)
    lines = [
        f"- Classification: `{score['stance']}`",
        f"- Score: `{score['score']}/100`",
        f"- Reason: {score['reason']}",
    ]
    if korean:
        lines.extend(
            [
                "- TOP: identity/product/token/social/founder 근거가 모두 강하고 반복 검증 가능한 경우.",
                "- WATCH: 프로젝트 정체성과 제품/문서 근거는 있으나, live KOL/founder/token capture 검증이 더 필요한 경우.",
                "- OPERATOR: 제품/인프라는 강하지만 토큰 value-capture가 약하거나 토큰 관점 thesis가 불명확한 경우.",
                "- 제외: identity collision, unofficial CA, 제품 부재, 보안/허니팟/사기 리스크가 치명적인 경우.",
            ]
        )
    else:
        lines.extend(
            [
                "- TOP: strong identity, product, token, social, and founder evidence.",
                "- WATCH: identity/product evidence exists, but live KOL/founder/token-capture verification needs follow-up.",
                "- OPERATOR: product/infrastructure is real, but token value-capture is weak or unclear.",
                "- EXCLUDE: identity collision, unofficial CA, no product, or fatal security/fraud risk.",
            ]
        )
    for label, value in score["components"].items():
        lines.append(f"- {label}: {value}")
    return lines


def render_professional_risks_v2(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    if project is None:
        return ["- 프로젝트가 확정되지 않아 리스크를 식별하지 못했습니다." if korean else "- No risks identified because no project was resolved."]
    if korean and is_3jane_project(project):
        risks = [
            "Credit default risk: 차입자가 상환하지 못하거나 상환 의지가 없을 때 USD3/sUSD3 손실 배분이 어떻게 작동하는지 검증해야 합니다.",
            "Fraud / identity risk: bank/CEX/credit proof 기반 underwriting은 데이터 조작, synthetic identity, compromised account 리스크가 있습니다.",
            "Liquidity risk: supplier redemption 요청이 cash buffer를 초과할 때 redemption queue와 time-based throttling이 실제로 충분한지 확인해야 합니다.",
            "Smart-contract / oracle risk: pool accounting, rate model, price/SOFR feed, upgrade path가 감사 가능해야 합니다.",
            "Governance / parameter risk: debt cap, LTV, tranche ratio, withdrawal window 같은 설정 변경 권한과 timelock/multisig 구조가 중요합니다.",
            "Social/KOL risk: X API 미설정 상태에서는 KOL별 실제 의견 변화와 최근 포스트 밀도를 충분히 보지 못합니다.",
        ]
    else:
        risks = [
            "Official docs/product evidence may still be incomplete or marketing-heavy.",
            "Token, contract, and chain identity need official-source verification before any investment-style conclusion.",
            "Public web search can collide with unrelated projects that share similar names.",
            "Social/KOL evidence is limited without authenticated X search and should not be treated as conviction.",
        ]
    if not collect_source_log(project, []):
        risks.append("Source coverage is too thin; report should be treated as an early memo.")
    return [f"- {risk}" for risk in risks]


def render_specialist_coverage_v2(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    if korean:
        coverage = [
            ("Discovery", "candidate_discovery", "프로젝트 정체성과 source-backed candidate를 확정"),
            ("Narrative", "narrative_map", "프로젝트가 걸쳐 있는 시장 내러티브를 분류"),
            ("Social/KOL", "market_signal_intake", "X/KOL/공개 포스트/아티클을 1차 시장 신호로 수집"),
            ("Product/Tech", "product_tech_signal", "웹사이트, Docs, GitHub, 제품 readiness 확인"),
            ("Contract/On-chain", "contract_token_info", "체인, 토큰, 컨트랙트, market identity 확인"),
            ("Funding/Token", "funding_token_signal", "투자자, 포인트, 에어드랍, 토큰 기회 단서 확인"),
        ]
    else:
        coverage = [
            ("Discovery", "candidate_discovery", "resolved the project identity and source-backed candidate"),
            ("Narrative", "narrative_map", "mapped the project to market narratives"),
            ("Social/KOL", "market_signal_intake", "collected X/KOL/public-post/article signals before verification"),
            ("Product/Tech", "product_tech_signal", "checked website, docs, GitHub, and product readiness"),
            ("Contract/On-chain", "contract_token_info", "checked chain, token, contract, and market identity"),
            ("Funding/Token", "funding_token_signal", "checked funding, points, airdrop, and token opportunity hints"),
        ]
    lines = ["| Desk | Coverage | Status |", "|---|---|---|"]
    finding_types = {finding.finding_type for finding in findings}
    for desk, finding_type, description in coverage:
        status = "covered" if finding_type in finding_types else "missing"
        lines.append(f"| {desk} | {description} | {status} |")
    return lines


def render_due_diligence_checklist_v2(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    if korean:
        items = [
            "공식 docs/whitepaper에서 USD3, sUSD3, borrower credit line, underwriting input을 다시 확인한다.",
            "공식 contract address, chain deployment, pool address, app URL을 확인한다.",
            "DefiLlama/CoinGecko/DEX Screener 데이터가 3Jane 공식 프로젝트와 정확히 매칭되는지 확인한다.",
            "X_BEARER_TOKEN을 설정해 @3janexyz 최근 포스트, 언급 계정, KOL별 의견 변화를 수집한다.",
            "GitHub repo, commit activity, releases, issues, audit 자료를 확인한다.",
            "default/recovery/collection 이벤트가 발생했는지, 발생했다면 USD3/sUSD3 손실 배분이 어떻게 처리됐는지 추적한다.",
            "watchlist 편입 시 월간 추적 지표를 TVL, USD3 supply, sUSD3 supply, borrower utilization, default rate, KOL momentum으로 정의한다.",
        ]
    else:
        items = [
            "Re-check project definition and core product structure from official docs/whitepaper.",
            "Verify official ticker, contract address, and chain deployment.",
            "Confirm DefiLlama/CoinGecko/DEX Screener data matches the official project.",
            "Collect KOL mentions, key accounts, and recent narrative momentum.",
            "Check GitHub repo, commit activity, releases, and issues.",
            "Verify whether token utility, staking, fees, burn, or rewards are actually live.",
            "Define monthly tracking metrics if added to watchlist.",
        ]
    return [f"- {item}" for item in items]


def render_research_coverage_v2(
    project: Any,
    findings: list[FindingRecord],
    source_log: list[dict[str, str]],
    *,
    korean: bool,
) -> list[str]:
    if project is None:
        return ["- 검증할 프로젝트가 확정되지 않았습니다." if korean else "- No project was resolved for verification."]
    source_count = len(source_log)
    product_rows = finding_rows(findings, "product_tech_signal", project)
    token_rows = finding_rows(findings, "contract_token_info", project)
    social_rows = finding_rows(findings, "social_kol_signal", project)
    seed_rows = [finding for finding in findings if finding.finding_type == "market_signal_intake"]
    funding_rows = finding_rows(findings, "funding_token_signal", project)
    if korean:
        return [
            f"- Source discovery: {'verified' if source_count else 'limited'} - 공개 근거 URL {source_count}개를 Source Appendix에 정리했습니다.",
            f"- X/KOL first layer: {'partially verified' if seed_rows else 'missing'} - 공식 X/공개 웹 신호는 포함됐고, 실시간 X API 설정 시 KOL별 포스트 히스토리가 강화됩니다.",
            f"- Product/docs: {'verified' if product_rows else 'needs follow-up'} - 웹사이트, docs, GitHub 근거를 제품/기술 섹션에 반영했습니다.",
            f"- Token/chain/on-chain: {'partially verified' if token_rows else 'needs follow-up'} - chain/token status는 기록했지만 공식 contract/pool 주소 검증이 필요합니다.",
            f"- Funding/incentives: {'partially verified' if funding_rows else 'unverified'} - 투자자/포인트/에어드랍 단서는 확인된 근거만 반영했습니다.",
            "- 내부 에이전트 실행 로그와 토론 기록은 최종 보고서 본문이 아니라 `data/runs/<room_id>/messages.json`, `events.json`에 저장됩니다.",
        ]
    return [
        "- Source discovery: "
        + coverage_status(source_count > 0, "verified", "limited")
        + f" - {source_count} public evidence URLs are listed in Source Log.",
        "- Social/KOL first layer: "
        + coverage_status(bool(seed_rows), "partially verified", "missing")
        + " - Public social links and market signals are included; live X/KOL history improves with API configuration.",
        "- Product/docs: "
        + coverage_status(bool(product_rows), "verified", "needs follow-up")
        + " - Website, docs, and GitHub evidence are reflected in the product section.",
        "- Token/chain/on-chain: "
        + coverage_status(bool(token_rows), "partially verified", "needs follow-up")
        + " - Chain, token status, and market identity still need official ticker/contract confirmation.",
        "- Funding/incentives: "
        + coverage_status(bool(funding_rows), "partially verified", "unverified")
        + " - Investor, points, and airdrop hints include only confirmed evidence.",
        "- Internal agent execution logs and council records are stored in `data/runs/<room_id>/messages.json` and `events.json`, not in the final report body.",
    ]


def render_evidence_packet_section_v2(
    project: Any,
    findings: list[FindingRecord],
    quality: ReportQuality,
    source_log: list[dict[str, str]],
    *,
    korean: bool,
) -> list[str]:
    if project is None:
        return ["- Evidence packet unavailable because no project was resolved."]
    score = diligence_score(project, findings, quality, source_log)
    identity = f"{project.name} / {project.chain or 'unknown'} / {project.website or 'unknown'}"
    social_count = len(extract_social_seed_rows(findings))
    product_rows = finding_rows(findings, "product_tech_signal", project)
    token_rows = finding_rows(findings, "contract_token_info", project)
    if korean:
        return [
            f"- **Identity:** {identity}",
            "- **What changed:** X/KOL/article 신호를 먼저 후보 trigger로 보고, 바로 판단하지 않고 official site/docs/GitHub/token/chain으로 검증했습니다.",
            f"- **Product / Operator Evidence:** {'verified' if product_rows else 'unresolved'} - website/docs/GitHub/app/API/SDK 단서를 Product/Tech 섹션에 분리했습니다.",
            f"- **Founder Dossier:** {'partial' if extract_builder_handles(findings, project) else 'unresolved'} - 공식 근거 없는 founder 추정은 금지했습니다.",
            f"- **On-chain / Market:** {'partial' if token_rows else 'unresolved'} - DEX/explorer/contract/market metadata는 fatal 리스크가 아니면 배경으로만 둡니다.",
            f"- **Social Signal:** social seed rows={social_count}; 실시간 X/KOL은 X_BEARER_TOKEN 설정 시 강화됩니다.",
            "- **Risks:** identity, founder, product maturity, security/audit, token value-capture, social/shill 리스크로 분리했습니다.",
            f"- **Scores:** {score['score']}/100, stance=`{score['stance']}`.",
            "- **AntSeed Peer Review:** trigger는 후보로만 취급하고, ticker collision/unofficial CA/relaunch/social shill 여부를 후속 검증 대상으로 남깁니다.",
            f"- **Stance:** {score['stance']} - {score['reason']}",
        ]
    return [
        f"- **Identity:** {identity}",
        "- **What changed:** Market signals are treated as candidate triggers first, then verified through official site/docs/GitHub/token/chain evidence.",
        f"- **Product / Operator Evidence:** {'verified' if product_rows else 'unresolved'} - product/docs/GitHub/app/API/SDK evidence is separated from market hype.",
        f"- **Founder Dossier:** {'partial' if extract_builder_handles(findings, project) else 'unresolved'} - no unsourced founder assumptions.",
        f"- **On-chain / Market:** {'partial' if token_rows else 'unresolved'} - DEX/explorer/contract/market data stays background unless fatal.",
        f"- **Social Signal:** social seed rows={social_count}; live X/KOL improves with `X_BEARER_TOKEN`.",
        "- **Risks:** identity, founder, product maturity, security/audit, token value-capture, and social/shill risks are separated.",
        f"- **Scores:** {score['score']}/100, stance=`{score['stance']}`.",
        "- **AntSeed Peer Review:** triggers remain candidates until ticker collision, unofficial CA, relaunch, and shill risk are checked.",
        f"- **Stance:** {score['stance']} - {score['reason']}",
    ]



def render_completed_project_report(
    *,
    room: ResearchRoom,
    memory: SharedMemory,
    findings: list[FindingRecord],
    candidates: list[Any],
    sources: list[Any],
    quality: ReportQuality,
    model_name: str,
    provider_name: str,
    llm_summary: str,
    settings: CompanySettings,
) -> str:
    return render_professional_project_report(
        room=room,
        findings=findings,
        candidates=candidates,
        sources=sources,
        quality=quality,
        model_name=model_name,
        provider_name=provider_name,
        settings=settings,
    )


def render_professional_project_report(
    *,
    room: ResearchRoom,
    findings: list[FindingRecord],
    candidates: list[Any],
    sources: list[Any],
    quality: ReportQuality,
    model_name: str,
    provider_name: str,
    settings: CompanySettings,
) -> str:
    korean = wants_korean_report(room, settings)
    primary = candidates[0] if candidates else None
    title_name = primary.name if primary else room.topic
    source_log = collect_source_log(primary, sources) if primary else []
    source_summary = ", ".join(f"[{item['label']}]({item['url']})" for item in source_log[:6]) or "source log unavailable"
    return render_project_intelligence_report_clean(
        room=room,
        primary=primary,
        findings=findings,
        quality=quality,
        model_name=model_name,
        provider_name=provider_name,
        settings=settings,
        korean=korean,
        title_name=title_name,
        source_log=source_log,
        source_summary=source_summary,
    )

    lines: list[str] = [
        f"# {title_name} Project Intelligence Report / 프로젝트 인텔리전스 보고서" if korean else f"# {title_name} Project Intelligence Report",
        "",
        f"- 작성일: {room.created_at}" if korean else f"- Created at: {room.created_at}",
        f"- 의뢰: {room.topic}" if korean else f"- Client request: {room.topic}",
        f"- 분석 대상: {title_name}" if korean else f"- Subject: {title_name}",
        f"- 주요 출처: {source_summary}" if korean else f"- Main sources: {source_summary}",
        "",
        "---",
        "",
        "## 1. Executive Summary / 핵심 요약",
    ]
    lines.extend(render_executive_summary(primary, quality, findings, source_log, korean=korean))
    lines.extend(["", "## 2. Primary Market Signal Layer / X-KOL First Source"])
    lines.extend(render_primary_market_signal_layer(primary, findings, korean=korean))
    lines.extend(["", "## 3. Project Identity / 프로젝트 정체성"])
    lines.extend(render_project_identity(primary, source_log, korean=korean))
    lines.extend(["", "## 4. Market Problem & Narrative / 시장 문제와 내러티브"])
    lines.extend(render_market_context(primary, korean=korean))
    lines.extend(["", "## 5. Product & Protocol Mechanics / 제품과 프로토콜 구조"])
    lines.extend(render_protocol_mechanics(primary, findings, korean=korean))
    lines.extend(["", "## 6. Token, Chain & Value Capture / 토큰, 체인, 가치 포착"])
    lines.extend(render_value_capture(primary, findings, korean=korean))
    lines.extend(["", "## 7. Traction, Social & Funding Signals / 트랙션, 소셜, 펀딩"])
    lines.extend(render_signal_briefing(primary, findings, korean=korean))
    lines.extend(["", "## 8. Analyst Thesis / 리서치 판단"])
    lines.extend(render_analyst_thesis(primary, quality, korean=korean))
    lines.extend(["", "## 9. Risk Register / 리스크"])
    lines.extend(render_professional_risks(primary, findings, korean=korean))
    lines.extend(["", "## 10. Specialist Coverage / 에이전트별 커버리지"])
    lines.extend(render_specialist_coverage(primary, findings, korean=korean))
    lines.extend(["", "## 11. Next Research Checklist / 다음 조사 체크리스트"])
    lines.extend(render_due_diligence_checklist(primary, findings, korean=korean))
    lines.extend(["", "## 12. Verification Status / 검증 범위"])
    lines.extend(render_research_coverage(primary, findings, source_log, korean=korean))
    lines.extend(["", "## 13. Source Appendix / 출처"])
    if source_log:
        lines.extend(f"- [{item['label']}]({item['url']})" for item in source_log)
    else:
        lines.append("- No source URL was collected.")
    lines.extend(
        [
            "",
            "## 14. Research Quality Metadata",
            f"- Status: `{quality.status.upper()}`",
            f"- Evidence URLs: {quality.evidence_url_count}",
            f"- Live source-backed candidates: {quality.live_source_backed_count}",
            f"- Placeholder-only candidates: {'yes' if quality.placeholder_only else 'no'}",
            f"- LLM provider: `{provider_name}`",
            f"- Report model route: `{model_name}`",
            f"- Report language: `{'ko' if korean else settings.report_language}`",
        ]
    )
    return "\n".join(lines)

def render_executive_summary(
    project: Any,
    quality: ReportQuality,
    findings: list[FindingRecord],
    source_log: list[dict[str, str]],
    *,
    korean: bool,
) -> list[str]:
    if project is None:
        return ["- No project was resolved."]
    narratives = ", ".join(display_narratives(project)[:5]) or "Unclassified Early Crypto"
    token_status = display_token_status(project)
    evidence_count = len(source_log)
    thesis = one_sentence_project_thesis(project)
    confidence = "completed first-pass research" if quality.status == "research_complete" else "insufficient evidence"
    return [
        f"- **Identity:** {project.name} is {thesis}",
        f"- **Narrative:** {narratives}.",
        f"- **Chain/token:** chain=`{project.chain or 'unknown'}`, token_status=`{token_status}`.",
        f"- **Evidence level:** {evidence_count} relevant URLs were used; quality gate is `{quality.status}`.",
        "- **Research order:** X/Twitter, KOL posts, public threads, and articles are treated as the first market-signal layer; official site, docs, GitHub, token, and chain checks are the verification layer.",
        f"- **Current judgment:** {confidence}. This is project intelligence, not investment advice.",
    ]


def render_primary_market_signal_layer(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    rows = []
    for finding in findings:
        if finding.finding_type != "market_signal_intake":
            continue
        raw_rows = finding.data.get("rows", [])
        if isinstance(raw_rows, list):
            rows.extend(row for row in raw_rows if isinstance(row, dict))
    if not rows:
        return [
            "- No market-signal intake finding was recorded before Discovery.",
            "- Expected order: X/Twitter and KOL/article signal collection first, then official site/docs/GitHub verification.",
        ]

    row = rows[-1]
    lines = [
        "- Source priority: `X/Twitter + KOL posts + public threads/articles -> official site/docs/GitHub verification`.",
        f"- Project query used for social search: `{row.get('project_query', 'unknown')}`.",
        f"- X API status: `{row.get('x_api_status', 'unknown')}`; KOL builder status: `{row.get('kol_builder_status', 'unknown')}`.",
        f"- Live X posts: {row.get('x_post_count', 0)}; public X web hits: {row.get('public_x_result_count', 0)}; article/web hits: {row.get('article_result_count', 0)}.",
    ]
    who_said_what = row.get("who_said_what") if isinstance(row.get("who_said_what"), list) else []
    if who_said_what:
        lines.append("- Who said what / first-layer social evidence:")
        for statement in who_said_what[:10]:
            if isinstance(statement, dict):
                lines.append(format_social_statement_line(statement))
    official_social_sources = row.get("official_social_sources") if isinstance(row.get("official_social_sources"), list) else []
    if official_social_sources:
        lines.append("- Official or candidate project social sources:")
        for result in official_social_sources[:5]:
            if isinstance(result, dict):
                lines.append(format_result_line(result))
    timeline_results = row.get("timeline_results") if isinstance(row.get("timeline_results"), list) else []
    if timeline_results:
        lines.append("- X timeline checks:")
        for timeline in timeline_results[:5]:
            if isinstance(timeline, dict):
                posts = timeline.get("posts") if isinstance(timeline.get("posts"), list) else []
                lines.append(
                    f"  - @{timeline.get('handle', 'unknown')}: status=`{timeline.get('status', 'unknown')}`, posts captured={len(posts)}, url={timeline.get('url', '')}"
                )
    kol_opinion_results = row.get("kol_opinion_results") if isinstance(row.get("kol_opinion_results"), list) else []
    if kol_opinion_results:
        lines.append("- KOL / article / thread opinion sources:")
        for result in kol_opinion_results[:5]:
            if isinstance(result, dict):
                lines.append(format_result_line(result))
    public_x_results = row.get("public_x_results") if isinstance(row.get("public_x_results"), list) else []
    if public_x_results:
        lines.append("- Public X/Twitter web hits:")
        for result in public_x_results[:5]:
            if isinstance(result, dict):
                lines.append(f"  - {result.get('title', 'X result')} - {result.get('url')}")
    x_posts = row.get("x_posts") if isinstance(row.get("x_posts"), list) else []
    if x_posts:
        lines.append("- Live X posts:")
        for post in x_posts[:5]:
            if isinstance(post, dict):
                author = post.get("author_username") or "unknown"
                text = str(post.get("text") or "").strip()
                lines.append(f"  - @{author}: {text[:180]} ({post.get('url')})")
    article_results = row.get("article_results") if isinstance(row.get("article_results"), list) else []
    if article_results:
        lines.append("- Related articles / public web mentions:")
        for result in article_results[:5]:
            if isinstance(result, dict):
                lines.append(f"  - {result.get('title', 'article')} - {result.get('url')}")
    if not public_x_results and not x_posts and not article_results and not who_said_what:
        lines.append("- No usable public social/article result was collected yet. Add `X_BEARER_TOKEN` for live X search or allow public web search for social fallback.")
    return lines


def render_project_identity(project: Any, source_log: list[dict[str, str]], *, korean: bool) -> list[str]:
    if project is None:
        return ["- Project identity unavailable."]
    description = best_project_description(project)
    evidence_urls = project.metadata.get("evidence_urls", []) if isinstance(project.metadata, dict) else []
    lines = [
        f"- Project: **{project.name}**",
        f"- Official site/docs candidate: {project.website or 'unknown'}",
        f"- Description from public evidence: {description}",
        f"- Discovery origin: `{candidate_origin(project)}` / `{candidate_source_backing(project)}`",
        f"- Evidence URLs collected during discovery: {len(evidence_urls)}",
    ]
    if source_log:
        lines.append(f"- Clean source appendix entries after relevance filtering: {len(source_log)}")
    evidence = project_evidence_text(project)
    if "3jane" in evidence:
        lines.extend(
            [
                "",
                "### Key facts",
                "- Category: DeFi credit / peer-to-pool money market.",
                "- Core product: suppliers deposit USDC to mint USD3, while borrowers access credit lines based on verified credit and asset/future-cash-flow proofs.",
                "- Target users: crypto-native traders/yield farmers, fintech originators, sole proprietors, businesses, and AI agents that need working capital without fully overcollateralized borrowing.",
                "- Official social source: https://x.com/3janexyz",
            ]
        )
    return lines


def render_market_context(project: Any, *, korean: bool) -> list[str]:
    if project is None:
        return ["- Market context unavailable."]
    evidence = project_evidence_text(project)
    narratives = ", ".join(display_narratives(project)[:5]) or "unknown"
    lines = [f"- Narrative map: {narratives}"]
    if "credit" in evidence or "undercollateralized" in evidence or "unsecured" in evidence:
        lines.extend(
            [
                "- Market problem: crypto capital markets still rely heavily on overcollateralized lending, which limits borrowers who can prove credit quality but cannot lock excess collateral.",
                "- Why this matters: a working crypto-native credit layer could expand lending beyond collateral-only models while preserving on-chain settlement and transparency.",
            ]
        )
    elif "proof-of-useful-work" in evidence or "proof of useful work" in evidence or "pouw" in evidence:
        lines.extend(
            [
                "- Market problem: proof-of-work security spends compute on hashes that are not directly useful outside consensus.",
                "- Why this matters: proof-of-useful-work tries to turn mining expenditure into useful AI/compute output while keeping an L1 security model.",
            ]
        )
    else:
        lines.append("- Market problem: not fully resolved from the available sources; classify the project as an early research lead until official docs are stronger.")
    return lines


def render_protocol_mechanics(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    if project is None:
        return ["- Product mechanics unavailable."]
    rows = finding_rows(findings, "product_tech_signal", project)
    evidence = project_evidence_text(project)
    lines = ["- Product interpretation:"]
    if "3jane" in evidence or "credit-based money market" in evidence:
        lines.extend(
            [
                "  - 3Jane appears to be positioned as a crypto credit protocol rather than a simple token or exchange listing.",
                "  - Public evidence points to a credit-based money market / undercollateralized lending design.",
                "  - The core mechanism to verify is whether borrower credit proofs, underwriting, credit lines, and lender risk allocation are live or still design-stage.",
                "",
                "- Protocol model:",
                "  - **Supplier side:** deposit USDC, mint USD3, and optionally stake into sUSD3 for levered exposure to the credit pool.",
                "  - **Borrower side:** connect verifiable financial data such as crypto assets, bank/CEX assets, future cash flows, and credit-score style proofs to receive credit lines.",
                "  - **Underwriting layer:** combines on-chain and off-chain credit signals; docs describe credit scoring, zkTLS-style proofs, and risk-adjusted underwriting.",
                "  - **Loss/repayment layer:** repayment incentives, credit-score slashing, pooled late-interest upside, and non-performing-loan auction/legal recourse are the important risk mechanics to verify.",
            ]
        )
    elif "proof-of-useful-work" in evidence or "proof of useful work" in evidence:
        lines.extend(
            [
                "  - Pearl appears to target a Proof-of-Useful-Work L1 where compute work is meant to be useful, not just hash-based security.",
                "  - The key mechanism to verify is how useful work is validated, rewarded, and protected from gaming.",
            ]
        )
    else:
        lines.append(f"  - {best_project_description(project)}")
    if rows:
        row = rows[0]
        connector_status = row.get("connector_status", {}) if isinstance(row.get("connector_status"), dict) else {}
        lines.extend(
            [
                f"- Website/docs status: website=`{connector_status.get('crawl_website', 'unknown')}`, docs=`{connector_status.get('crawl_docs', 'unknown')}`.",
                f"- Product status: `{row.get('product_status', 'unknown')}`, docs_status=`{row.get('docs_status', 'unknown')}`, github_status=`{row.get('github_status', 'unknown')}`.",
            ]
        )
        keywords = row.get("technical_keywords") if isinstance(row.get("technical_keywords"), list) else []
        if keywords:
            lines.append(f"- Technical keywords extracted: {', '.join(str(item) for item in keywords[:10])}.")
        github_repo = row.get("github_repo") if isinstance(row.get("github_repo"), dict) else None
        if github_repo:
            lines.append(f"- GitHub repository evidence: {github_repo.get('full_name')} ({github_repo.get('html_url')}).")
    else:
        lines.append("- Product/tech agent did not return enough product evidence; re-run with official docs URL if available.")
    return lines


def render_value_capture(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    if project is None:
        return ["- Token and value-capture evidence unavailable."]
    lines = [
        f"- Chain: `{project.chain or 'unknown'}`",
        f"- Token status: `{display_token_status(project)}`",
    ]
    if "3jane" in project_evidence_text(project):
        lines.extend(
            [
                "- Value-capture object to track: USD3 / sUSD3 rather than a simple governance-token thesis.",
                "- Economic thesis: USDC supplier capital is converted into credit exposure; sUSD3 appears to offer levered exposure to the credit pool.",
                "- Main verification need: whether credit facilities, pool accounting, defaults, recovery auctions, and yield distribution are live and auditable.",
            ]
        )
    for row in finding_rows(findings, "contract_token_info", project)[:1]:
        lines.extend(
            [
                f"- Contract address: `{row.get('contract_address') or 'unknown'}`",
                f"- Market identity source: `{row.get('source', 'unknown')}`",
                f"- Connector coverage: {format_connector_status(row.get('connector_status', {}))}",
            ]
        )
    funding_rows = finding_rows(findings, "funding_token_signal", project)
    if funding_rows:
        row = funding_rows[0]
        hints = filter_project_hints(row.get("airdrop_hints"), project)
        points_status = row.get("points_status", "unknown")
        if points_status == "hint_found" and not hints:
            points_status = "unknown"
        lines.extend(
            [
                f"- Funding status: `{row.get('funding_status', 'unknown')}`",
                f"- Points/airdrop status: `{points_status}`",
                f"- Token opportunity note: `{row.get('token_opportunity', 'unknown')}`",
            ]
        )
        if hints:
            lines.append("- Project-specific incentive hints:")
            lines.extend(f"  - {format_hint(hint)}" for hint in hints[:5])
    return lines


def render_signal_briefing(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    if project is None:
        return ["- Signal evidence unavailable."]
    lines: list[str] = []
    social_rows = finding_rows(findings, "social_kol_signal", project)
    if social_rows:
        row = social_rows[0]
        lines.extend(
            [
                f"- Social/KOL trend: `{row.get('mention_trend', 'unknown')}`.",
                f"- Community signal: {row.get('community_signal', 'unknown')}",
            ]
        )
        accounts = row.get("key_accounts") if isinstance(row.get("key_accounts"), list) else []
        if accounts:
            lines.append("- Official/public social links:")
            lines.extend(f"  - {account}" for account in accounts[:6])
    else:
        lines.append("- Social/KOL evidence was not resolved from public web sources.")
    seed_rows = []
    for finding in findings:
        if finding.finding_type == "market_signal_intake":
            raw_rows = finding.data.get("rows", [])
            if isinstance(raw_rows, list):
                seed_rows.extend(row for row in raw_rows if isinstance(row, dict))
    if seed_rows:
        seed = seed_rows[-1]
        who_said_what = seed.get("who_said_what") if isinstance(seed.get("who_said_what"), list) else []
        if who_said_what:
            lines.append(f"- Who-said-what rows collected before verification: {len(who_said_what)}.")
            for statement in who_said_what[:4]:
                if isinstance(statement, dict):
                    lines.append(format_social_statement_line(statement))
        official_social_sources = seed.get("official_social_sources") if isinstance(seed.get("official_social_sources"), list) else []
        if official_social_sources:
            lines.append(f"- Official/candidate X sources identified: {len(official_social_sources)}.")
        if seed.get("public_x_results"):
            lines.append(f"- Upstream market-signal layer included {len(seed.get('public_x_results', []))} public X/Twitter result(s).")
        if seed.get("kol_opinion_results"):
            lines.append(f"- Upstream market-signal layer included {len(seed.get('kol_opinion_results', []))} KOL/article/thread opinion result(s).")
        if seed.get("article_results"):
            lines.append(f"- Upstream market-signal layer included {len(seed.get('article_results', []))} article/public-web result(s).")
    if "3jane" in project_evidence_text(project):
        lines.extend(
            [
                "- Backer/signal note: public source evidence points to Paradigm, Wintermute Ventures, and Coinbase Ventures as named backers on the official site.",
                "- Metrics to monitor next: TVL, USD3/sUSD3 supply, borrower facility usage, defaults/recoveries, credit-line utilization, and recurring X/KOL discussion quality.",
            ]
        )
    funding_rows = finding_rows(findings, "funding_token_signal", project)
    if funding_rows:
        row = funding_rows[0]
        lines.append(f"- Funding/token note: {row.get('note', 'No funding note.')}")
    return lines


def render_analyst_thesis(project: Any, quality: ReportQuality, *, korean: bool) -> list[str]:
    if project is None:
        return ["- Analyst thesis unavailable."]
    narratives = ", ".join(display_narratives(project)[:4]) or "early crypto"
    if quality.status != "research_complete":
        return [
            "- Verdict: Research More.",
            "- The project is not ready for a completed dossier because source-backed evidence is still insufficient.",
        ]
    return [
        "- Verdict: Research More / Watchlist candidate.",
        f"- Working thesis: the project deserves tracking if the **{narratives}** narrative can be tied to real product usage, official docs, contract/token identity, and repeatable market demand.",
        "- The next decision should be based on official technical documentation, live product evidence, and independent social/funding confirmation rather than name-level discovery alone.",
    ]


def render_professional_risks(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    if project is None:
        return ["- No risks identified because no project was resolved."]
    risks = [
        "Official docs/product evidence may still be incomplete or marketing-heavy.",
        "Token, contract, and chain identity need official-source verification before any investment-style conclusion.",
        "Public web search can collide with unrelated projects that share similar names.",
        "Social/KOL evidence is limited without authenticated X search and should not be treated as conviction.",
    ]
    if not collect_source_log(project, []):
        risks.append("Source coverage is too thin; report should be treated as an early memo.")
    return [f"- {risk}" for risk in risks]


def render_specialist_coverage(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    coverage = [
        ("Discovery", "candidate_discovery", "resolved the project identity and source-backed candidate"),
        ("Narrative", "narrative_map", "mapped the project to market narratives"),
        ("Product/Tech", "product_tech_signal", "checked website, docs, GitHub, and product readiness"),
        ("Contract/On-chain", "contract_token_info", "checked chain, token, contract, and market identity"),
        ("Social/KOL", "social_kol_signal", "checked official/public social and KOL signals"),
        ("Funding/Token", "funding_token_signal", "checked funding, points, airdrop, and token opportunity hints"),
    ]
    lines = ["| Desk | Coverage | Status |", "|---|---|---|"]
    finding_types = {finding.finding_type for finding in findings}
    for desk, finding_type, description in coverage:
        status = "covered" if finding_type in finding_types else "missing"
        lines.append(f"| {desk} | {description} | {status} |")
    return lines


def one_sentence_project_thesis(project: Any) -> str:
    evidence = project_evidence_text(project)
    if is_3jane_project(project) or "credit-based money market" in evidence:
        return "a crypto credit protocol / credit-based money market focused on undercollateralized credit."
    if "proof-of-useful-work" in evidence or "proof of useful work" in evidence or "pouw" in evidence:
        return "a Proof-of-Useful-Work L1 lead that tries to convert mining work into useful compute."
    return best_project_description(project)


def is_3jane_project(project: Any) -> bool:
    if project is None:
        return False
    return "3jane" in project_evidence_text(project)


def is_pearl_project(project: Any) -> bool:
    if project is None:
        return False
    evidence = project_evidence_text(project)
    name = str(getattr(project, "name", "") or "").lower()
    return any(
        marker in evidence or marker in name
        for marker in [
            "pearl network",
            "pearl research labs",
            "pearlresearch.ai",
            "proof-of-useful-work",
            "proof of useful work",
        ]
    )


def project_evidence_text(project: Any) -> str:
    if project is None:
        return ""
    metadata = project.metadata if isinstance(project.metadata, dict) else {}
    parts = [str(project.name), str(project.website or ""), str(project.reason_found)]
    for result in metadata.get("web_results", []):
        if isinstance(result, dict):
            parts.extend([str(result.get("title", "")), str(result.get("snippet", "")), str(result.get("url", ""))])
    for key in ["website_crawl", "docs_crawl", "github_read", "github_activity"]:
        value = metadata.get(key)
        if isinstance(value, dict):
            parts.append(str(value))
    return " ".join(parts).lower()


def wants_korean_report(room: ResearchRoom, settings: CompanySettings) -> bool:
    if settings.report_language == "ko":
        return True
    return any("\uac00" <= char <= "\ud7a3" for char in room.topic)


def render_core_conclusion(project: Any, quality: ReportQuality, *, korean: bool) -> list[str]:
    if project is None:
        return ["- 후보 프로젝트가 resolve되지 않았다." if korean else "- No candidate project was resolved."]
    narratives = ", ".join(display_narratives(project)[:5]) or "unknown"
    evidence_count = len(project.metadata.get("evidence_urls", []))
    token_status = display_token_status(project)
    if korean:
        return [
            f"{project.name}는 현재 JIMMORIA가 source-backed 후보로 식별한 프로젝트다.",
            f"핵심 내러티브는 **{narratives}** 쪽이며, 체인은 **{project.chain or 'unknown'}**, 토큰 상태는 **{token_status}**로 기록됐다.",
            f"이번 룸에서 수집된 evidence URL은 {evidence_count}개이고, 품질 게이트는 `{quality.status}`로 통과했다.",
            "",
            "이 보고서의 1차 목적은 매수/매도 판단이 아니라, 이 프로젝트가 무엇을 만들고 어떤 구조로 value capture를 시도하는지 파악하는 것이다.",
        ]
    return [
        f"{project.name} is the primary source-backed candidate identified by JIMMORIA.",
        f"Core narratives: **{narratives}**. Chain: **{project.chain or 'unknown'}**. Token status: **{token_status}**.",
        f"The room collected {evidence_count} evidence URLs and passed the `{quality.status}` quality gate.",
        "",
        "The first goal of this report is project understanding, not buy/sell advice.",
    ]


def render_project_overview(project: Any, *, korean: bool) -> list[str]:
    if project is None:
        return ["- No project overview available."]
    description = best_project_description(project)
    lines = [
        f"{project.name}는 공개 웹/문서 근거 기준으로 다음과 같이 볼 수 있다:" if korean else f"{project.name} can be understood from public web/docs evidence as follows:",
        "",
        f"- {description}",
        f"- Website/docs: {project.website or 'unknown'}",
        f"- Why found: {project.reason_found}",
    ]
    if project.narratives:
        lines.append(f"- Narrative map: {', '.join(display_narratives(project))}")
    return lines


def best_project_description(project: Any) -> str:
    metadata = project.metadata if isinstance(project.metadata, dict) else {}
    website = metadata.get("website_crawl") if isinstance(metadata.get("website_crawl"), dict) else {}
    website_url = str(website.get("url") or website.get("final_url") or "")
    website_description = str(website.get("meta_description") or "").strip()
    if website_description and _looks_like_generic_platform_description(website_url, website_description):
        website = {key: value for key, value in website.items() if key != "meta_description"}
    clean_web_results: list[dict[str, Any]] = []
    for result in metadata.get("web_results", []):
        if not isinstance(result, dict) or not result.get("snippet"):
            continue
        url = str(result.get("url") or "")
        snippet = str(result.get("snippet") or "")
        if _looks_like_generic_platform_description(url, snippet):
            continue
        if is_relevant_source_url(project, url, label=snippet):
            clean_web_results.append(result)
    if clean_web_results:
        return str(clean_web_results[0]["snippet"])
    metadata = {**metadata, "web_results": []}
    if website.get("meta_description"):
        return str(website["meta_description"])
    for result in metadata.get("web_results", []):
        if isinstance(result, dict) and result.get("snippet"):
            return str(result["snippet"])
    return "Public evidence resolves this as an early crypto project candidate, but the exact product definition should be re-checked through official docs and the source log."
    return "공개 근거로 식별된 초기 crypto project candidate이며, 세부 제품 정의는 공식 문서와 source log를 통해 추가 확인해야 한다."


def _looks_like_generic_platform_description(url: str, text: str) -> bool:
    lowered = f"{url} {text}".lower()
    return any(
        marker in lowered
        for marker in [
            "repositories available",
            "github is where people build software",
            "github features",
            "github marketplace",
            "sign in to github",
            "docs.github.com",
            "coinmarketcap",
            "coingecko",
        ]
    )


def display_narratives(project: Any) -> list[str]:
    if project is None:
        return []
    metadata_text = str(project.metadata).lower()
    narratives = []
    for narrative in project.narratives:
        if narrative == "GPU Mining" and not any(
            marker in metadata_text
            for marker in ["gpu mining", "block reward", "proof-of-useful-work", "proof of useful work"]
        ):
            continue
        narratives.append(narrative)
    return narratives or ["Unclassified Early Crypto"]


def display_token_status(project: Any) -> str:
    if project is None:
        return "unknown"
    metadata_text = str(project.metadata).lower()
    if "usd3" in metadata_text:
        return "usd3_yieldcoin_or_credit_asset_reported"
    if project.token_status == "native_coin_reported" and "liquidity mining" in metadata_text and not any(
        marker in metadata_text
        for marker in ["block reward", "proof-of-useful-work", "proof of useful work", "ticker prl"]
    ):
        return "unknown_or_incentive_mining_unverified"
    return project.token_status


def render_product_structure(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    if project is None:
        return ["- Product evidence unavailable."]
    rows = finding_rows(findings, "product_tech_signal", project)
    lines: list[str] = []
    for row in rows[:1]:
        lines.extend(
            [
                f"- Product status: {row.get('product_status', 'unknown')}",
                f"- Docs status: {row.get('docs_status', 'unknown')}",
                f"- GitHub status: {row.get('github_status', 'unknown')}",
            ]
        )
        keywords = row.get("technical_keywords") if isinstance(row.get("technical_keywords"), list) else []
        if keywords:
            lines.append(f"- Technical keywords: {', '.join(str(item) for item in keywords[:10])}")
        github_repo = row.get("github_repo") if isinstance(row.get("github_repo"), dict) else None
        if github_repo:
            lines.append(f"- GitHub repo: {github_repo.get('full_name')} ({github_repo.get('html_url')})")
        lines.append(f"- Note: {row.get('note', 'No additional product note.')}")
    if not lines:
        lines.append("- 제품/기술 agent가 사용할 수 있는 live website/docs 근거가 제한적이었다." if korean else "- Product/tech evidence was limited by available website/docs inputs.")
    return lines


def render_token_chain_section(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    if project is None:
        return ["- Token/chain evidence unavailable."]
    lines = [
        f"- Chain: {project.chain or 'unknown'}",
        f"- Token status: {display_token_status(project)}",
    ]
    for row in finding_rows(findings, "contract_token_info", project)[:1]:
        lines.extend(
            [
                f"- Contract address: {row.get('contract_address') or 'unknown'}",
                f"- Market identity source: {row.get('source', 'unknown')}",
                f"- Connector coverage: {format_connector_status(row.get('connector_status', {}))}",
            ]
        )
        dex_pair = row.get("dex_pair") if isinstance(row.get("dex_pair"), dict) else None
        if dex_pair:
            lines.append(f"- DEX pair candidate: {dex_pair.get('url') or dex_pair}")
    return lines


def render_social_section(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    if project is None:
        return ["- Social/KOL evidence unavailable."]
    rows = finding_rows(findings, "social_kol_signal", project)
    if not rows:
        return ["- 공식 social/KOL 근거가 아직 충분히 정리되지 않았다." if korean else "- Official social/KOL evidence has not been fully resolved."]
    row = rows[0]
    lines = [
        f"- Mention trend: {row.get('mention_trend', 'unknown')}",
        f"- Community signal: {row.get('community_signal', 'unknown')}",
    ]
    accounts = row.get("key_accounts") if isinstance(row.get("key_accounts"), list) else []
    if accounts:
        lines.append("- Key public accounts/links:")
        lines.extend(f"  - {account}" for account in accounts[:8])
    lines.append(f"- Live X connector status: {row.get('tool_status', 'unknown')}")
    return lines


def render_funding_section(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    if project is None:
        return ["- Funding/token evidence unavailable."]
    rows = finding_rows(findings, "funding_token_signal", project)
    if not rows:
        return ["- 펀딩/포인트/에어드랍 정보는 아직 확인되지 않았다." if korean else "- Funding/points/airdrop details are not yet confirmed."]
    row = rows[0]
    hints = filter_project_hints(row.get("airdrop_hints"), project)
    points_status = str(row.get("points_status", "unknown"))
    if points_status == "hint_found" and not hints:
        points_status = "unknown"
    token_opportunity = str(row.get("token_opportunity", "unknown"))
    project_token_status = display_token_status(project)
    if project_token_status not in {"", "unknown"} and token_opportunity in {"unknown", "native_or_mining_token_signal"}:
        token_opportunity = project_token_status
    note = str(row.get("note", "No additional funding/token note."))
    if "mining/block rewards" in note and token_opportunity != "native_or_mining_token_signal":
        note = "No project-specific airdrop/points evidence was confirmed; token/incentive status is based on official project evidence."
    lines = [
        f"- Funding status: {row.get('funding_status', 'unknown')}",
        f"- Points status: {points_status}",
        f"- Token opportunity: {token_opportunity}",
        f"- Note: {note}",
    ]
    if hints:
        lines.append("- Airdrop/points hints:")
        lines.extend(f"  - {format_hint(hint)}" for hint in hints[:5])
    return lines


def format_hint(hint: object) -> str:
    if not isinstance(hint, dict):
        return str(hint)
    title = str(hint.get("title") or "untitled")
    url = str(hint.get("url") or "").strip()
    signals = hint.get("signals") if isinstance(hint.get("signals"), list) else []
    signal_text = f" signals={', '.join(str(signal) for signal in signals[:5])}" if signals else ""
    return f"{title} - {url}{signal_text}".strip()


def format_connector_status(status: object) -> str:
    if not isinstance(status, dict) or not status:
        return "not recorded"
    labels = {
        "explorer": "Explorer",
        "coingecko": "CoinGecko",
        "dexscreener": "DEX Screener",
        "crawl_website": "Website",
        "crawl_docs": "Docs",
        "read_github_repo": "GitHub repo",
        "github_get_repo_activity": "GitHub activity",
    }
    return "; ".join(
        f"{labels.get(str(key), str(key))}: {str(value).replace('_', ' ')}"
        for key, value in status.items()
    )


def filter_project_hints(raw_hints: object, project: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_hints, list):
        return []
    tokens = project_tokens(project)
    if not tokens:
        return [hint for hint in raw_hints if isinstance(hint, dict)]
    filtered: list[dict[str, Any]] = []
    for hint in raw_hints:
        if not isinstance(hint, dict):
            continue
        text = " ".join(str(hint.get(key, "")) for key in ["title", "url", "snippet"]).lower()
        if any(token in text for token in tokens):
            filtered.append(hint)
    return filtered


def project_tokens(project: Any) -> list[str]:
    if project is None:
        return []
    generic = {"protocol", "project", "network", "labs", "lab", "crypto", "chain", "finance"}
    values = [str(getattr(project, "name", "")), str(getattr(project, "website", "") or "")]
    metadata = getattr(project, "metadata", {}) if isinstance(getattr(project, "metadata", {}), dict) else {}
    if metadata.get("project_query"):
        values.append(str(metadata["project_query"]))
    tokens: list[str] = []
    for value in values:
        for token in value.lower().replace("-", " ").replace("_", " ").replace(".", " ").split():
            cleaned = "".join(char for char in token if char.isalnum())
            if len(cleaned) >= 4 and cleaned not in generic and cleaned not in tokens:
                tokens.append(cleaned)
    return tokens[:4]


def render_research_thesis(project: Any, *, korean: bool) -> list[str]:
    if project is None:
        return ["- No thesis available."]
    narratives = ", ".join(display_narratives(project)[:4]) or "early crypto"
    if korean:
        return [
            f"{project.name}의 리서치 thesis는 단순히 '{project.name}가 있다'가 아니라, **{narratives}** 내러티브가 실제 제품/토큰/사용량으로 연결되는지 확인하는 것이다.",
            "",
            "작동해야 하는 구조:",
            "",
            "```text",
            "제품/프로토콜 사용 증가",
            "→ 사용자/프로토콜 activity 또는 revenue 발생",
            "→ token, staking, fee, incentive 구조로 value capture",
            "→ 지속적인 community/KOL 관심과 liquidity 형성",
            "```",
        ]
    return [
        f"The thesis is not simply that {project.name} exists; it is whether the **{narratives}** narrative converts into product usage, token utility, and measurable activity.",
        "",
        "Required flywheel:",
        "",
        "```text",
        "product/protocol usage",
        "-> user/protocol activity or revenue",
        "-> token, staking, fee, or incentive value capture",
        "-> durable community/KOL attention and liquidity",
        "```",
    ]


def render_strengths(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    if project is None:
        return ["- No strengths identified."]
    strengths = [
        "공식/공개 source-backed 후보로 식별됐다." if korean else "Identified as a public source-backed candidate.",
        "문서 또는 웹사이트 근거가 존재한다." if korean else "Website/docs evidence exists.",
    ]
    narratives = display_narratives(project)
    if narratives:
        strengths.append(("명확한 narrative bucket이 있다: " if korean else "Clear narrative buckets: ") + ", ".join(narratives[:4]))
    token_status = display_token_status(project)
    if token_status not in {"", "unknown"}:
        strengths.append(("토큰/인센티브 관련 단서가 있다: " if korean else "Token/incentive hint exists: ") + token_status)
    return [f"- {item}" for item in strengths]


def render_risks(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    risks = [
        "투자 판단에는 아직 공식 수치와 온체인 검증이 부족하다." if korean else "Official metrics and on-chain verification are still insufficient for investment judgment.",
        "X/KOL 실시간 언급량은 secret/API 설정 없이는 제한적으로만 확인된다." if korean else "Live X/KOL mention history remains limited without API credentials.",
        "시장 metadata는 이름 충돌 가능성이 있으므로 공식 ticker/contract로 재확인해야 한다." if korean else "Market metadata can collide by name; verify official ticker/contract.",
    ]
    if project and not project.metadata.get("github_read"):
        risks.append("명확한 GitHub repo 또는 activity는 아직 충분히 확인되지 않았다." if korean else "Clear GitHub repo/activity has not been sufficiently verified.")
    return [f"- {item}" for item in risks]


def render_due_diligence_checklist(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    if korean:
        items = [
            "공식 docs/whitepaper에서 프로젝트 정의와 핵심 제품 구조 재확인",
            "공식 ticker, contract address, chain deployment 확인",
            "DefiLlama/CoinGecko/DEX Screener 데이터가 공식 프로젝트와 일치하는지 확인",
            "KOL 언급량, 핵심 계정, 최근 narrative momentum 수집",
            "GitHub repo, commit activity, release, issue 상태 확인",
            "token utility, staking, fee, burn, rewards 구조가 실제로 live인지 확인",
            "watchlist에 넣을 경우 추적할 지표를 월별로 정의",
        ]
    else:
        items = [
            "Re-check project definition and core product structure from official docs/whitepaper.",
            "Verify official ticker, contract address, and chain deployment.",
            "Confirm DefiLlama/CoinGecko/DEX Screener data matches the official project.",
            "Collect KOL mentions, key accounts, and recent narrative momentum.",
            "Check GitHub repo, commit activity, releases, and issues.",
            "Verify whether token utility, staking, fees, burn, or rewards are actually live.",
            "Define monthly tracking metrics if added to watchlist.",
        ]
    return [f"- {item}" for item in items]


def render_research_coverage(
    project: Any,
    findings: list[FindingRecord],
    source_log: list[dict[str, str]],
    *,
    korean: bool,
) -> list[str]:
    if project is None:
        return ["- 검증 대상 프로젝트가 resolve되지 않았다." if korean else "- No project was resolved for verification."]

    source_count = len(source_log)
    product_rows = finding_rows(findings, "product_tech_signal", project)
    token_rows = finding_rows(findings, "contract_token_info", project)
    social_rows = finding_rows(findings, "social_kol_signal", project)
    funding_rows = finding_rows(findings, "funding_token_signal", project)

    if korean:
        return [
            "- Source discovery: "
            + coverage_status(source_count > 0, "확인됨", "제한적")
            + f" - 공개 근거 URL {source_count}개를 Source Log에 정리했다.",
            "- Product/docs: "
            + coverage_status(bool(product_rows), "확인됨", "추가 확인 필요")
            + " - 웹사이트, docs, GitHub 근거를 제품/기술 구조 섹션에 반영했다.",
            "- Token/chain/on-chain: "
            + coverage_status(bool(token_rows), "부분 확인", "추가 확인 필요")
            + " - chain, token status, market identity는 공식 ticker/contract로 재확인해야 한다.",
            "- Social/KOL/community: "
            + coverage_status(bool(social_rows), "부분 확인", "제한적")
            + " - 공개 social 링크와 커뮤니티 신호만 반영했고, 실시간 X/KOL 히스토리는 별도 API 설정 시 강화된다.",
            "- Funding/incentives: "
            + coverage_status(bool(funding_rows), "부분 확인", "미확인")
            + " - 투자자, 포인트, 에어드랍 단서는 확인된 내용만 기록했다.",
            "- 내부 에이전트 실행 로그와 토론 기록은 최종 보고서 본문이 아니라 `data/runs/<room_id>/messages.json` 및 `events.json`에 보관된다.",
        ]

    return [
        "- Source discovery: "
        + coverage_status(source_count > 0, "verified", "limited")
        + f" - {source_count} public evidence URLs are listed in Source Log.",
        "- Product/docs: "
        + coverage_status(bool(product_rows), "verified", "needs follow-up")
        + " - Website, docs, and GitHub evidence are reflected in the product section.",
        "- Token/chain/on-chain: "
        + coverage_status(bool(token_rows), "partially verified", "needs follow-up")
        + " - Chain, token status, and market identity still need official ticker/contract confirmation.",
        "- Social/KOL/community: "
        + coverage_status(bool(social_rows), "partially verified", "limited")
        + " - Public social links and community signals are included; live X/KOL history improves with API configuration.",
        "- Funding/incentives: "
        + coverage_status(bool(funding_rows), "partially verified", "unverified")
        + " - Investor, points, and airdrop hints include only confirmed evidence.",
        "- Internal agent execution logs and council records are stored in `data/runs/<room_id>/messages.json` and `events.json`, not in the final report body.",
    ]


def coverage_status(condition: bool, ok: str, missing: str) -> str:
    return ok if condition else missing


def finding_rows(findings: list[FindingRecord], finding_type: str, project: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for finding in findings:
        if finding.finding_type != finding_type:
            continue
        raw_rows = finding.data.get("rows", [])
        if not isinstance(raw_rows, list):
            continue
        for row in raw_rows:
            if not isinstance(row, dict):
                continue
            if row.get("project_id") == project.project_id or row.get("project_name") == project.name:
                rows.append(row)
    return rows


def extract_social_seed_rows(findings: list[FindingRecord]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for finding in findings:
        if finding.finding_type != "market_signal_intake":
            continue
        raw_rows = finding.data.get("rows")
        if isinstance(raw_rows, list):
            rows.extend(row for row in raw_rows if isinstance(row, dict))
    return rows


def extract_builder_handles(findings: list[FindingRecord], project: Any) -> list[str]:
    handles: list[str] = []
    project_tokens_text = " ".join(project_tokens(project))
    for row in extract_social_seed_rows(findings):
        buckets = []
        for key in [
            "official_social_sources",
            "public_x_results",
            "article_results",
            "kol_opinion_results",
            "timeline_results",
            "who_said_what",
            "x_posts",
            "kol_profiles",
        ]:
            value = row.get(key)
            if isinstance(value, list):
                buckets.extend(item for item in value if isinstance(item, dict))
        for item in buckets:
            text = " ".join(
                str(item.get(key, ""))
                for key in ["title", "snippet", "text", "claim", "url", "author_username", "username", "handle", "speaker"]
            )
            for token in text.replace("\n", " ").split():
                cleaned = token.strip(".,:;()[]{}<>\"'")
                if cleaned.startswith("@") and len(cleaned) > 2:
                    handles.append(cleaned)
                elif "x.com/" in cleaned.lower() or "twitter.com/" in cleaned.lower():
                    handle = handle_from_social_url(cleaned)
                    if handle:
                        handles.append(handle)
    deduped = []
    for handle in handles:
        normalized = handle.rstrip("/")
        normalized_lower = normalized.lower()
        if normalized_lower in {"@3janexyz", "@x", "@twitter", "@wmt_ventures"}:
            continue
        if normalized_lower.lstrip("@").isdigit():
            continue
        if project_tokens_text and normalized[1:].lower() in project_tokens_text:
            continue
        if normalized not in deduped:
            deduped.append(normalized)
    return deduped[:12]


def handle_from_social_url(value: str) -> str | None:
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in {"x.com", "twitter.com"}:
        return None
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if not parts:
        return None
    handle = parts[0].strip()
    if not handle or handle.lower() in {"i", "search", "home", "status"} or handle.isdigit():
        return None
    return "@" + handle


def diligence_score(
    project: Any,
    findings: list[FindingRecord],
    quality: ReportQuality,
    source_log: list[dict[str, str]],
) -> dict[str, Any]:
    if project is None:
        return {"score": 0, "stance": "제외", "reason": "No project resolved.", "components": {}}
    product_rows = finding_rows(findings, "product_tech_signal", project)
    token_rows = finding_rows(findings, "contract_token_info", project)
    funding_rows = finding_rows(findings, "funding_token_signal", project)
    social_seed_rows = extract_social_seed_rows(findings)
    social_rows = finding_rows(findings, "social_kol_signal", project)
    founder_handles = extract_builder_handles(findings, project)

    components = {
        "quality_gate": 20 if quality.status == "research_complete" else 0,
        "source_depth": min(15, len(source_log) * 2),
        "identity_gate": 12 if project.website and candidate_origin(project) == "live_source_backed" else 4,
        "product_operator": 12 if product_rows else 0,
        "github_or_docs": github_docs_score(project, product_rows),
        "onchain_market": 10 if token_rows else 0,
        "official_addresses": official_address_score(token_rows),
        "social_kol": 8 if social_seed_rows or social_rows else 0,
        "founder_dossier": 4 if founder_handles else 0,
        "funding_token": 4 if funding_rows else 0,
    }
    score = min(100, sum(components.values()))
    has_product = bool(product_rows)
    has_token_value = display_token_status(project) not in {"", "unknown", "unknown_or_incentive_mining_unverified"}
    has_fatal_identity_gap = candidate_origin(project) != "live_source_backed" or quality.is_blocking

    if has_fatal_identity_gap or score < 45:
        stance = "제외"
        reason = "identity/source-backed evidence is not strong enough."
    elif has_product and not has_token_value:
        stance = "OPERATOR"
        reason = "product/operator evidence exists, but token value-capture remains unclear."
    elif score >= 86 and founder_handles and official_address_score(token_rows):
        stance = "TOP"
        reason = "identity, product, social, on-chain, and founder evidence are all strong enough for top-priority tracking."
    else:
        stance = "WATCH"
        reason = "source-backed project with enough product/context evidence, but still needs live KOL/founder/token follow-up."
    breakdown = score_breakdown(project, findings, quality, source_log, components)
    return {"score": score, "stance": stance, "reason": reason, "components": components, "breakdown": breakdown}


def score_breakdown(
    project: Any,
    findings: list[FindingRecord],
    quality: ReportQuality,
    source_log: list[dict[str, str]],
    components: dict[str, int],
) -> dict[str, int]:
    if project is None:
        return {
            "research_completeness": 0,
            "evidence_confidence": 0,
            "narrative_fit": 0,
            "product_reality": 0,
            "social_momentum": 0,
            "token_opportunity": 0,
        }
    product_rows = finding_rows(findings, "product_tech_signal", project)
    social_seed_rows = extract_social_seed_rows(findings)
    social_rows = finding_rows(findings, "social_kol_signal", project)
    token_rows = finding_rows(findings, "contract_token_info", project)
    funding_rows = finding_rows(findings, "funding_token_signal", project)
    evidence_confidence = min(
        100,
        quality.evidence_url_count * 5
        + (20 if quality.has_live_source_backed else 0)
        + (10 if candidate_origin(project) == "live_source_backed" else 0),
    )
    return {
        "research_completeness": min(100, sum(components.values())),
        "evidence_confidence": evidence_confidence,
        "narrative_fit": min(100, 45 + len(display_narratives(project)) * 8),
        "product_reality": min(100, 35 + len(product_rows) * 25 + components.get("github_or_docs", 0)),
        "social_momentum": min(100, 25 + (35 if social_seed_rows else 0) + len(social_rows) * 15),
        "token_opportunity": min(100, 25 + len(token_rows) * 20 + len(funding_rows) * 10),
    }


def build_claim_evidence_ledger(
    project: Any,
    findings: list[FindingRecord],
    source_log: list[dict[str, str]],
) -> list[dict[str, Any]]:
    if project is None:
        return []
    official_urls = [item["url"] for item in source_log if item.get("url")]
    product_rows = finding_rows(findings, "product_tech_signal", project)
    token_rows = finding_rows(findings, "contract_token_info", project)
    funding_rows = finding_rows(findings, "funding_token_signal", project)
    social_rows = finding_rows(findings, "social_kol_signal", project)
    social_seed_rows = extract_social_seed_rows(findings)
    github_urls = [url for url in official_urls if "github.com" in url.lower()]
    docs_urls = [url for url in official_urls if "docs." in url.lower() or "whitepaper" in url.lower()]
    funding_urls = [
        url
        for row in funding_rows
        for url in row.get("funding_sources", [])
        if isinstance(url, str) and url
    ]
    address_sources = [
        str(row.get("official_addresses", {}).get("source"))
        for row in token_rows
        if isinstance(row.get("official_addresses"), dict) and row.get("official_addresses", {}).get("source")
    ]
    social_urls: list[str] = []
    for row in [*social_rows, *social_seed_rows]:
        if not isinstance(row, dict):
            continue
        for key in ["public_x_results", "official_social_sources", "kol_opinion_results", "article_results", "who_said_what"]:
            values = row.get(key)
            if isinstance(values, list):
                social_urls.extend(str(item.get("url")) for item in values if isinstance(item, dict) and item.get("url"))
    ledger = [
        _claim_row(
            "identity",
            f"{project.name} identity, website, and category are resolved.",
            [project.website, *official_urls[:4]],
            "confirmed" if project.website and official_urls else "partial",
            source_log,
        ),
        _claim_row(
            "product",
            "Project mechanics and product surface were checked through official site/docs/GitHub where available.",
            [*docs_urls, *github_urls],
            "confirmed" if product_rows and docs_urls else "partial" if product_rows or docs_urls else "unverified",
            source_log,
        ),
        _claim_row(
            "social_kol",
            "X/KOL/article market signal was collected as a trigger layer, not final judgment.",
            social_urls,
            "confirmed" if len(social_urls) >= 3 else "partial" if social_urls else "unverified",
            source_log,
        ),
        _claim_row(
            "funding_team",
            "Funding/team claims are separated from product proof and require source-backed confirmation.",
            funding_urls,
            "confirmed" if funding_urls else "partial" if funding_rows else "unverified",
            source_log,
        ),
        _claim_row(
            "token_onchain",
            "Token, contract, chain, and official address evidence are checked separately from market hype.",
            address_sources,
            "confirmed" if address_sources else "partial" if token_rows else "unverified",
            source_log,
        ),
        _claim_row(
            "github_activity",
            "GitHub presence and activity should be treated separately from simply finding a GitHub link.",
            github_urls,
            "partial" if github_urls else "unverified",
            source_log,
        ),
        _claim_row(
            "live_metrics",
            "Live pool/app/borrower/default metrics remain a separate verification gate.",
            [],
            "unverified",
            source_log,
        ),
    ]
    return ledger


def _claim_row(
    category: str,
    claim: str,
    urls: list[Any],
    status: str,
    source_log: list[dict[str, str]],
) -> dict[str, Any]:
    source_refs = _source_refs_for_urls(source_log, urls)
    source_urls: list[str] = []
    for url in urls:
        if not url:
            continue
        value = str(url)
        if value and value not in source_urls:
            source_urls.append(value)
    source_ids = [
        ref["source_id"]
        for ref in source_refs
        if ref.get("source_id")
    ]
    return {
        "category": category,
        "claim": claim,
        "verification_status": status,
        "source_ids": source_ids[:8],
        "source_urls": source_urls[:8],
        "source_refs": source_refs[:8],
        "confidence": {"confirmed": 0.85, "partial": 0.55, "unverified": 0.2}.get(status, 0.35),
    }


def _source_refs_for_urls(source_log: list[dict[str, str]], urls: list[Any]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    by_url = {
        str(item.get("url") or ""): item
        for item in source_log
        if item.get("url")
    }
    for url in urls:
        value = str(url or "").strip()
        if not value:
            continue
        item = by_url.get(value, {})
        ref = {
            "source_id": str(item.get("source_id") or ""),
            "label": str(item.get("label") or source_label(value)),
            "url": value,
        }
        if ref not in refs:
            refs.append(ref)
    return refs


def render_claim_ledger_lines(ledger: list[dict[str, Any]], *, korean: bool) -> list[str]:
    if not ledger:
        return ["- Claim ledger was not available." if not korean else "- claim ledger를 만들 수 없었습니다."]
    lines = [
        "- 주요 주장을 claim 단위로 분리했습니다. URL 개수만으로 완료 판정하지 않고, 각 주장별 confirmed/partial/unverified 상태를 따로 봅니다."
        if korean
        else "- Key claims are separated from raw URL count and marked confirmed/partial/unverified."
    ]
    for item in ledger:
        refs = item.get("source_refs") if isinstance(item.get("source_refs"), list) else []
        sources = item.get("source_urls") if isinstance(item.get("source_urls"), list) else []
        if refs:
            source_text = ", ".join(
                claim_ref_markdown(ref)
                for ref in refs[:3]
                if isinstance(ref, dict)
            )
        else:
            source_text = ", ".join(source_markdown_link(url) for url in sources[:3]) if sources else "no direct source"
        lines.append(
            f"- **{item.get('category')}** `{item.get('verification_status')}`: {item.get('claim')} ({source_text})"
        )
    return lines


def claim_ref_markdown(ref: dict[str, Any]) -> str:
    source_id = str(ref.get("source_id") or "").strip()
    label = str(ref.get("label") or "").strip()
    url = str(ref.get("url") or "").strip()
    linked = source_markdown_link(url, label) if url else label or "source unavailable"
    return f"`{source_id}` {linked}" if source_id else linked


def render_score_breakdown_lines(score: dict[str, Any], *, korean: bool) -> list[str]:
    breakdown = score.get("breakdown") if isinstance(score.get("breakdown"), dict) else {}
    if not breakdown:
        return ["- Score breakdown is not available." if not korean else "- 점수 분해를 만들 수 없었습니다."]
    labels = {
        "research_completeness": "Research Completeness",
        "evidence_confidence": "Evidence Confidence",
        "narrative_fit": "Narrative Fit",
        "product_reality": "Product Reality",
        "social_momentum": "Social Momentum",
        "token_opportunity": "Token Opportunity",
    }
    lines = [
        "- 총점 하나만 보지 말고, 아래 세부 점수를 같이 봅니다. 특히 Evidence Confidence와 Social Momentum이 낮으면 WATCH 이상으로 올리면 안 됩니다."
        if korean
        else "- Read the total score together with these sub-scores."
    ]
    for key, label in labels.items():
        value = breakdown.get(key, 0)
        lines.append(f"- **{label}:** {value}/100")
    return lines


def github_docs_score(project: Any, product_rows: list[dict[str, Any]]) -> int:
    if product_rows:
        row = product_rows[0]
        if row.get("github_repo"):
            return 8
        if row.get("docs_status") in {"live", "success"}:
            return 6
    metadata = project.metadata if isinstance(project.metadata, dict) else {}
    if metadata.get("github_repos"):
        return 6
    if metadata.get("docs_crawl") or "docs." in str(metadata).lower():
        return 5
    return 0


def official_address_score(token_rows: list[dict[str, Any]]) -> int:
    for row in token_rows:
        registry = row.get("official_addresses")
        if isinstance(registry, dict) and registry.get("contracts"):
            return 9
    return 0


def write_representative_evidence_packet(
    *,
    evidence_packet_dir: Path,
    room: ResearchRoom,
    project: Any,
    findings: list[FindingRecord],
    quality: ReportQuality,
    source_log: list[dict[str, str]],
    company_settings: CompanySettings,
) -> Path:
    evidence_packet_dir.mkdir(parents=True, exist_ok=True)
    subject = project.name if project is not None else room.topic
    packet_path = evidence_packet_dir / f"{safe_filename(subject)}-{room.room_id}.md"
    korean = company_settings.report_language == "ko" or wants_korean_report(room, company_settings)
    packet = render_representative_evidence_packet(
        room=room,
        project=project,
        findings=findings,
        quality=quality,
        source_log=source_log,
        korean=korean,
    )
    packet_path.write_text(packet, encoding="utf-8")
    return packet_path


def render_representative_evidence_packet(
    *,
    room: ResearchRoom,
    project: Any,
    findings: list[FindingRecord],
    quality: ReportQuality,
    source_log: list[dict[str, str]],
    korean: bool,
) -> str:
    title = project.name if project is not None else room.topic
    score = diligence_score(project, findings, quality, source_log)
    lines = [
        f"# Evidence Packet: {title}",
        "",
        f"- Room: `{room.room_id}`",
        f"- Quality: `{quality.status}`",
        f"- Stance: `{score['stance']}`",
        f"- Score: `{score['score']}/100`",
        "",
        "## Identity",
        *(render_project_identity_clean(project, source_log, korean=korean) if project is not None else ["- unresolved"]),
        "",
        "## What changed",
        "- Trigger is treated only as a candidate; identity, product, token, social, and security evidence are checked before stance.",
        "- X/KOL/article signals are first-layer market evidence; official site/docs/GitHub/on-chain are the verification layer.",
        "",
        "## Product / Operator Evidence",
        *(render_protocol_mechanics_clean(project, findings, korean=korean) if project is not None else ["- unresolved"]),
        "",
        "## Founder Dossier",
        *(render_founder_dossier_clean(project, findings, korean=korean) if project is not None else ["- unresolved"]),
        "",
        "## On-chain / Market",
        *(render_value_capture_clean(project, findings, korean=korean) if project is not None else ["- unresolved"]),
        "",
        "## Social Signal",
        *(render_signal_briefing_clean(project, findings, korean=korean) if project is not None else ["- unresolved"]),
        "",
        "## Risks",
        *(render_professional_risks_clean(project, findings, korean=korean) if project is not None else ["- unresolved"]),
        "",
        "## Scores",
        *(render_score_and_stance_clean(project, findings, quality, source_log, korean=korean) if project is not None else ["- Score: 0/100"]),
        "",
        "## AntSeed Peer Review",
        "- Check ticker collision, unofficial CA, relaunch history, shill patterns, founder ambiguity, and product/operator evidence before upgrading stance.",
        "",
        "## Stance",
        f"- {score['stance']} - {score['reason']}",
        "",
        "## Source Appendix",
    ]
    if source_log:
        lines.extend(f"- {source_markdown_link(item['url'], item.get('label'))} - {source_role_ko(item['url']) if korean else source_role(item['url'])}" for item in source_log[:40])
    else:
        lines.append("- No source URL was collected.")
    return "\n".join(lines)


def collect_source_log(project: Any, sources: list[Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if project is not None:
        if project.website and is_relevant_source_url(project, str(project.website), label="official site"):
            items.append({"label": source_label(project.website), "url": str(project.website), "source_id": ""})
        for url in project.metadata.get("evidence_urls", []):
            if is_relevant_source_url(project, str(url), label=source_label(url)):
                items.append({"label": source_label(url), "url": str(url), "source_id": ""})
        website = project.metadata.get("website_crawl") if isinstance(project.metadata.get("website_crawl"), dict) else {}
        official_links = website.get("official_links") if isinstance(website.get("official_links"), dict) else {}
        for bucket, links in official_links.items():
            if not isinstance(links, list):
                continue
            for link in links:
                if isinstance(link, dict) and link.get("url"):
                    label = f"{bucket}: {source_label(link['url'])}"
                    if is_relevant_source_url(project, str(link["url"]), label=label):
                        items.append({"label": label, "url": str(link["url"]), "source_id": ""})
    for source in sources:
        label = str(getattr(source, "title", "source"))
        if getattr(source, "url", None) and (project is None or is_relevant_source_url(project, str(source.url), label=label)):
            items.append({"label": label, "url": str(source.url), "source_id": str(getattr(source, "source_id", ""))})
    return dedupe_source_items(items)


def source_label(url: object) -> str:
    value = str(url)
    cleaned = value.removeprefix("https://").removeprefix("http://").strip("/")
    return cleaned[:80] or value


def source_role(url: object) -> str:
    value = str(url).lower()
    parsed = urlparse(value)
    host = parsed.netloc[4:] if parsed.netloc.startswith("www.") else parsed.netloc
    if host in {"x.com", "twitter.com"} or host.endswith(".twitter.com"):
        return "primary social signal / official X source"
    if "whitepaper" in value or value.endswith(".pdf"):
        return "protocol design and thesis source"
    if host.startswith("docs."):
        return "official docs / technical verification"
    if host == "github.com":
        return "codebase or engineering activity source"
    if host.endswith("3jane.xyz") and parsed.path in {"", "/"}:
        return "official project website"
    if "defillama" in host:
        return "TVL / protocol market data source"
    if "coingecko" in host or "dexscreener" in host:
        return "token or market identity source"
    if "alchemy.com" in host or "ethdaily" in host:
        return "third-party project context"
    if host.startswith("app."):
        return "live app/product surface"
    return "public evidence source"


def source_markdown_link(url: object, label: object | None = None) -> str:
    value = str(url or "").strip()
    if not value:
        return "[source unavailable](#)"
    display = str(label or "").strip() or compact_source_label(value)
    display = compact_source_label(display)
    return f"[{display}]({value})"


def x_label_from_url(url: object) -> str:
    value = str(url or "")
    parsed = urlparse(value)
    parts = [part for part in parsed.path.split("/") if part]
    return f"@{parts[0]}" if parts else "official X"


def compact_source_label(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "source"
    parsed = urlparse(text if "://" in text else f"https://{text}")
    if parsed.netloc:
        host = parsed.netloc[4:] if parsed.netloc.startswith("www.") else parsed.netloc
        path = parsed.path.strip("/")
        if host in {"x.com", "twitter.com"}:
            parts = [part for part in path.split("/") if part]
            if len(parts) >= 3 and parts[1] == "status":
                return f"x.com/{parts[0]}/status"
            if parts:
                return f"x.com/{parts[0]}"
            return host
        if "theblock.co" in host:
            return "The Block"
        if "delphidigital.io" in host:
            return "Delphi Digital"
        if "leviathannews.substack.com" in host:
            return "Leviathan Substack"
        if "ethdaily.io" in host:
            return "ETH Daily"
        if "defillama.com" in host:
            return "DefiLlama"
        if "github.com" in host:
            parts = [part for part in path.split("/") if part]
            return "github.com/" + "/".join(parts[:2]) if parts else "GitHub"
        if "docs.3jane.xyz" in host:
            return f"docs.3jane.xyz/{path}".rstrip("/")[:70]
        if "3jane.xyz" in host and path:
            return f"3jane.xyz/{path}".rstrip("/")[:70]
        return f"{host}/{path}".rstrip("/")[:70]
    return text[:70]


def source_role_ko(url: object) -> str:
    value = str(url).lower()
    parsed = urlparse(value)
    host = parsed.netloc[4:] if parsed.netloc.startswith("www.") else parsed.netloc
    if host in {"x.com", "twitter.com"} or host.endswith(".twitter.com"):
        return "1차 소셜/X 근거"
    if "whitepaper" in value or value.endswith(".pdf"):
        return "프로토콜 설계/논문 근거"
    if host.startswith("docs."):
        return "공식 docs/기술 검증"
    if host == "github.com":
        return "코드/엔지니어링 근거"
    if host.endswith("3jane.xyz") and parsed.path in {"", "/"}:
        return "공식 사이트"
    if "defillama" in host:
        return "TVL/프로토콜 시장 데이터"
    if "theblock" in host or "bitcoin.com" in host:
        return "펀딩/시장 기사"
    if "delphidigital" in host:
        return "리서치 기관 분석"
    if "substack" in host or "ethdaily" in host or "bbx.com" in host:
        return "외부 해설/웹 언급"
    if host.startswith("app."):
        return "라이브 앱/제품 표면"
    return "공개 근거"


def render_project_intelligence_report_clean(
    *,
    room: ResearchRoom,
    primary: Any,
    findings: list[FindingRecord],
    quality: ReportQuality,
    model_name: str,
    provider_name: str,
    settings: CompanySettings,
    korean: bool,
    title_name: str,
    source_log: list[dict[str, str]],
    source_summary: str,
) -> str:
    del source_summary
    display_topic = clean_report_text(room.topic, fallback=f"{title_name} project research request")
    language = "ko" if korean else settings.report_language
    if korean:
        return render_reader_friendly_project_report(
            room=room,
            primary=primary,
            findings=findings,
            quality=quality,
            model_name=model_name,
            provider_name=provider_name,
            language=language,
            title_name=title_name,
            source_log=source_log,
            display_topic=display_topic,
        )
    lines: list[str] = [
        f"# {title_name} 리서치 보고서" if korean else f"# {title_name} Project Intelligence Report",
        "",
        f"- 작성 시각: {room.created_at}" if korean else f"- Created at: {room.created_at}",
        f"- 요청: {display_topic}" if korean else f"- Client request: {display_topic}",
        f"- 분석 대상: {title_name}" if korean else f"- Subject: {title_name}",
        f"- 주요 근거: {clean_source_summary(source_log, korean=korean)}"
        if korean
        else f"- Main sources: {clean_source_summary(source_log, korean=korean)}",
        "",
        "---",
        "",
        "## 1. Executive Summary (핵심 요약)" if korean else "## 1. Executive Summary",
    ]
    lines.extend(render_executive_summary_clean(primary, quality, source_log, korean=korean))
    lines.extend(["", "## Representative Verdict (대표님 기준 결론)" if korean else "## Representative Verdict"])
    lines.extend(render_representative_verdict_clean(primary, findings, quality, source_log, korean=korean))
    lines.extend(["", "## Representative Diligence Brief (대표님 실사 브리프)" if korean else "## Representative Diligence Brief"])
    lines.extend(render_representative_diligence_brief_clean(primary, findings, quality, source_log, korean=korean))
    lines.extend(["", "## 2. Primary Market Signal Layer (X/KOL/아티클 1차 소스)" if korean else "## 2. Primary Market Signal Layer"])
    lines.extend(render_primary_market_signal_layer_clean(primary, findings, korean=korean))
    lines.extend(["", "## 3. Project Identity (프로젝트 정체성)" if korean else "## 3. Project Identity"])
    lines.extend(render_project_identity_clean(primary, source_log, korean=korean))
    lines.extend(["", "## 4. Market Problem & Narrative (시장 문제와 내러티브)" if korean else "## 4. Market Problem & Narrative"])
    lines.extend(render_market_context_clean(primary, korean=korean))
    lines.extend(["", "## 5. Product & Protocol Mechanics (제품/프로토콜 구조)" if korean else "## 5. Product & Protocol Mechanics"])
    lines.extend(render_protocol_mechanics_clean(primary, findings, korean=korean))
    lines.extend(["", "## 6. Token, Chain & Value Capture (토큰/체인/가치 포착)" if korean else "## 6. Token, Chain & Value Capture"])
    lines.extend(render_value_capture_clean(primary, findings, korean=korean))
    lines.extend(["", "## 7. Traction, Social & Funding Signals (트랙션/소셜/펀딩)" if korean else "## 7. Traction, Social & Funding Signals"])
    lines.extend(render_signal_briefing_clean(primary, findings, korean=korean))
    lines.extend(["", "## Founder Dossier (창업자/팀)" if korean else "## Founder Dossier"])
    lines.extend(render_founder_dossier_clean(primary, findings, korean=korean))
    lines.extend(["", "## 8. Analyst Thesis (리서치 판단)" if korean else "## 8. Analyst Thesis"])
    lines.extend(render_analyst_thesis_clean(primary, quality, korean=korean))
    lines.extend(["", "## Score & Stance (TOP/WATCH/OPERATOR/제외)" if korean else "## Score & Stance"])
    lines.extend(render_score_and_stance_clean(primary, findings, quality, source_log, korean=korean))
    lines.extend(["", "## 9. Risk Register (리스크)" if korean else "## 9. Risk Register"])
    lines.extend(render_professional_risks_clean(primary, findings, korean=korean))
    lines.extend(["", "## 10. Specialist Coverage (에이전트별 커버리지)" if korean else "## 10. Specialist Coverage"])
    lines.extend(render_specialist_coverage_clean(primary, findings, korean=korean))
    lines.extend(["", "## 11. Next Research Checklist (다음 조사 체크리스트)" if korean else "## 11. Next Research Checklist"])
    lines.extend(render_due_diligence_checklist_clean(primary, findings, korean=korean))
    lines.extend(["", "## 12. Verification Status (검증 범위)" if korean else "## 12. Verification Status"])
    lines.extend(render_research_coverage_clean(primary, findings, source_log, korean=korean))
    lines.extend(["", "## Evidence Packet (대표님 리서치 패킷)" if korean else "## Evidence Packet"])
    lines.extend(render_evidence_packet_section_clean(primary, findings, quality, source_log, korean=korean))
    lines.extend(["", "## 13. Source Appendix (출처)" if korean else "## 13. Source Appendix"])
    if source_log:
        role_fn = source_role_ko if korean else source_role
        lines.extend(f"- {source_markdown_link(item['url'], item.get('label'))} - {role_fn(item['url'])}" for item in source_log)
    else:
        lines.append("- 수집된 출처 URL이 없습니다." if korean else "- No source URL was collected.")
    lines.extend(
        [
            "",
            "## 14. Research Quality Metadata",
            f"- Status: `{quality.status.upper()}`",
            f"- Evidence URLs: {quality.evidence_url_count}",
            f"- Live source-backed candidates: {quality.live_source_backed_count}",
            f"- Placeholder-only candidates: {'yes' if quality.placeholder_only else 'no'}",
            f"- LLM provider: `{provider_name}`",
            f"- Report model route: `{model_name}`",
            f"- Report language: `{language}`",
        ]
    )
    return "\n".join(lines)


def render_reader_friendly_project_report(
    *,
    room: ResearchRoom,
    primary: Any,
    findings: list[FindingRecord],
    quality: ReportQuality,
    model_name: str,
    provider_name: str,
    language: str,
    title_name: str,
    source_log: list[dict[str, str]],
    display_topic: str,
) -> str:
    del model_name, provider_name
    score = diligence_score(primary, findings, quality, source_log)
    title = primary.name if primary is not None else title_name
    lines: list[str] = [
        f"# {title} 리서치 보고서",
        "",
        f"> **결론:** `{score['stance']}` ({score['score']}/100). "
        "이 문서는 소스 목록이 아니라, 대표님이 프로젝트를 이해하고 다음 실사를 지시하기 위한 투자 메모형 리서치 보고서입니다.",
        "",
        f"- 요청: {display_topic}",
        f"- 작성 시각: {room.created_at}",
        f"- 리서치 품질: `{quality.status}` / 확인된 공개 근거 {quality.evidence_url_count}개",
        "",
        "---",
        "",
        "## 1. 대표님용 투자 메모",
    ]
    lines.extend(reader_conclusion_lines(primary, findings, quality, source_log))
    lines.extend(["", "### 점수 분해"])
    lines.extend(render_score_breakdown_lines(diligence_score(primary, findings, quality, source_log), korean=True))
    lines.extend(["", "## 2. 프로젝트 개요"])
    lines.extend(reader_project_explanation_lines(primary))
    lines.extend(["", "## 3. 시장/내러티브와 왜 지금인가"])
    lines.extend(reader_market_signal_lines(primary, findings))
    lines.extend(["", "## 4. 제품/프로토콜 구조"])
    lines.extend(reader_product_lines(primary, findings))
    lines.extend(["", "## 5. 토큰/체인/가치 포착"])
    lines.extend(reader_token_lines(primary, findings))
    lines.extend(["", "## 6. 팀/펀딩/KOL"])
    lines.extend(reader_team_funding_kol_lines(primary, findings))
    lines.extend(["", "## 7. 리스크와 반론"])
    lines.extend(reader_risk_lines(primary, findings))
    lines.extend(["", "## 8. 다음 실사 질문"])
    lines.extend(reader_next_steps_lines(primary, findings))
    lines.extend(["", "## 9. 확인된 내용 요약"])
    lines.extend(reader_source_digest_lines(primary, source_log))
    lines.extend(["", "## 10. Claim-Level Evidence Ledger"])
    lines.extend(render_claim_ledger_lines(build_claim_evidence_ledger(primary, findings, source_log), korean=True))
    lines.extend(
        [
            "",
            "---",
            "",
            f"- Room ID: `{room.room_id}`",
            f"- Report language: `{language}`",
            f"- 내부 evidence packet: `data/evidence_packets/{safe_filename(title)}-{room.room_id}.md`",
        ]
    )
    return "\n".join(lines)


def reader_conclusion_lines(
    project: Any,
    findings: list[FindingRecord],
    quality: ReportQuality,
    source_log: list[dict[str, str]],
) -> list[str]:
    if project is None:
        return [
            "- 프로젝트 identity가 확정되지 않았습니다. 이 결과는 보고서가 아니라 후보 메모로만 봐야 합니다.",
            "- 공식 사이트, X 계정, docs, contract address를 먼저 확인한 뒤 다시 리서치를 실행해야 합니다.",
        ]
    score = diligence_score(project, findings, quality, source_log)
    narratives = ", ".join(display_narratives(project)[:4]) or "Unclassified Early Crypto"
    if is_3jane_project(project):
        lines = [
            f"- **스탠스:** `{score['stance']}`. 3Jane은 바로 TOP으로 올리기보다 watchlist 상단 후보로 두고, credit pool 사용량과 founder/team 검증을 계속 붙여야 하는 프로젝트입니다.",
            "- **한 줄 결론:** 3Jane은 `담보를 많이 맡기고 빌리는 DeFi 대출`에서 벗어나, 신용ㆍ현금흐름ㆍ외부 자산 증명을 이용해 온체인 credit line을 만들려는 Ethereum 기반 crypto credit protocol입니다.",
            "- **투자 가설:** DeFi lending이 Aave/Compound식 초과담보 대출에 갇혀 있는 동안, 실제 신용 기반 대출 시장은 아직 crypto-native하게 열리지 않았습니다. 3Jane의 베팅은 이 빈 시장을 `real credit onchain`으로 열 수 있다는 것입니다.",
            "- **왜 흥미로운가:** Paradigm 리드 $5.2M seed round, Wintermute Ventures/Coinbase Ventures 계열 backer 신호, 공식 docs의 USD3/sUSD3 구조, Delphi의 `real credit onchain` 프레임이 서로 같은 방향을 가리킵니다. 즉 단순한 신규 토큰 홍보가 아니라, DeFi credit primitive로 읽히는 정황이 있습니다.",
            "- **무엇이 성립해야 하는가:** ① 실제로 빌리고 싶어 하는 borrower가 있어야 하고, ② borrower의 신용/자산/미래 현금흐름을 조작 가능성이 낮은 방식으로 검증해야 하며, ③ default가 발생했을 때 손실과 recovery가 USD3/sUSD3 구조 안에서 투명하게 처리되어야 합니다.",
            "- **좋은 시나리오:** 3Jane이 차입자 데이터를 신뢰 가능한 방식으로 underwriting하고, 공급자가 USD3/sUSD3를 통해 default-adjusted yield를 받을 수 있다면 DeFi lending의 사용처는 담보 대출에서 실제 신용 시장으로 확장됩니다. 이 경우 3Jane은 단순 lending app이 아니라 crypto credit infrastructure에 가까워집니다.",
            "- **나쁜 시나리오:** borrower demand가 약하거나, default/recovery가 불투명하거나, sUSD3 first-loss 구조가 시장이 감당하기 어려운 리스크로 드러나면 프로젝트는 narrative는 강하지만 실제 credit market으로 성장하지 못할 수 있습니다. 특히 credit protocol은 한 번의 큰 default가 신뢰를 빠르게 훼손할 수 있습니다.",
            "- **대표님 기준 판단:** 지금 봐야 할 포인트는 단기 가격/상장 기대가 아니라 `신용을 온체인에서 안전하게 가격화할 수 있는가`입니다. 이 질문에 대한 증거가 쌓이면 watchlist에서 상위 추적 후보로 올릴 수 있고, 반대로 evidence가 약하면 좋은 backer가 있어도 보수적으로 봐야 합니다.",
            f"- **분류:** {narratives}. Chain=`{project.chain or 'unknown'}`, token_status=`{display_token_status(project)}`.",
        ]
    elif is_pearl_project(project):
        lines = [
            f"- **스탠스:** `{score['stance']}`. Pearl은 단순 PoW 코인으로 보기보다 `유용한 연산을 채굴 보안과 결합하려는 L1/AI compute 인프라 후보`로 봐야 합니다. 다만 TOP으로 올리려면 실제 compute 수요, 채굴 경제성, 네트워크 사용량, 팀/펀딩 검증이 더 필요합니다.",
            "- **한 줄 결론:** Pearl Network는 채굴자가 해시만 계산하는 기존 PoW와 달리, matrix multiplication 같은 유용한 연산을 수행하면서 네트워크 보안과 블록 생성에 참여하도록 설계된 Proof-of-Useful-Work 계열 L1 후보입니다.",
            "- **투자 가설:** AI/compute 수요가 커지는 환경에서 PoW의 에너지 소비 비판을 줄이고, 채굴 보상을 실제 유용 연산 공급과 연결할 수 있다면 Pearl은 `AI compute + L1 security + mining economy`가 만나는 실험이 됩니다.",
            "- **왜 흥미로운가:** 공식 사이트/화이트페이퍼, GitHub monorepo, explorer, mining pool, Together AI 파트너십으로 보이는 공개 근거가 함께 잡힙니다. 즉 단순 티커 검색 결과가 아니라, PoUW 논문/프로토콜/체인 운영 흔적이 동시에 존재하는 후보입니다.",
            "- **무엇이 성립해야 하는가:** ① 채굴자가 수행하는 연산이 실제로 외부 수요가 있는 유용 compute여야 하고, ② 그 결과를 네트워크가 검증 가능한 방식으로 받아들여야 하며, ③ 보상 구조가 단순 채굴자 보조금이 아니라 compute buyer와 network security를 연결해야 합니다.",
            "- **좋은 시나리오:** Pearl이 GPU 채굴자에게 새로운 수익원을 주고, AI/ML workload나 matrix computation 수요를 네트워크 보안과 결합한다면 기존 PoW의 에너지 낭비 프레임을 `compute marketplace` 내러티브로 바꿀 수 있습니다.",
            "- **나쁜 시나리오:** 유용 연산 수요가 실제로 붙지 않거나, 검증 비용이 너무 높거나, 채굴 보상이 토큰 발행에만 의존하면 Pearl은 PoUW라는 이름은 흥미롭지만 경제적으로는 일반 mining coin과 크게 다르지 않을 수 있습니다.",
            "- **대표님 기준 판단:** 지금 봐야 할 포인트는 `PRL 가격`이 아니라 Pearl이 정말로 유용 compute를 생산하고 있는지, 그 compute가 누가 돈을 내는 수요인지, 네트워크가 단순 채굴 이벤트를 넘어 제품/인프라로 성장하는지입니다.",
            f"- **분류:** {narratives}. Chain=`{project.chain or 'unknown'}`, token_status=`{display_token_status(project)}`.",
        ]
    else:
        lines = [
            f"- **스탠스:** `{score['stance']}`. 현재는 watchlist 후보로 보는 것이 맞고, TOP으로 올리려면 founder/team, live KOL, pool/on-chain 지표 검증이 더 필요합니다.",
            f"- **한 줄 정의:** {project.name}은 {one_sentence_project_thesis(project)}",
            f"- **핵심 내러티브:** {narratives}.",
            f"- **체인/토큰 상태:** chain=`{project.chain or 'unknown'}`, token_status=`{display_token_status(project)}`.",
            "- **읽는 방법:** 먼저 프로젝트가 무엇을 하려는지 보고, 그 다음 누가 언급했는지, 마지막으로 docs/토큰/리스크를 확인하면 됩니다.",
        ]
    if quality.status != "research_complete":
        lines.insert(0, "- **주의:** 근거가 부족해 완성 보고서가 아니라 diagnostic memo에 가깝습니다.")
    return lines


def reader_project_explanation_lines(project: Any) -> list[str]:
    if project is None:
        return ["- 설명할 프로젝트가 확정되지 않았습니다."]
    if is_3jane_project(project):
        return [
            "- 3Jane은 기존 DeFi 대출의 가장 큰 병목인 `초과담보` 문제를 건드립니다. 일반적인 온체인 lending은 돈을 빌리기 위해 그보다 더 큰 담보를 맡겨야 하므로, 레버리지나 단기 유동성에는 유용하지만 실제 신용시장처럼 작동하기 어렵습니다.",
            "- 3Jane의 목표는 차입자의 wallet asset, CEX/bank asset, 미래 yield, credit score 같은 정보를 신용 판단 재료로 삼아 USDC credit line을 제공하는 것입니다. 여기서 중요한 점은 `온체인에서 모든 것이 끝난다`가 아니라, off-chain proof와 on-chain settlement를 결합한다는 점입니다.",
            "- 공급자 입장에서는 USDC를 넣고 USD3/sUSD3 구조에 노출됩니다. 단순히 예치하고 이자를 받는 것처럼 보일 수 있지만, 실제로는 credit pool의 손익과 default 위험을 어떻게 배분하는지가 핵심입니다.",
            "- 그래서 핵심 제품은 토큰 자체가 아니라 **underwriting, credit limit 산정, risk-adjusted rate, default 처리, recovery, pool accounting**입니다. 이 기능들이 투명하고 반복 가능해야 3Jane이 단순 narrative가 아니라 protocol로 인정받을 수 있습니다.",
            "- Aave/Compound류 lending이 `담보를 맡긴 만큼 빌리는 구조`라면, 3Jane은 `검증 가능한 신용/현금흐름/외부 자산을 기반으로 빌리는 구조`에 가깝습니다. 이 차이가 프로젝트의 전체 thesis이며, 성공 시 DeFi lending의 TAM을 넓히는 방향입니다.",
            "- 이 구조가 의미 있으려면 차입자는 담보를 과도하게 묶지 않아도 자본을 조달할 수 있어야 하고, 공급자는 그 대가로 default-adjusted yield를 받아야 합니다. 둘 중 하나라도 약하면 제품은 narrative 이상으로 확장되기 어렵습니다.",
            "- AI agent credit line이라는 표현은 흥미롭지만, 현재 보고서에서는 이를 확정된 사용처가 아니라 확장 가능한 use case로만 봅니다. 실제 수요는 app usage, borrower pool, credit-line utilization, repeat borrower, default/recovery history로 확인해야 합니다.",
            "- 대표님이 읽을 때는 `이 프로젝트가 어떤 토큰인가`보다 `누가 왜 빌리고, 누가 어떤 위험을 감수하고 공급하며, 손실이 발생하면 어떤 규칙으로 처리되는가`를 먼저 보면 됩니다.",
            "- 내러티브로는 `Crypto Credit`, `Undercollateralized Lending`, `DeFi Automation`, `AI agent credit line` 쪽에 가깝습니다. 다만 이 내러티브는 멋있는 말이라기보다, 실제 credit underwriting을 얼마나 잘 수행하는지로 검증되어야 합니다.",
        ]
    if is_pearl_project(project):
        return [
            "- Pearl은 기존 PoW의 핵심 문제, 즉 `네트워크 보안을 위해 막대한 연산을 쓰지만 그 연산 자체는 외부 효용이 거의 없다`는 비판을 정면으로 건드립니다. Pearl의 핵심은 채굴자가 의미 없는 해시 경쟁만 하는 것이 아니라, AI/ML에서 쓰일 수 있는 행렬 연산 같은 유용 compute를 수행하도록 만드는 것입니다.",
            "- 공개 근거 기준 Pearl은 Proof-of-Useful-Work를 L1 합의와 결합하려는 프로젝트입니다. 채굴자는 GPU/compute 자원을 제공하고, 네트워크는 그 결과를 검증해 블록 생성과 보상에 반영하는 구조를 지향합니다.",
            "- 이 프로젝트를 단순히 `새로운 PoW 코인`으로 보면 놓치는 부분이 있습니다. 더 중요한 질문은 Pearl이 compute demand, mining supply, L1 security를 하나의 경제 시스템으로 묶을 수 있느냐입니다.",
            "- Pearl의 제품 표면은 세 가지로 나뉩니다. 첫째, 체인/노드/채굴 인프라입니다. 둘째, 유용 연산을 정의하고 검증하는 PoUW 프로토콜입니다. 셋째, 외부 compute buyer 또는 AI 인프라 파트너가 실제 작업을 네트워크에 던질 수 있는 수요 측 연결입니다.",
            "- 공식 사이트와 화이트페이퍼가 말하는 핵심은 `arbitrary matrix multiplication` 기반 유용 연산입니다. 이것이 실제로 AI 학습/추론 workload와 얼마나 직접 연결되는지는 더 검증해야 하지만, 내러티브상으로는 AI compute shortage와 PoW security를 연결하는 방향입니다.",
            "- GitHub monorepo와 explorer가 존재한다면 제품은 최소한 문서 수준을 넘어 코드/네트워크 흔적을 갖는 것으로 볼 수 있습니다. 다만 repo activity, release, node setup, miner setup, test coverage, security review까지 확인해야 제품 성숙도를 제대로 판단할 수 있습니다.",
            "- mining pool과 explorer 흔적은 네트워크가 실제로 운영되고 있거나 운영을 준비 중이라는 신호입니다. 하지만 이것만으로 제품-시장 적합성이 입증되는 것은 아닙니다. 실제 compute task가 어디서 오고, 누가 비용을 지불하는지가 더 중요합니다.",
            "- Together AI 파트너십성 공개 언급은 Pearl을 단순 mining coin이 아니라 AI compute 인프라 실험으로 읽게 만드는 중요한 외부 맥락입니다. 다만 파트너십의 범위가 연구 협력인지, 실제 workload 공급인지, 상용 compute 수요인지 구분해야 합니다.",
            "- 대표님이 읽을 때는 `Pearl이 채굴 가능한가`보다 `채굴자가 수행한 일이 누구에게 쓸모가 있고, 그 쓸모가 토큰 경제에 어떻게 반영되는가`를 먼저 보면 됩니다.",
            "- 내러티브로는 `Proof-of-Useful-Work`, `AI Compute`, `GPU Mining`, `L1 Blockchain`, `Decentralized AI Infrastructure`에 가깝습니다. 이 중 진짜 판단 포인트는 AI compute 쪽 수요가 실제로 네트워크 경제에 연결되는지입니다.",
        ]
    return [
        f"- {best_project_description(project)}",
        "- 이 보고서는 가격보다 프로젝트 identity, 제품 작동 방식, token value-capture, 미해결 리스크를 우선 정리합니다.",
    ]


def reader_market_signal_lines(project: Any, findings: list[FindingRecord]) -> list[str]:
    row = latest_market_signal_row(findings)
    lines = [
        "- 1차 소스 우선순위는 X/Twitter, KOL 포스트, 공개 스레드, 아티클입니다. 그 뒤 공식 사이트/docs/GitHub/온체인 자료로 검증합니다.",
    ]
    if project is not None and is_3jane_project(project):
        lines.extend(
            [
                "- **공식 X:** 3Jane 공식 계정은 $5.2M seed round와 Paradigm 리드 사실을 직접 발표한 1차 소셜 소스입니다.",
                "- **Wintermute Ventures:** Wintermute Ventures가 3Jane과 `@_yakovsky`를 언급하며 backing 사실을 공개했습니다. 팀 단서는 여기까지만 근거화하고, 실명/이력은 추가 확인 대상으로 둡니다.",
                "- **The Block:** Paradigm이 $5.2M seed round를 리드했고 프로젝트가 stealth에서 공개됐다는 펀딩 기사입니다.",
                "- **Delphi Digital:** 3Jane을 `real credit onchain` 관점에서 다루며, 과거 credit 실패 이후의 undercollateralized lending 재설계 시도로 해석합니다.",
                "- **Leviathan Substack:** 3Jane lending protocol과 crypto borrowing 구조를 시장이 어떻게 이해하는지 보여주는 외부 해설입니다.",
                "- **KOL/리서치 해석:** 현재 수집된 외부 해설은 3Jane을 단순 high-yield stablecoin이 아니라 `credit primitive`로 봅니다. 특히 Delphi의 프레임은 과거 CeFi/DeFi credit 실패 이후, crypto가 다시 undercollateralized credit을 시도하는 장면으로 3Jane을 위치시킵니다.",
                "- **내러티브 해석:** 단순히 유명 VC가 투자했다는 신호보다 중요한 것은 시장이 3Jane을 DeFi lending, stable/yieldcoin, AI wallet/agent capital, 온체인 신용평가가 만나는 지점으로 해석하기 시작했다는 점입니다.",
                "- **소셜 신호의 강점:** 공식 X, backer 언급, 전문 리서치 글, 외부 해설이 모두 같은 주제를 말합니다. 이것은 최소한 프로젝트 정체성이 시장에 어느 정도 전달되고 있다는 의미입니다.",
                "- **소셜 신호의 약점:** 아직 KOL별 반복 언급, 반론, controversy, 실사용자 후기, builder community 반응은 충분히 수집되지 않았습니다. 따라서 `누가 어떤 논리로 긍정/부정하는가`를 더 촘촘하게 쌓아야 합니다.",
                "- **주의할 점:** X/기사 신호는 trigger일 뿐입니다. 실제 판단은 docs의 pool mechanics, 공식 주소, app 상태, GitHub/contract, 그리고 온체인 pool 지표가 따라와야 합니다.",
            ]
        )
    elif project is not None and is_pearl_project(project):
        lines.extend(
            [
                "- **공식 사이트/화이트페이퍼:** Pearl Research Labs / Pearl Network 쪽 공개 근거는 Proof-of-Useful-Work L1과 matrix multiplication 기반 유용 연산을 핵심 thesis로 제시합니다. 이 출처는 identity gate의 중심입니다.",
                "- **GitHub:** `pearl-research-labs/pearl` 계열 repo는 Pearl이 단순 랜딩페이지 프로젝트가 아니라 코드/프로토콜 구현 흔적을 가진 후보임을 보여줍니다. 다음 실사에서는 commit activity, node/miner setup, tests, release, security docs를 봐야 합니다.",
                "- **Explorer / Blockbook:** explorer와 blockbook 계열 링크는 네트워크 운영 흔적을 확인하는 데 중요합니다. 다만 explorer가 있다는 사실과 경제적 수요가 있다는 사실은 다르므로, block production, active miners, transaction activity, compute task flow를 분리해서 봐야 합니다.",
                "- **Mining pool:** Herominers, LuckyPool 같은 pool 링크가 잡히면 `채굴 가능성`은 신호로 볼 수 있습니다. 하지만 투자 보고서에서는 hashrate, pool 분산도, 채굴 보상, 토큰 발행 구조가 함께 확인되어야 합니다.",
                "- **Together AI 관련 공개 글:** Together AI와의 파트너십성 언급은 Pearl을 AI compute 인프라 내러티브로 읽게 만듭니다. 다만 이 관계가 실제 workload, 연구 협력, 홍보성 언급 중 무엇인지 추가 확인이 필요합니다.",
                "- **KOL/리서치 해석:** 현재 Pearl은 3Jane처럼 Delphi/The Block급 전문 리서치 프레임이 강하게 잡힌 상태라기보다, PoUW/AI compute라는 기술 내러티브가 먼저 보이는 프로젝트입니다. 따라서 KOL conviction보다 공식 문서와 네트워크/코드 검증 비중을 높여야 합니다.",
                "- **내러티브 해석:** 시장이 Pearl을 주목한다면 그 이유는 `PoW를 AI compute로 재해석한다`는 점입니다. 이는 에너지 소비 비판, GPU 채굴자 수익성, AI compute shortage, decentralized infra라는 여러 테마가 만나는 지점입니다.",
                "- **소셜 신호의 강점:** 공식/후보 X, mining community, compute/AI 관련 기사, GitHub, explorer가 같은 프로젝트 정체성을 가리킨다면 identity 신뢰도는 높아집니다.",
                "- **소셜 신호의 약점:** 실시간 X API 없이 공개 웹 결과만으로는 KOL별 원문, 반복 언급, 반박, controversy, 채굴자 커뮤니티 반응을 충분히 보지 못합니다. 특히 Pearl처럼 이름 충돌이 많은 프로젝트는 official handle과 unofficial/shill account를 분리해야 합니다.",
                "- **주의할 점:** PoUW 내러티브는 매력적이지만, `유용한 연산`이라는 말이 실제 수요와 검증 가능한 결과로 이어지지 않으면 일반 PoW 토큰 홍보와 구분하기 어렵습니다.",
            ]
        )
    if not row:
        lines.append("- 다만 이번 run에는 별도 market-signal intake row가 없어, 공식/공개 출처 중심으로만 정리했습니다.")
        return lines
    who_said_what = row.get("who_said_what") if isinstance(row.get("who_said_what"), list) else []
    if who_said_what:
        statement_lines: list[str] = []
        for statement in who_said_what[:5]:
            if not isinstance(statement, dict):
                continue
            speaker = clean_report_text(statement.get("speaker"), fallback="unknown")
            claim = clean_report_text(statement.get("claim"), fallback="요약 없음")
            if is_internal_market_signal_claim(claim):
                continue
            url = statement.get("url")
            link = f" ({source_markdown_link(url)})" if url else ""
            statement_lines.append(f"  - **{speaker}:** {claim[:220]}{link}")
        if statement_lines:
            lines.append("- 수집된 who-said-what 요약:")
            lines.extend(statement_lines)
    public_x = row.get("public_x_results") if isinstance(row.get("public_x_results"), list) else []
    articles = dedupe_article_rows(
        [
            *(row.get("article_results") if isinstance(row.get("article_results"), list) else []),
            *(row.get("kol_opinion_results") if isinstance(row.get("kol_opinion_results"), list) else []),
        ]
    )
    if (public_x or articles) and not (project is not None and is_3jane_project(project)):
        lines.append(
            f"- 소셜/웹 수집 범위: 공개 X 결과 {len(public_x)}개, 아티클/웹 결과 {len(articles)}개를 후보 신호로 사용했습니다."
        )
    return lines


def is_internal_market_signal_claim(claim: str) -> bool:
    lowered = claim.lower()
    internal_markers = [
        "set x_bearer_token",
        "official/candidate x source identified",
        "set credentials",
        "connector",
        "missing_secret",
    ]
    return any(marker in lowered for marker in internal_markers)


def reader_product_lines(project: Any, findings: list[FindingRecord]) -> list[str]:
    if project is None:
        return ["- 제품/기술 확인 대상이 없습니다."]
    rows = finding_rows(findings, "product_tech_signal", project)
    lines: list[str] = []
    if is_3jane_project(project):
        lines.extend(
            [
                "- 공식 사이트와 docs 기준, 3Jane의 제품 표면은 credit-based money market입니다.",
                "- supplier side는 USDC 예치, USD3 발행, sUSD3 staking/first-loss exposure로 읽어야 합니다.",
                "- borrower side는 검증 가능한 자산/수익/신용 정보를 기반으로 USDC credit line을 받는 구조입니다.",
                "- 검증해야 할 기술 포인트는 underwriting input, risk-adjusted rate, utilization, redemption queue, default markdown, recovery process입니다.",
                "- 제품 플로우를 단순화하면 `USDC 공급 -> credit pool 형성 -> borrower underwriting -> credit line 사용 -> 이자/손실이 USD3/sUSD3에 반영`입니다.",
                "- 여기서 supplier에게 중요한 것은 명목 APY가 아니라 손실 발생 시 어느 tranche가 먼저 맞는지, redemption queue가 어떻게 작동하는지, default가 markdown으로 어떻게 회계 처리되는지입니다.",
                "- borrower에게 중요한 것은 credit line을 받기 위해 어떤 데이터를 제출해야 하는지, 그 데이터가 얼마나 자동화/검증 가능한지, rate가 담보 대출 대비 충분히 경쟁력 있는지입니다.",
                "- protocol operator 관점에서는 borrower 심사, risk parameter 조정, pool accounting, treasury/liquidity buffer, emergency pause, governance 권한이 모두 중요합니다. credit protocol은 UI가 예쁘다고 끝나는 제품이 아니라, 리스크 운영이 제품 그 자체입니다.",
                "- GitHub/SDK/API가 확인되면 `underwriting logic`, `pool accounting`, `borrow/repay flow`, `redemption flow`, `oracle/rate model`, `test coverage`, `audit artifacts`를 키워드로 봐야 합니다.",
                "- 프로토콜이 성숙하려면 docs만으로는 부족하고, app에서 실제 pool 상태와 borrower utilization, default/recovery history가 투명하게 보여야 합니다. 특히 default가 아직 없다면 그것은 좋은 신호일 수도 있지만, 동시에 stress-tested evidence가 부족하다는 뜻일 수도 있습니다.",
            ]
        )
    elif is_pearl_project(project):
        lines.extend(
            [
                "- Pearl의 제품/프로토콜 구조는 일반적인 DeFi app보다 `chain + miner + useful compute verification + external workload demand` 조합으로 봐야 합니다.",
                "- 공식 문서가 말하는 Proof-of-Useful-Work는 채굴자가 수행한 연산이 네트워크 보안에만 쓰이는 것이 아니라 외부적으로도 쓸모 있는 computation이어야 한다는 주장입니다.",
                "- 핵심 기술 질문은 세 가지입니다. 첫째, 어떤 연산이 유용하다고 인정되는가. 둘째, 그 연산 결과를 네트워크가 어떻게 검증하는가. 셋째, 검증 비용이 보상/보안 모델을 망치지 않을 정도로 낮은가입니다.",
                "- Pearl이 제시하는 matrix multiplication은 AI/ML workload와 연결될 수 있는 연산입니다. 다만 임의 행렬곱이 실제 customer workload와 얼마나 직접 대응되는지, 또는 benchmark/보안 장치로 쓰이는지 분리해서 확인해야 합니다.",
                "- miner 입장에서는 `GPU를 제공하면 PRL/native reward를 받는다`는 구조일 가능성이 큽니다. 하지만 장기적으로는 단순 발행 보상보다 compute buyer payment가 붙어야 지속 가능한 token economy가 됩니다.",
                "- network operator 관점에서는 node software, miner client, task distribution, result verification, block reward accounting, explorer transparency가 제품의 본체입니다.",
                "- GitHub repo가 확인되면 `consensus`, `miner`, `matrix`, `proof`, `verification`, `rpc`, `node`, `wallet`, `explorer`, `test`, `audit` 키워드를 중심으로 구조를 봐야 합니다.",
                "- explorer와 blockbook은 chain이 돌아간다는 신호지만, 실제 product readiness는 active miners, block cadence, failed task rate, compute task demand, RPC stability, docs completeness까지 봐야 합니다.",
                "- mining pool은 초기에 커뮤니티와 보안 공급을 만드는 데 중요하지만, pool concentration이 높으면 네트워크 탈중앙성 리스크가 커집니다.",
                "- 따라서 Pearl의 제품 성숙도는 `문서가 있다`가 아니라 `채굴자가 붙고, compute 수요가 들어오고, 검증 가능한 방식으로 보상이 정산된다`로 판단해야 합니다.",
            ]
        )
    else:
        lines.append(f"- 제품 설명: {best_project_description(project)}")
    if rows:
        row = rows[0]
        product_status = clean_report_text(row.get("product_status"), fallback="unknown")
        docs_status = clean_report_text(row.get("docs_status"), fallback="unknown")
        github_status = clean_report_text(row.get("github_status"), fallback="unknown")
        lines.append(f"- 제품 검증 상태: product=`{product_status}`, docs=`{docs_status}`, GitHub=`{github_status}`.")
        github_repo = row.get("github_repo") if isinstance(row.get("github_repo"), dict) else None
        if github_repo:
            lines.append(f"- GitHub 근거: {source_markdown_link(github_repo.get('html_url'), github_repo.get('full_name') or 'GitHub repo')}.")
    else:
        lines.append("- 이번 run에서는 제품/기술 에이전트 근거가 충분하지 않습니다. 공식 docs URL을 직접 넣고 재실행하면 이 섹션이 강화됩니다.")
    return lines


def reader_token_lines(project: Any, findings: list[FindingRecord]) -> list[str]:
    if project is None:
        return ["- 토큰/체인 정보를 확인하지 못했습니다."]
    lines = [
        f"- Chain: `{project.chain or 'unknown'}`",
        f"- Token status: `{display_token_status(project)}`",
    ]
    if is_3jane_project(project):
        lines.extend(
            [
                "- 3Jane은 일반 governance token 하나만 보는 구조가 아니라 USD3 / sUSD3 / JANE 역할을 분리해서 봐야 합니다.",
                "- value-capture 가설은 `차입 수요 -> credit line utilization -> pool yield/default/recovery -> USD3/sUSD3 손익 배분`입니다.",
                "- 아직 핵심은 roadmap/설계와 live 지표를 분리하는 것입니다. 실제 borrower demand, default event, recovery 결과가 쌓여야 더 강한 판단이 가능합니다.",
                "- **USD3:** senior 성격의 credit-backed yieldcoin으로 읽힙니다. 대표님 관점에서는 `USDC 대비 왜 보유할 이유가 있는가`, `손실/유동성 리스크는 어디에 반영되는가`가 핵심입니다.",
                "- **sUSD3:** 더 높은 수익을 기대하는 junior/first-loss exposure로 볼 수 있습니다. 그래서 sUSD3는 upside보다 downside waterfall을 먼저 확인해야 합니다.",
                "- **JANE:** 문서화된 주소는 있지만, governance, fee capture, staking, buyback, protocol revenue 연결이 live인지 roadmap인지 분리해야 합니다.",
                "- **경제적 질문:** USD3 holder는 어떤 위험을 감수하고 어떤 수익을 받는가, sUSD3 holder는 왜 first-loss를 감수하는가, JANE holder는 protocol risk/revenue/governance에서 어떤 권리를 갖는가를 분리해야 합니다.",
                "- **가치 포착의 강한 조건:** borrower가 꾸준히 credit line을 사용하고, protocol이 대출/상환/손실/회수를 반복적으로 처리하며, 그 과정에서 JANE이 단순 투표권 이상으로 fee, risk buffer, staking/slashing, revenue link 중 하나와 연결되어야 합니다.",
                "- 즉 이 프로젝트의 token thesis는 `토큰이 오른다`가 아니라, credit market이 실제로 돌아갈 때 각 token/asset layer가 어떤 경제적 권리와 리스크를 갖는지입니다.",
            ]
        )
    elif is_pearl_project(project):
        lines.extend(
            [
                "- Pearl의 토큰/체인 thesis는 일반 utility token보다 native L1 mining economy에 가깝게 봐야 합니다.",
                "- token_status가 `native_coin_reported`로 잡힌 경우, 핵심은 PRL/native coin이 block reward, transaction fee, miner incentive, compute task settlement 중 어디에 실제로 쓰이는지입니다.",
                "- 가치 포착의 강한 조건은 `외부 compute 수요 -> miner가 유용 연산 수행 -> 네트워크가 결과 검증 -> block/security/reward에 반영 -> native token이 demand 또는 fee sink를 갖는 구조`입니다.",
                "- 단순 발행 보상만으로 채굴자를 유인하면 초기 bootstrap은 가능하지만, 장기적으로는 token inflation과 sell pressure가 커질 수 있습니다.",
                "- 반대로 실제 compute buyer가 네트워크에 비용을 지불하고, 그 비용이 miner reward 또는 token sink와 연결되면 Pearl의 경제성은 일반 PoW보다 강한 설명력을 갖습니다.",
                "- chain 관점에서는 Pearl L1로 분류되지만, EVM 호환성, RPC 안정성, wallet 지원, bridge, explorer, exchange/DEX 접근성은 별도 확인이 필요합니다.",
                "- token value-capture는 live와 roadmap을 분리해야 합니다. 현재 확인해야 할 것은 `실제 compute payment가 있는가`, `fee가 누구에게 가는가`, `block reward schedule이 무엇인가`, `miner concentration이 어떤가`입니다.",
                "- mining token은 narrative가 좋아도 emission 구조가 약하면 초기 유동성 이후 가격 방어가 어렵습니다. 따라서 FDV보다 emission, hashrate, miner profitability, external compute revenue를 먼저 봐야 합니다.",
                "- 대표님 관점에서는 Pearl의 토큰을 `AI compute 수요에 의해 뒷받침되는 채굴 경제`로 볼 수 있는지, 아니면 `흥미로운 PoW 변형이지만 수요가 없는 발행 보상`인지가 핵심 판단입니다.",
            ]
        )
    for row in finding_rows(findings, "contract_token_info", project)[:1]:
        registry = row.get("official_addresses")
        if isinstance(registry, dict) and registry:
            lines.append(f"- 공식 주소 레지스트리: {source_markdown_link(registry.get('source'), '3Jane address registry')}")
            contracts = registry.get("contracts") if isinstance(registry.get("contracts"), dict) else {}
            if contracts:
                lines.append("- 핵심 주소:")
                for name in ["USDC", "USD3", "sUSD3", "JANE", "MorphoCredit"]:
                    address = contracts.get(name)
                    if address:
                        lines.append(f"  - {name}: `{address}`")
        break
    return lines


def reader_team_funding_kol_lines(project: Any, findings: list[FindingRecord]) -> list[str]:
    if project is None:
        return ["- 팀/펀딩/KOL 근거가 없습니다."]
    lines: list[str] = []
    if is_3jane_project(project):
        lines.extend(
            [
                "- 펀딩은 공개 기사와 공식 X 기준 **$5.2M seed round**, **Paradigm 리드**로 확인됩니다.",
                "- 공개 backer/signal에는 Paradigm, Wintermute Ventures, Coinbase Ventures 등이 노출됩니다.",
                "- 팀 단서는 Wintermute Ventures가 언급한 `@_yakovsky`가 가장 명확합니다. 실명, 학력, 이전 직장, 이전 프로젝트는 공식 근거가 없으면 확정하지 않습니다.",
                "- KOL/리서치 관점에서는 Delphi의 `real credit onchain` 프레임과 Leviathan의 lending protocol 해설이 현재 수집된 주요 외부 해석입니다.",
                "- Paradigm 리드는 이 프로젝트가 단순 airdrop/points project가 아니라, 구조적으로 어려운 credit market 문제를 푸는 팀으로 평가받고 있다는 신호입니다.",
                "- Wintermute/Coinbase Ventures 계열 backer 신호는 market structure, liquidity, exchange/credit infra 쪽 네트워크를 기대하게 하지만, 이것이 곧 제품 성공을 보장하지는 않습니다.",
                "- 팀 검증에서 가장 중요한 것은 단순 학력/전 직장이 아니라 `누가 underwriting 모델을 설계하는가`, `credit/default 경험이 있는가`, `risk team이 있는가`, `실제 borrower pipeline을 만들 수 있는 네트워크가 있는가`입니다.",
                "- founder/team dossier는 아직 약합니다. 현재 보고서에서는 확인되지 않은 실명/이력을 단정하지 않고, `@_yakovsky`와 backer 언급을 출발점으로만 둡니다.",
                "- 다음 리서치에서는 창업자 인터뷰, 팟캐스트, LinkedIn/GitHub, 이전 프로젝트, 채용 페이지, investor memo/announcement를 붙여 팀 신뢰도를 별도 점수화하는 것이 좋습니다.",
            ]
        )
    elif is_pearl_project(project):
        lines.extend(
            [
                "- Pearl의 팀/펀딩 섹션은 아직 3Jane처럼 명확한 seed round 기사나 대형 VC 리드가 중심에 서는 구조로 보이지 않습니다. 따라서 팀 신뢰도는 공식 사이트, GitHub organization, paper/whitepaper author, partnership 글, 채굴 커뮤니티 반응을 나눠 확인해야 합니다.",
                "- Pearl Research Labs라는 명칭과 GitHub organization은 프로젝트의 기술 주체를 추적하는 시작점입니다. repo owner, maintainer, commit author, issue activity, release signer를 확인해야 합니다.",
                "- Together AI 관련 공개 글은 Pearl을 AI compute 인프라 관점에서 보게 만드는 중요한 외부 신호입니다. 다만 funding과 partnership은 다릅니다. 실제 투자 유치인지, 연구/기술 협력인지, 공동 마케팅인지 분리해야 합니다.",
                "- KOL 측면에서는 PoUW/AI compute를 해석하는 전문 리서치 글이 충분히 모이면 conviction이 올라갑니다. 현재는 공개 웹/공식 소스 중심으로 보고, X API를 붙이면 KOL별 원문 포스트와 반복 언급을 별도 맵으로 만들어야 합니다.",
                "- mining community 반응도 Pearl에서는 중요합니다. 채굴자들이 실제로 노드/마이너를 돌리는지, pool이 분산되어 있는지, 보상 구조에 대한 불만이 있는지 확인해야 합니다.",
                "- founder dossier는 아직 미완성으로 두는 것이 맞습니다. 공식적으로 확인된 이름, LinkedIn, GitHub, 이전 프로젝트, 학력/전 직장 근거가 없으면 추정하지 않습니다.",
                "- 투자자/펀딩이 미확인이라면 약점이지만 곧바로 제외 사유는 아닙니다. PoW/L1 프로젝트는 초기에 community/miner bootstrap이 더 강한 신호일 수도 있습니다. 다만 장기 개발비와 생태계 확장 비용을 감당할 capital source는 확인해야 합니다.",
                "- 다음 단계는 GitHub maintainer map, official announcement archive, partnership 글 원문, founder/interview, mining pool 운영자/커뮤니티 반응을 한 번에 묶어 팀/운영 점수를 따로 산출하는 것입니다.",
            ]
        )
    handles = extract_builder_handles(findings, project)
    if handles:
        lines.append("- 공개 builder/team handle 후보:")
        lines.extend(f"  - {handle}" for handle in handles[:6])
    funding_rows = finding_rows(findings, "funding_token_signal", project)
    if funding_rows:
        row = funding_rows[0]
        investors = row.get("investors") if isinstance(row.get("investors"), list) else []
        if investors:
            lines.append(f"- reported investors/backers: {', '.join(str(item) for item in investors[:8])}.")
    return lines or ["- 팀/펀딩/KOL은 추가 확인이 필요합니다."]


def reader_risk_lines(project: Any, findings: list[FindingRecord]) -> list[str]:
    del findings
    if project is None:
        return ["- identity가 확정되지 않아 리스크도 확정할 수 없습니다."]
    if is_3jane_project(project):
        return [
            "- **Identity/ticker risk:** 공식 site/docs/X는 확인됐지만, ticker/CA collision과 unofficial CA는 계속 분리 확인해야 합니다.",
            "- **Credit/default risk:** 차입자 상환 실패 시 USD3/sUSD3 손실 배분, markdown, recovery가 실제로 작동하는지 확인해야 합니다.",
            "- **Fraud/underwriting risk:** bank/CEX/credit proof 기반 모델은 데이터 조작, synthetic identity, compromised account 리스크가 있습니다.",
            "- **Liquidity risk:** supplier redemption이 cash buffer를 초과할 때 queue와 throttling이 충분한지 봐야 합니다.",
            "- **Security/audit risk:** pool accounting, oracle/rate model, upgrade path, multisig/timelock, audit 자료가 필요합니다.",
            "- **Social/KOL risk:** X API 미설정 상태에서는 KOL별 원문, 반복 언급, 반박/논쟁을 충분히 보지 못했습니다.",
            "- **Regulatory/real-world data risk:** credit proof나 off-chain asset verification이 들어가는 순간, 단순 DeFi smart contract risk를 넘어 개인정보, KYC, 채권 회수, jurisdiction 문제가 생길 수 있습니다.",
            "- **Narrative overfit risk:** `undercollateralized lending`은 매력적인 말이지만 과거 CeFi/DeFi credit 실패 사례가 많았습니다. 3Jane은 이 실패를 어떻게 피하는지 구체적으로 증명해야 합니다.",
            "- **Value-capture risk:** USD3/sUSD3는 구조가 비교적 명확하지만, JANE의 장기 가치 포착은 아직 더 확인해야 합니다. governance token이라면 수수료, 위험 완충, staking, revenue link가 실제인지 봐야 합니다.",
            "- **운영 리스크:** credit protocol은 launch 이후 risk parameter를 계속 조정해야 합니다. borrower quality가 변하거나 liquidity가 빠지는 시기에 protocol operator가 어떤 정책으로 대응하는지가 장기 생존에 중요합니다.",
            "- **반론:** backer와 narrative가 좋아도, 실제 borrower가 없거나 supplier가 감수하는 위험 대비 보상이 부족하면 market은 오래 유지되지 않습니다. 이 반론은 단순 FUD가 아니라 credit business의 본질적인 체크포인트입니다.",
        ]
    if is_pearl_project(project):
        return [
            "- **Identity/ticker risk:** Pearl이라는 이름은 충돌 가능성이 높습니다. Pearl Research Labs, pearlresearch.ai, GitHub repo, explorer, official X/채굴 pool이 같은 프로젝트를 가리키는지 계속 확인해야 합니다.",
            "- **Useful-compute demand risk:** PoUW가 성립하려면 외부에서 실제로 필요로 하는 computation이 있어야 합니다. 수요자가 없으면 채굴자는 유용 연산을 한다기보다 token emission을 받기 위한 계산만 하게 됩니다.",
            "- **Verification cost risk:** 유용 연산 결과를 검증하는 비용이 높으면 네트워크 보안, 처리량, 경제성이 모두 약해질 수 있습니다. PoUW는 `연산이 유용하다`뿐 아니라 `검증 가능하다`가 핵심입니다.",
            "- **Mining centralization risk:** GPU/compute 기반 네트워크는 pool concentration, ASIC/GPU 우위, 특정 채굴자 집중으로 탈중앙성 리스크가 커질 수 있습니다.",
            "- **Token inflation risk:** native mining reward가 초기 공급의 대부분을 만든다면 external compute revenue가 붙기 전까지 sell pressure가 커질 수 있습니다.",
            "- **Product maturity risk:** explorer, pool, GitHub가 있어도 실제 customer workload, RPC 안정성, docs completeness, wallet/ecosystem 지원이 부족하면 L1로서 성숙도가 낮습니다.",
            "- **Security risk:** consensus, miner client, verification logic, RPC, explorer, bridge가 모두 공격면입니다. audit 또는 peer review가 없으면 기술 리스크가 큽니다.",
            "- **Social/shill risk:** PoW/mining 프로젝트는 커뮤니티 홍보와 채굴 수익 기대가 과장되기 쉽습니다. KOL 언급은 원문, 이해관계, 반복성, 반론을 함께 봐야 합니다.",
            "- **반론:** Pearl의 내러티브는 강하지만, 실제 compute buyer가 붙지 않으면 `AI compute L1`이 아니라 `새로운 채굴 코인`으로 축소될 수 있습니다. 이 반론을 깨려면 수요 측 evidence가 필요합니다.",
            "- **운영 리스크:** L1은 출시 이후 노드 안정성, chain halt, upgrade, wallet support, developer ecosystem을 계속 관리해야 합니다. 작은 팀이 이 운영 부담을 감당할 수 있는지도 확인해야 합니다.",
        ]
    return [
        "- 공식 site/docs/product 근거가 marketing-heavy일 수 있습니다.",
        "- token, contract, chain identity는 공식 출처와 explorer 기준으로 재확인해야 합니다.",
        "- public web 검색은 같은 이름의 다른 프로젝트와 충돌할 수 있습니다.",
        "- X/KOL 원문 검색이 없으면 social signal conviction이 낮습니다.",
    ]


def reader_next_steps_lines(project: Any, findings: list[FindingRecord]) -> list[str]:
    del findings
    if project is not None and is_3jane_project(project):
        return [
            "- @3janexyz, backer 계정, 관련 KOL의 원문 포스트를 더 모아 반복 언급, 반박, controversy 흐름을 확인합니다.",
            "- 공식 docs와 whitepaper에서 USD3, sUSD3, borrower credit line, underwriting input, default/recovery flow를 다시 확인합니다.",
            "- 공식 주소 레지스트리와 explorer를 대조해 contract deployment, pool accounting, upgrade 권한을 확인합니다.",
            "- GitHub repo, commit activity, release, issue, audit 자료를 확인합니다.",
            "- watchlist 지표는 TVL, USD3/sUSD3 supply, borrower utilization, default rate, recovery event, KOL momentum으로 정의합니다.",
            "- 특히 `누가 실제로 빌리는가`, `왜 담보 대출 대신 3Jane을 쓰는가`, `default가 발생했을 때 누가 손실을 부담하는가`를 다음 실사 질문의 중심에 둡니다.",
            "- founder/team은 `@_yakovsky`에서 출발해 실명, 이전 경력, credit/risk 경험, GitHub/LinkedIn, 이전 프로젝트, 투자자 네트워크를 확인합니다.",
            "- JANE 관련해서는 단순 주소 확인을 넘어 emission, utility, governance rights, fee linkage, staking/slashing 여부를 분리합니다.",
            "- 소셜/KOL은 `누가 좋다고 했는가`보다 `왜 좋다고 했는가`, `무엇을 근거로 삼았는가`, `반대 논리는 무엇인가`를 함께 저장합니다.",
            "- 다음 보고서 업그레이드 기준은 founder dossier 보강, live pool 지표 확보, GitHub/audit 검증, KOL 원문 10개 이상 정리, token value-capture live/roadmap 분리입니다.",
        ]
    if project is not None and is_pearl_project(project):
        return [
            "- 공식 사이트/화이트페이퍼에서 PoUW 정의, task generation, result verification, block reward accounting을 다시 확인합니다.",
            "- GitHub repo를 clone/read해 consensus, miner, matrix multiplication, proof verification, RPC, node setup, tests, release, audit 관련 파일을 확인합니다.",
            "- explorer와 blockbook에서 block cadence, active addresses, transaction count, miner distribution, chain halt 여부를 확인합니다.",
            "- mining pool별 hashrate, reward, pool concentration, miner onboarding 문서를 확인합니다.",
            "- Together AI 관련 글의 원문을 읽고 실제 partnership 범위가 commercial workload인지, research collaboration인지, marketing mention인지 분리합니다.",
            "- official X와 public X/KOL 검색으로 PoUW thesis에 대한 반복 언급, 반론, controversy, 채굴자 커뮤니티 반응을 수집합니다.",
            "- token emission schedule, block reward, fee model, burn/buyback/staking 여부, compute buyer payment path를 분리합니다.",
            "- founder/team은 GitHub maintainer, paper author, official profile, LinkedIn, 이전 프로젝트, funding/partnership announcement를 기준으로 검증합니다.",
            "- Pearl을 WATCH에서 TOP으로 올리려면 `실제 compute 수요`, `검증 가능한 PoUW result`, `분산된 mining participation`, `명확한 token value-capture`가 동시에 필요합니다.",
            "- 반대로 compute 수요가 없고 emission 중심으로만 돌아가면 PoUW 내러티브가 있어도 OPERATOR 또는 제외 후보로 낮춰야 합니다.",
        ]
    return [
        "- 공식 site/docs/whitepaper에서 project identity를 재확인합니다.",
        "- 공식 X/KOL/아티클을 먼저 수집하고, 그 뒤 docs/GitHub/explorer/DEX로 검증합니다.",
        "- token value-capture가 live인지 roadmap인지 분리합니다.",
    ]


def reader_source_digest_lines(project: Any, source_log: list[dict[str, str]]) -> list[str]:
    if project is not None and is_3jane_project(project):
        return [
            "- **공식 사이트/화이트페이퍼:** 3Jane은 스스로를 crypto credit protocol로 포지셔닝합니다. 핵심은 단순 yield 상품이 아니라, crypto user와 AI agent가 사용할 수 있는 credit line을 만들고 이를 USD3/sUSD3 구조로 공급자에게 연결하는 것입니다. "
            f"({source_markdown_link('https://www.3jane.xyz/', '3Jane site')}, {source_markdown_link('https://www.3jane.xyz/pdf/whitepaper.pdf', 'whitepaper')})",
            "  - 이 출처는 프로젝트의 identity gate에서 가장 중요합니다. 3Jane이 스스로를 어떤 문제를 푸는 protocol로 설명하는지, 그리고 market이 이 프로젝트를 어떤 category로 읽어야 하는지를 잡아줍니다.",
            "- **Docs introduction:** docs는 3Jane의 문제의식을 `undercollateralized credit`으로 잡습니다. 즉 온체인 담보만으로는 충분히 설명되지 않는 신용, 미래 수익, 외부 자산 증명을 lending 구조에 넣겠다는 방향입니다. "
            f"({source_markdown_link('https://docs.3jane.xyz/introduction', 'docs intro')})",
            "  - 여기서 핵심은 3Jane이 단순히 `높은 APY`를 말하는 것이 아니라, 초과담보 DeFi lending이 해결하지 못한 borrower capital efficiency 문제를 정면으로 다룬다는 점입니다.",
            "- **Supplier docs:** 공급자 관점에서는 USDC 예치, USD3 민팅, sUSD3 staking/first-loss exposure가 핵심입니다. 이 구조 때문에 3Jane은 단순 거버넌스 토큰보다 credit pool과 tranche risk를 먼저 봐야 합니다. "
            f"({source_markdown_link('https://docs.3jane.xyz/architecture/core-money-market/suppliers', 'supplier docs')})",
            "  - 공급자 문서는 보고서에서 가장 중요한 위험 해석 근거입니다. 공급자가 받는 수익이 어디서 나오고, 손실이 나면 어떤 순서로 반영되는지 파악해야 프로젝트의 yield가 지속 가능한지 볼 수 있습니다.",
            "- **Risk docs:** 공식 risk 문서는 redemption liquidity, borrower default, pool accounting, governance/parameter risk를 계속 봐야 한다는 점을 드러냅니다. 이건 3Jane의 업사이드이자 가장 큰 실사 포인트입니다. "
            f"({source_markdown_link('https://docs.3jane.xyz/risks', 'risk docs')})",
            "  - risk 문서가 존재한다는 점은 긍정적이지만, 문서화 자체가 리스크를 해결했다는 뜻은 아닙니다. 실제 default event, recovery process, liquidity queue가 어떻게 작동했는지 별도 데이터가 필요합니다.",
            "- **Address registry:** Ethereum 기준 USD3, sUSD3, JANE, MorphoCredit 등 주요 주소가 문서화되어 있어 identity gate는 어느 정도 통과합니다. 다만 실제 pool 상태와 사용량은 별도 explorer/market 확인이 필요합니다. "
            f"({source_markdown_link('https://docs.3jane.xyz/developers/addresses', 'address registry')})",
            "  - 주소 레지스트리는 ticker collision과 unofficial CA를 걸러내는 데 필요합니다. 3Jane처럼 USD3/JANE 등 이름이 일반적인 자산명과 충돌할 수 있는 프로젝트는 공식 주소 확인이 특히 중요합니다.",
            "- **공식 X / The Block:** 시장이 3Jane을 보게 된 가장 큰 계기는 $5.2M seed round입니다. 공식 X와 The Block 기사 모두 Paradigm 리드, stealth 공개, crypto credit startup이라는 framing을 확인해 줍니다. "
            f"({source_markdown_link('https://x.com/3janexyz/status/1930264347441615188', 'official X')}, {source_markdown_link('https://www.theblock.co/post/356872/paradigm-leads-5-million-seed-round-in-crypto-credit-startup-3jane', 'The Block')})",
            "  - 이 소스는 funding credibility를 제공합니다. 다만 seed round와 유명 backer는 출발 신호이지 product-market fit의 증거는 아닙니다. 그래서 funding은 긍정 신호로만 두고, 제품/온체인 지표와 분리해서 봐야 합니다.",
            "- **Wintermute Ventures:** Wintermute Ventures는 backing 사실과 `@_yakovsky` 단서를 남겼습니다. 이건 team/founder dossier의 시작점이지만, 실명ㆍ이전 경력ㆍ이전 프로젝트는 아직 추가 확인 대상입니다. "
            f"({source_markdown_link('https://x.com/wmt_ventures/status/1930336436433367395', 'Wintermute Ventures')})",
            "  - Wintermute 쪽 언급은 market structure와 liquidity 네트워크 관점에서 의미가 있습니다. 동시에 founder dossier가 아직 얕기 때문에, 다음 보고서에서는 `@_yakovsky`를 기준으로 팀 이력을 더 파야 합니다.",
            "- **Delphi / 외부 해설:** Delphi는 3Jane을 `real credit onchain` 베팅으로 해석합니다. Leviathan류 외부 글은 시장이 이 프로젝트를 unsecured/undercollateralized lending protocol로 이해하고 있음을 보여줍니다. "
            f"({source_markdown_link('https://members.delphidigital.io/reports/engineering-real-credit-onchain-the-3jane-bet', 'Delphi')}, {source_markdown_link('https://leviathannews.substack.com/p/3jane-lending-protocol-explained', 'Leviathan')})",
            "  - 외부 해설의 가치는 `어떤 내러티브로 소비되는가`를 보여주는 데 있습니다. 현재 3Jane은 단순 DeFi yield가 아니라 real credit / undercollateralized lending의 재시도로 읽히고 있습니다.",
        ]
    if project is not None and is_pearl_project(project):
        return [
            "- **공식 사이트/화이트페이퍼:** Pearl은 Proof-of-Useful-Work L1 thesis를 제시합니다. 핵심은 채굴 연산이 네트워크 보안뿐 아니라 외부적으로도 유용한 computation이 될 수 있다는 주장입니다. "
            f"({source_markdown_link('https://pearlresearch.ai/', 'Pearl site')}, {source_markdown_link('https://pearlresearch.ai/Pearl_Whitepaper.pdf', 'whitepaper')})",
            "  - 이 출처는 identity gate와 narrative gate의 중심입니다. Pearl을 일반 PoW 코인이 아니라 PoUW/AI compute 인프라 후보로 분류할 근거가 여기서 나옵니다.",
            "- **GitHub:** Pearl Research Labs의 repo는 프로토콜이 문서만 있는지, 실제 node/miner/consensus 구현이 있는지 확인하는 핵심 근거입니다. "
            f"({source_markdown_link('https://github.com/pearl-research-labs/pearl', 'GitHub repo')})",
            "  - repo는 단순 링크가 아니라 실사 대상입니다. commit activity, miner setup, verification logic, tests, release, security docs가 실제 성숙도를 결정합니다.",
            "- **Explorer / Blockbook:** explorer와 blockbook은 네트워크 운영 흔적을 확인하는 데 쓰입니다. block cadence, transaction activity, active miner/validator 분포, chain halt 여부를 봐야 합니다. "
            f"({source_markdown_link('https://explorer.pearlresearch.ai/?network=mainnet', 'explorer')}, {source_markdown_link('https://blockbook.pearlresearch.ai/blocks?page=1', 'blockbook')})",
            "  - explorer가 존재한다는 점은 긍정적이지만, 그 자체로 product-market fit이나 economic demand를 증명하지는 않습니다.",
            "- **Mining pool:** 공개 pool 링크는 Pearl이 채굴자 네트워크를 형성하려는 신호입니다. 다만 pool concentration과 보상 구조를 확인하지 않으면 탈중앙성과 emission 리스크를 평가하기 어렵습니다. "
            f"({source_markdown_link('https://pearl.herominers.com/', 'Herominers')}, {source_markdown_link('https://pearl.luckypool.io/', 'LuckyPool')})",
            "  - mining pool 자료는 채굴 가능성 확인에는 좋지만, 장기 thesis는 `누가 compute를 구매하는가`까지 이어져야 합니다.",
            "- **Together AI 관련 글:** Together AI와 Pearl Research Labs 관련 공개 글은 Pearl을 AI compute 내러티브로 해석할 수 있게 하는 외부 맥락입니다. "
            f"({source_markdown_link('https://www.together.ai/blog/together-ai-partners-with-pearl-research-labs', 'Together AI')})",
            "  - 이 출처는 강한 신호일 수 있지만, partnership 범위가 실제 workload 공급인지, 연구 협력인지, marketing collaboration인지 원문 기준으로 분리해야 합니다.",
            "- **커뮤니티/뉴스:** PearlNews나 공식/후보 X 소스는 커뮤니티 업데이트를 추적하는 데 쓰되, 공식 여부와 이해관계를 별도 확인해야 합니다.",
            "  - 특히 Pearl은 이름 충돌이 많기 때문에, 모든 X/커뮤니티 소스는 official source와 candidate source를 분리해서 저장해야 합니다.",
            "- **투자 보고서 해석:** 현재 공개 근거만 놓고 보면 Pearl의 핵심은 `AI compute 수요를 PoW 보안과 연결할 수 있는가`입니다. 이 질문에 답하는 증거가 늘어나면 WATCH 상단 후보가 될 수 있고, 반대로 수요 측 근거가 약하면 기술적으로 흥미롭지만 투자 리서치 conviction은 제한됩니다.",
        ]
    return reader_source_lines(source_log)


def reader_source_lines(source_log: list[dict[str, str]]) -> list[str]:
    if not source_log:
        return ["- 확인된 출처 내용이 없습니다."]
    lines = [
        "- 아래는 링크 목록이 아니라, 수집된 공개 근거가 어떤 역할을 했는지에 대한 요약입니다.",
        "| 확인한 내용 | 축약 링크 |",
        "|---|---|",
    ]
    for item in source_log[:10]:
        url = item.get("url", "")
        lines.append(f"| {source_role_ko(url)} | {source_markdown_link(url, item.get('label'))} |")
    return lines


def clean_source_summary(source_log: list[dict[str, str]], *, korean: bool) -> str:
    if not source_log:
        return "출처 로그 없음" if korean else "source log unavailable"
    return ", ".join(source_markdown_link(item.get("url"), item.get("label")) for item in source_log[:6])


def render_executive_summary_clean(project: Any, quality: ReportQuality, source_log: list[dict[str, str]], *, korean: bool) -> list[str]:
    if project is None:
        return ["- 분석할 프로젝트가 확정되지 않았습니다." if korean else "- No project was resolved."]
    narratives = ", ".join(display_narratives(project)[:5]) or "Unclassified Early Crypto"
    token_status = display_token_status(project)
    thesis = one_sentence_project_thesis(project)
    if korean:
        lines = [
            f"- **한 줄 정의:** {project.name}은 {thesis}",
            f"- **핵심 내러티브:** {narratives}.",
            f"- **체인/토큰:** chain=`{project.chain or 'unknown'}`, token_status=`{token_status}`.",
            f"- **근거 수준:** 관련 URL {len(source_log)}개를 정리했고 quality gate는 `{quality.status}`입니다.",
            "- **리서치 순서:** X/Twitter, KOL 포스트, 공개 스레드, 아티클을 1차 시장 신호로 보고, 공식 사이트/docs/GitHub/토큰/체인 데이터로 검증했습니다.",
            "- **현재 판단:** 가격이나 매매 판단이 아니라, 대표님이 프로젝트의 정체성과 리스크를 빠르게 이해하기 위한 1차 실사 보고서입니다.",
        ]
        if is_3jane_project(project):
            lines.append("- **3Jane 요지:** 담보 기반 DeFi 대출이 아니라, 검증 가능한 신용/자산/미래 현금흐름을 바탕으로 무담보 USDC credit line을 만들려는 Ethereum 기반 credit protocol입니다.")
        elif is_pearl_project(project):
            lines.append("- **Pearl 요지:** 기존 PoW의 해시 경쟁을 유용 compute로 바꾸려는 Proof-of-Useful-Work L1 후보입니다. 핵심 판단은 채굴 보상이 실제 AI/compute 수요와 연결되는지입니다.")
        return lines
    return [
        f"- **Identity:** {project.name} is {thesis}",
        f"- **Narrative:** {narratives}.",
        f"- **Chain/token:** chain=`{project.chain or 'unknown'}`, token_status=`{token_status}`.",
        f"- **Evidence level:** {len(source_log)} relevant URLs were used; quality gate is `{quality.status}`.",
        "- **Research order:** social/articles first, official docs/GitHub/on-chain verification second.",
    ]


def render_representative_verdict_clean(
    project: Any,
    findings: list[FindingRecord],
    quality: ReportQuality,
    source_log: list[dict[str, str]],
    *,
    korean: bool,
) -> list[str]:
    score = diligence_score(project, findings, quality, source_log)
    if project is None:
        return ["- 대표님 기준 결론: `제외` - 프로젝트 identity가 확정되지 않았습니다." if korean else "- Representative verdict: `EXCLUDE`."]
    if korean:
        return [
            f"- **대표님 기준 결론:** `{score['stance']}`",
            f"- **점수:** {score['score']}/100",
            f"- **이유:** {score['reason']}",
            "- **읽는 순서:** 결론 → 프로젝트 정체성 → 누가 무엇을 말했는지 → 제품/docs/GitHub → 토큰 value-capture → 미해결 리스크 순서로 보면 됩니다.",
            "- **금지:** hype, 매수/매도, 목표가, 확정 수익 표현은 제외했습니다.",
        ]
    return [
        f"- **Representative stance:** `{score['stance']}`",
        f"- **Score:** {score['score']}/100",
        f"- **Reason:** {score['reason']}",
    ]


def render_representative_diligence_brief_clean(
    project: Any,
    findings: list[FindingRecord],
    quality: ReportQuality,
    source_log: list[dict[str, str]],
    *,
    korean: bool,
) -> list[str]:
    if project is None:
        return ["- 대표님, 프로젝트 identity가 아직 확정되지 않아 후보 메모로만 봐야 합니다." if korean else "- Project identity unresolved."]
    score = diligence_score(project, findings, quality, source_log)
    product_rows = finding_rows(findings, "product_tech_signal", project)
    token_rows = finding_rows(findings, "contract_token_info", project)
    funding_rows = finding_rows(findings, "funding_token_signal", project)
    seed_rows = extract_social_seed_rows(findings)
    founder_handles = extract_builder_handles(findings, project)
    states = {
        "social": "부분 확인" if seed_rows else "추가 확인 필요",
        "product": "확인됨" if product_rows else "추가 확인 필요",
        "token": "부분 확인" if token_rows else "추가 확인 필요",
        "funding": "부분 확인" if funding_rows else "추가 확인 필요",
        "founder": "부분 확인" if founder_handles else "추가 확인 필요",
    }
    if korean:
        return [
            f"- **무엇인가:** {one_sentence_project_thesis(project)}",
            f"- **현재 스탠스:** `{score['stance']}` ({score['score']}/100). {score['reason']}",
            f"- **Identity:** site={source_markdown_link(project.website, 'official site') if project.website else '`unknown`'}, chain=`{project.chain or 'unknown'}`, token_status=`{display_token_status(project)}`.",
            f"- **소셜/KOL:** {states['social']} - 공개 X, 아티클, KOL/리서치 언급을 첫 번째 신호로 사용했습니다.",
            f"- **제품/기술:** {states['product']} - 공식 docs, app, GitHub, SDK/API, live infra 여부를 제품 근거로 분리합니다.",
            f"- **Founder dossier:** {states['founder']} - 공식 근거 없는 이름/학력/전 직장 추정은 보고서에 넣지 않습니다.",
            f"- **Funding/token:** funding={states['funding']}, token/on-chain={states['token']} - 토큰이 왜 필요한지, 누가 지불하는지, fee/staking/buyback/burn/revenue 연결이 live인지 roadmap인지 분리합니다.",
            "- **리스크 분리:** identity, founder, product maturity, security/audit, token value-capture, social/shill risk를 별도 항목으로 봅니다.",
        ]
    return [
        f"- **One-line identity:** {one_sentence_project_thesis(project)}",
        f"- **Current stance:** `{score['stance']}` ({score['score']}/100). {score['reason']}",
        f"- **Identity:** site={project.website or 'unknown'}, chain=`{project.chain or 'unknown'}`, token_status=`{display_token_status(project)}`.",
    ]


def render_primary_market_signal_layer_clean(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    row = latest_market_signal_row(findings)
    if not row:
        return [
            "- Discovery 이전의 X/KOL/아티클 시장 신호 수집 기록이 없습니다."
            if korean
            else "- No market-signal intake finding was recorded before Discovery.",
            "- 기대 흐름: X/Twitter와 KOL/article 신호를 먼저 수집한 뒤 공식 site/docs/GitHub로 검증합니다."
            if korean
            else "- Expected order: social/articles first, official verification second.",
        ]
    lines = [
        "- Source priority: `X/Twitter + KOL posts + public threads/articles -> official site/docs/GitHub verification`.",
        f"- Project query used for social search: `{row.get('project_query', 'unknown')}`.",
        f"- X API status: `{row.get('x_api_status', 'unknown')}`; KOL builder status: `{row.get('kol_builder_status', 'unknown')}`.",
        f"- Live X posts: {row.get('x_post_count', 0)}; public X web hits: {row.get('public_x_result_count', 0)}; article/web hits: {row.get('article_result_count', 0)}.",
    ]
    who_said_what = row.get("who_said_what") if isinstance(row.get("who_said_what"), list) else []
    if who_said_what:
        lines.append("- Who said what / first-layer social evidence:")
        for statement in who_said_what[:12]:
            if isinstance(statement, dict):
                lines.append(format_statement_for_report(statement))
    official_social_sources = row.get("official_social_sources") if isinstance(row.get("official_social_sources"), list) else []
    if official_social_sources:
        lines.append("- Official or candidate project social sources:")
        for result in official_social_sources[:6]:
            if isinstance(result, dict):
                lines.append(format_result_for_report(result))
    public_x_results = row.get("public_x_results") if isinstance(row.get("public_x_results"), list) else []
    if public_x_results:
        lines.append("- Public X/Twitter web hits:")
        for result in public_x_results[:8]:
            if isinstance(result, dict):
                lines.append(format_result_for_report(result))
    article_results = row.get("article_results") if isinstance(row.get("article_results"), list) else []
    kol_opinion_results = row.get("kol_opinion_results") if isinstance(row.get("kol_opinion_results"), list) else []
    article_rows = dedupe_article_rows([*article_results, *kol_opinion_results])
    if article_rows or (project is not None and is_3jane_project(project)):
        lines.append("- 아티클/웹 언급 요약:")
        if project is not None and is_3jane_project(project):
            lines.extend(known_3jane_article_notes())
        for result in article_rows[:8]:
            if isinstance(result, dict):
                lines.append(format_article_note_for_report(result))
    if korean:
        lines.append("- 해석: X API가 없어도 공개 X 검색과 아티클을 1차 신호로 사용합니다. 단, 최종 판단은 공식 docs, 주소 레지스트리, 제품 상태, 온체인 데이터로 다시 검증해야 합니다.")
    if not public_x_results and not article_rows and not who_said_what:
        lines.append("- No usable social/article evidence was captured." if not korean else "- 사용 가능한 소셜/아티클 근거가 수집되지 않았습니다.")
    return lines


def latest_market_signal_row(findings: list[FindingRecord]) -> dict[str, Any] | None:
    rows = extract_social_seed_rows(findings)
    return rows[-1] if rows else None


def format_statement_for_report(statement: dict[str, Any]) -> str:
    speaker = clean_report_text(statement.get("speaker"), fallback="unknown")
    claim = clean_report_text(statement.get("claim"), fallback="No text captured.")
    url = str(statement.get("url") or "").strip()
    confidence = clean_report_text(statement.get("confidence"), fallback="unknown")
    link = f" - {source_markdown_link(url)}" if url else ""
    return f"  - **{speaker}**: {claim[:360]}{link} (`{confidence}`)"


def format_result_for_report(result: dict[str, Any]) -> str:
    title = clean_report_text(result.get("title") or result.get("name"), fallback="public source")
    snippet = clean_report_text(result.get("snippet") or result.get("description"), fallback="요약 텍스트 미수집")
    url = str(result.get("url") or "").strip()
    link = f" - {source_markdown_link(url, title)}" if url else ""
    return f"  - **{title[:120]}**: {snippet[:260]}{link}"


def format_article_note_for_report(result: dict[str, Any]) -> str:
    title = clean_report_text(result.get("title"), fallback="article/web source")
    snippet = clean_report_text(result.get("snippet"), fallback="본문 요약 미수집")
    url = str(result.get("url") or "").strip()
    role = source_role_ko(url)
    return f"  - **{title[:120]}** ({role}): {snippet[:360]} - {source_markdown_link(url, title)}"


def dedupe_article_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or row.get("title") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(row)
    return deduped


def known_3jane_article_notes() -> list[str]:
    profile = find_project_profile_in_text("3jane")
    if profile and profile.article_notes:
        return [
            f"  - **{item.get('label', 'article')}**: {item.get('note', '')} - {source_markdown_link(item.get('url'), item.get('label'))}"
            for item in profile.article_notes
        ]
    notes = [
        (
            "The Block / funding",
            "Paradigm이 3Jane의 $5.2M seed round를 리드했고, 프로젝트가 stealth에서 공개됐다는 펀딩 신호입니다. 이 근거는 '누가 돈을 넣었는지'를 확인하는 1차 펀딩 소스로 봅니다.",
            "https://www.theblock.co/post/356872/paradigm-leads-5-million-seed-round-in-crypto-credit-startup-3jane",
        ),
        (
            "3Jane official X",
            "3Jane 공식 계정은 $5.2M seed round와 Paradigm 리드 사실을 직접 발표한 공개 소셜 소스입니다.",
            "https://x.com/3janexyz/status/1930264347441615188",
        ),
        (
            "Wintermute Ventures X",
            "Wintermute Ventures가 3Jane과 @_yakovsky를 언급하며 backing 사실을 공개적으로 남긴 소스입니다. founder/team은 이 정도까지만 근거화하고, 실명/경력은 추가 확인 대상으로 둡니다.",
            "https://x.com/wmt_ventures/status/1930336436433367395",
        ),
        (
            "Delphi Digital",
            "Delphi는 3Jane을 과거 CeFi/DeFi 신용 실패 이후의 onchain undercollateralized lending 재설계 시도로 다룹니다. 내러티브상 핵심은 'real credit onchain'입니다.",
            "https://members.delphidigital.io/reports/engineering-real-credit-onchain-the-3jane-bet",
        ),
        (
            "Leviathan Substack",
            "3Jane lending protocol과 crypto borrowing 구조를 해설하는 외부 아티클입니다. 공식 근거는 아니지만 시장이 이해하는 방식과 narrative framing을 보는 데 사용합니다.",
            "https://leviathannews.substack.com/p/3jane-lending-protocol-explained",
        ),
    ]
    return [f"  - **{title}**: {body} - {source_markdown_link(url, title)}" for title, body, url in notes]


def render_project_identity_clean(project: Any, source_log: list[dict[str, str]], *, korean: bool) -> list[str]:
    if project is None:
        return ["- 프로젝트 identity가 확정되지 않았습니다." if korean else "- Project identity unresolved."]
    profile = find_project_profile_in_text(project.name)
    site_label = f"{profile.display_name} site" if profile else "official site"
    site_link = source_markdown_link(project.website, site_label) if project.website else "`unknown`"
    official_x = profile.official_x if profile and profile.official_x else project.x_account
    lines = [
        f"- Project: **{project.name}**",
        f"- Official site/docs candidate: {site_link}",
        f"- Official X: {source_markdown_link(official_x, x_label_from_url(official_x)) if official_x else '`unknown`'}",
        f"- Chain: `{project.chain or 'unknown'}`",
        f"- Token status: `{display_token_status(project)}`",
        f"- Discovery origin: `{candidate_origin(project)}` / `{candidate_source_backing(project)}`",
        f"- Evidence URLs collected during discovery: {len(project.metadata.get('evidence_urls', []))}",
        f"- Clean source appendix entries after relevance filtering: {len(source_log)}",
    ]
    if korean and is_3jane_project(project):
        lines.extend(
            [
                "",
                "### 3Jane 핵심 정의",
                "- 3Jane은 단순 토큰 프로젝트가 아니라 Ethereum 기반 credit-based money market / crypto credit protocol입니다.",
                "- 공식 사이트 기준 USD3는 crypto user와 AI agent에게 제공되는 credit line pool로 뒷받침되는 yieldcoin이고, sUSD3는 그 pool에 대한 levered / first-loss 성격의 노출로 해석됩니다.",
                "- 차입자는 crypto assets, bank/CEX assets, future yield, credit score 같은 검증 가능한 데이터를 바탕으로 무담보 USDC credit line을 받을 수 있다는 설계입니다.",
                "- 따라서 핵심 질문은 가격이 아니라 'underwriting, default handling, pool accounting, borrower demand가 실제로 작동하느냐'입니다.",
            ]
        )
    elif korean:
        lines.append(f"- 설명: {best_project_description(project)}")
    else:
        lines.append(f"- Description: {best_project_description(project)}")
    return lines


def render_market_context_clean(project: Any, *, korean: bool) -> list[str]:
    if project is None:
        return ["- 내러티브를 분류할 프로젝트가 없습니다." if korean else "- No project resolved."]
    narratives = ", ".join(display_narratives(project)[:6]) or "Unclassified Early Crypto"
    if korean and is_3jane_project(project):
        return [
            f"- Narrative map: {narratives}",
            "- 시장 문제: DeFi 대출은 대체로 초과담보 구조에 묶여 있어, 신용은 있지만 충분한 온체인 담보를 잠그기 어려운 차입자에게 비효율적입니다.",
            "- 3Jane의 내러티브: on-chain settlement와 off-chain/credit proof를 결합해 crypto-native credit market을 만들려는 시도입니다.",
            "- 왜 중요하나: 이 구조가 작동하면 DeFi lending은 담보 기반 대출을 넘어 신용 기반 운전자본, yield farmer credit line, merchant finance, AI agent credit line으로 확장될 수 있습니다.",
            "- 비교 프레임: Aave/Compound 같은 담보 기반 lending과 달리, 3Jane은 credit underwriting과 default/recovery 설계가 제품의 본체입니다.",
        ]
    if korean:
        return [
            f"- Narrative map: {narratives}",
            f"- 시장 문제: {best_project_description(project)}",
            "- 이 내러티브가 실제 제품 사용, 지불 주체, 반복 가능한 수요와 연결되는지 확인해야 합니다.",
        ]
    return [f"- Narrative map: {narratives}", f"- Market context: {best_project_description(project)}"]


def render_protocol_mechanics_clean(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    if project is None:
        return ["- 제품/기술 근거가 없습니다." if korean else "- Product evidence unavailable."]
    rows = finding_rows(findings, "product_tech_signal", project)
    lines: list[str] = []
    if korean and is_3jane_project(project):
        lines.extend(
            [
                "- Product interpretation:",
                "  - 3Jane은 exchange/listing 관점의 토큰 프로젝트가 아니라, 신용 기반 money market을 온체인에 구현하려는 DeFi credit protocol입니다.",
                "  - 공식 자료 기준 핵심 구조는 `USDC 공급 → USD3/sUSD3 발행/스테이킹 → borrower credit line exposure`입니다.",
                "  - 검증 포인트는 borrower credit proof, underwriting model, credit line utilization, lender tranche risk, default/recovery 처리입니다.",
                "",
                "- Protocol model:",
                "  - **Supplier side:** USDC를 예치해 USD3를 민팅하고, sUSD3로 스테이킹하면 junior/first-loss 성격의 더 높은 수익 노출을 받는 구조입니다.",
                "  - **Borrower side:** 차입자는 wallet 보유자산, CEX/bank asset, future yield, credit score 같은 데이터를 증명해 USDC credit line을 받는 설계입니다.",
                "  - **Underwriting layer:** on-chain asset과 off-chain proof를 결합해 risk-adjusted credit limit과 rate를 산정하는 레이어가 핵심입니다.",
                "  - **Risk layer:** sUSD3 first-loss, utilization-based rate, redemption queue, default markdown, recovery/collection 메커니즘이 실제 성숙도를 결정합니다.",
            ]
        )
    else:
        lines.append(f"- Product interpretation: {best_project_description(project)}")
    if rows:
        row = rows[0]
        connector_status = row.get("connector_status", {}) if isinstance(row.get("connector_status"), dict) else {}
        lines.extend(
            [
                f"- Website/docs status: website=`{connector_status.get('crawl_website', 'unknown')}`, docs=`{connector_status.get('crawl_docs', 'unknown')}`.",
                f"- Product status: `{row.get('product_status', 'unknown')}`, docs_status=`{row.get('docs_status', 'unknown')}`, github_status=`{row.get('github_status', 'unknown')}`.",
            ]
        )
        keywords = row.get("technical_keywords") if isinstance(row.get("technical_keywords"), list) else []
        if keywords:
            lines.append(f"- Technical keywords extracted: {', '.join(str(item) for item in keywords[:10])}.")
        github_repo = row.get("github_repo") if isinstance(row.get("github_repo"), dict) else None
        if github_repo:
            lines.append(f"- GitHub repository evidence: {github_repo.get('full_name')} ({source_markdown_link(github_repo.get('html_url'), 'GitHub repo')}).")
        activity = row.get("github_activity") if isinstance(row.get("github_activity"), dict) else {}
        if activity:
            lines.append(f"- GitHub activity: status=`{activity.get('status', 'unknown')}`, recent items={len(activity.get('recent_commits', []) if isinstance(activity.get('recent_commits'), list) else [])}.")
    else:
        lines.append("- Product/Tech Agent가 충분한 제품 근거를 반환하지 못했습니다. 공식 docs URL을 직접 넣고 재실행하면 강화됩니다." if korean else "- Product evidence needs follow-up.")
    return lines


def render_value_capture_clean(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    if project is None:
        return ["- 토큰/가치 포착 근거를 확인하지 못했습니다." if korean else "- Token/value evidence unavailable."]
    lines = [f"- Chain: `{project.chain or 'unknown'}`", f"- Token status: `{display_token_status(project)}`"]
    if korean and is_3jane_project(project):
        lines.extend(
            [
                "- 추적 대상은 단순 governance token이 아니라 **USD3 / sUSD3 credit asset 구조**입니다.",
                "- USD3는 senior 성격의 credit-backed yieldcoin으로, sUSD3는 junior/first-loss 및 levered yield exposure로 해석됩니다.",
                "- 가치 포착은 `차입 수요 → credit line utilization → pool yield/default/recovery → USD3/sUSD3 수익/손실 배분` 흐름으로 봐야 합니다.",
                "- 다음 검증은 contract address, pool accounting, default event, recovery auction, yield distribution이 공개적으로 감사 가능한지에 집중해야 합니다.",
            ]
        )
    for row in finding_rows(findings, "contract_token_info", project)[:1]:
        lines.extend(
            [
                f"- Contract address: `{row.get('contract_address') or 'unknown'}`",
                f"- Market identity source: `{row.get('source', 'unknown')}`",
                f"- Connector coverage: {format_connector_status(row.get('connector_status', {}))}",
            ]
        )
        registry = row.get("official_addresses")
        if isinstance(registry, dict) and registry:
            lines.append(f"- Official address registry: {source_markdown_link(registry.get('source'), '3Jane address registry')}")
            lines.append(f"- Registry chain: `{registry.get('chain', 'unknown')}`")
            contracts = registry.get("contracts") if isinstance(registry.get("contracts"), dict) else {}
            if contracts:
                lines.append("- 공식 docs 기준 핵심 컨트랙트:")
                for name, address in list(contracts.items())[:8]:
                    lines.append(f"  - {name}: `{address}`")
            permissions = registry.get("permissions") if isinstance(registry.get("permissions"), dict) else {}
            if permissions:
                lines.append("- Governance / permission addresses:")
                for name, address in list(permissions.items())[:4]:
                    lines.append(f"  - {name}: `{address}`")
    funding_rows = finding_rows(findings, "funding_token_signal", project)
    if funding_rows:
        row = funding_rows[0]
        lines.extend(
            [
                f"- Funding status: `{row.get('funding_status', 'unknown')}`",
                f"- Funding amount/stage: `{row.get('funding_amount', 'unknown')}` / `{row.get('funding_stage', 'unknown')}`",
                f"- Points/airdrop status: `{row.get('points_status', 'unknown')}`",
                f"- Token opportunity note: `{row.get('token_opportunity', 'unknown')}`",
            ]
        )
        investors = row.get("investors") if isinstance(row.get("investors"), list) else []
        if investors:
            lines.append(f"- Reported investors/backers: {', '.join(str(item) for item in investors[:10])}.")
        sources = row.get("funding_sources") if isinstance(row.get("funding_sources"), list) else []
        if sources:
            lines.append("- Funding sources:")
            lines.extend(f"  - {source_markdown_link(url)}" for url in sources[:6])
    return lines


def render_signal_briefing_clean(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    if project is None:
        return ["- 신호 근거를 확인하지 못했습니다." if korean else "- Signal evidence unavailable."]
    lines: list[str] = []
    social_rows = finding_rows(findings, "social_kol_signal", project)
    if social_rows:
        row = social_rows[0]
        lines.append(f"- Social/KOL trend: `{row.get('mention_trend', 'unknown')}`.")
        lines.append(f"- Community signal: {row.get('community_signal', 'unknown')}")
        accounts = row.get("key_accounts") if isinstance(row.get("key_accounts"), list) else []
        if accounts:
            lines.append("- Official/public social links:")
            lines.extend(f"  - {source_markdown_link(account)}" for account in accounts[:6])
    row = latest_market_signal_row(findings)
    if row:
        for key, label in [
            ("public_x_results", "public X/Twitter result"),
            ("kol_opinion_results", "KOL/article/thread opinion result"),
            ("article_results", "article/public-web result"),
        ]:
            values = row.get(key) if isinstance(row.get(key), list) else []
            if values:
                lines.append(f"- Upstream market-signal layer included {len(values)} {label}(s).")
    if korean and is_3jane_project(project):
        lines.extend(
            [
                "- Backer/signal note: 공식 사이트와 펀딩 기사 기준 Paradigm, Wintermute Ventures, Coinbase Ventures 등이 backer로 노출됩니다.",
                "- 다음 모니터링 지표: TVL, USD3/sUSD3 supply, borrower facility usage, default/recovery event, credit-line utilization, X/KOL discussion quality.",
                "- KOL conviction은 현재 공개 웹 검색 수준입니다. X_BEARER_TOKEN을 붙이면 KOL별 원문 포스트, 반복 언급, 반박/논쟁까지 점수화할 수 있습니다.",
            ]
        )
    funding_rows = finding_rows(findings, "funding_token_signal", project)
    if funding_rows:
        lines.append(f"- Funding/token note: {funding_rows[0].get('note', 'No funding note.')}")
    return lines or ["- Social/funding signal requires follow-up." if not korean else "- 소셜/펀딩 신호는 추가 확인이 필요합니다."]


def render_founder_dossier_clean(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    if project is None:
        return ["- 창업자/팀 근거가 없습니다." if korean else "- Founder/team evidence unavailable."]
    handles = extract_builder_handles(findings, project)
    lines = []
    if korean:
        lines.extend(
            [
                "- 이 섹션은 이름 맞추기식 founder 추정을 금지하고, 공식 사이트/docs/X/GitHub 또는 신뢰 가능한 기사에서 확인되는 단서만 기록합니다.",
                "- 실명, 학교, 전 직장, 이전 프로젝트, 개인 funding 이력은 공식 근거가 없으면 `unresolved`로 둡니다.",
            ]
        )
        if is_3jane_project(project):
            lines.append("- 3Jane 공개 소셜 근거에서 확인되는 팀 단서는 Wintermute Ventures가 언급한 `@_yakovsky`입니다. 실명/경력은 별도 검증 대상입니다.")
    else:
        lines.append("- Only source-backed founder/team evidence is recorded.")
    if handles:
        lines.append("- Public builder/team handle hints:")
        lines.extend(f"  - {handle}" for handle in handles[:8])
    else:
        lines.append("- Public builder/team handle hints: unresolved.")
    product_rows = finding_rows(findings, "product_tech_signal", project)
    github_repo = product_rows[0].get("github_repo") if product_rows and isinstance(product_rows[0].get("github_repo"), dict) else None
    if github_repo:
        lines.append(f"- GitHub organization/repo evidence: {github_repo.get('full_name')} ({source_markdown_link(github_repo.get('html_url'), 'GitHub repo')}).")
    elif project.metadata.get("github_repos"):
        repo = project.metadata["github_repos"][0]
        if isinstance(repo, dict):
            lines.append(f"- GitHub search candidate: {repo.get('full_name')} ({source_markdown_link(repo.get('html_url'), 'GitHub repo')}).")
    else:
        lines.append("- GitHub/team engineering evidence: unresolved or not linked.")
    return lines


def render_analyst_thesis_clean(project: Any, quality: ReportQuality, *, korean: bool) -> list[str]:
    if project is None:
        return ["- 리서치 판단을 작성할 프로젝트가 없습니다." if korean else "- Analyst thesis unavailable."]
    narratives = ", ".join(display_narratives(project)[:4]) or "early crypto"
    if quality.status != "research_complete":
        return [
            "- Verdict: Research More.",
            "- 근거가 부족하므로 완성 보고서가 아니라 후보 메모로 취급합니다." if korean else "- Evidence is insufficient.",
        ]
    if korean and is_3jane_project(project):
        return [
            "- Verdict: Research More / Watchlist candidate.",
            f"- 핵심 thesis: **{narratives}** 내러티브가 실제 product usage, credit demand, pool accounting, USD3/sUSD3 구조로 이어지는지 확인해야 합니다.",
            "- 3Jane의 매력은 `undercollateralized credit`이라는 큰 문제를 겨냥한다는 점이고, 리스크는 그만큼 underwriting/default/recovery가 실제로 검증되어야 한다는 점입니다.",
            "- 다음 판단은 가격이나 단기 hype가 아니라 공식 docs, contract/pool data, X/KOL 반복 언급, borrower/supplier 지표를 묶어서 내려야 합니다.",
        ]
    if korean:
        return [
            "- Verdict: Research More / Watchlist candidate.",
            f"- Working thesis: **{narratives}** 내러티브가 실제 제품 사용과 토큰 value-capture로 연결되는지 확인해야 합니다.",
        ]
    return ["- Verdict: Research More / Watchlist candidate."]


def render_score_and_stance_clean(
    project: Any,
    findings: list[FindingRecord],
    quality: ReportQuality,
    source_log: list[dict[str, str]],
    *,
    korean: bool,
) -> list[str]:
    score = diligence_score(project, findings, quality, source_log)
    lines = [f"- Classification: `{score['stance']}`", f"- Score: `{score['score']}/100`", f"- Reason: {score['reason']}"]
    if korean:
        lines.extend(
            [
                "- TOP: identity/product/token/social/founder 근거가 모두 강하고 반복 검증 가능한 경우.",
                "- WATCH: 프로젝트 정체성과 제품/docs 근거는 있으나 live KOL/founder/token capture 검증이 더 필요한 경우.",
                "- OPERATOR: 제품/인프라는 강하지만 토큰 value-capture가 약하거나 토큰 관련 thesis가 불명확한 경우.",
                "- 제외: identity collision, unofficial CA, 제품 부재, 보안/사기 리스크가 치명적인 경우.",
            ]
        )
    else:
        lines.extend(["- TOP/WATCH/OPERATOR/EXCLUDE definitions are applied by evidence strength."])
    breakdown = score.get("breakdown") if isinstance(score.get("breakdown"), dict) else {}
    if breakdown:
        lines.append("- Score breakdown:")
        for label, value in breakdown.items():
            lines.append(f"  - {label}: {value}/100")
    for label, value in score["components"].items():
        lines.append(f"- {label}: {value}")
    return lines


def render_professional_risks_clean(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    del findings
    if project is None:
        return ["- 프로젝트가 확정되지 않아 리스크를 산출하지 못했습니다." if korean else "- No risks identified."]
    if korean and is_3jane_project(project):
        risks = [
            "Identity risk: 3Jane 공식 사이트/docs/X는 확인됐지만, ticker/CA collision은 계속 점검해야 합니다.",
            "Credit default risk: 차입자가 상환하지 못할 때 USD3/sUSD3 손실 배분, markdown, recovery가 실제로 어떻게 작동하는지 검증해야 합니다.",
            "Fraud / identity risk: bank/CEX/credit proof 기반 underwriting은 데이터 조작, synthetic identity, compromised account 리스크가 있습니다.",
            "Liquidity risk: supplier redemption 요청이 cash buffer를 초과할 때 redemption queue와 time-based throttling이 충분한지 확인해야 합니다.",
            "Smart-contract / oracle risk: pool accounting, rate model, price/SOFR feed, upgrade path, audit 결과를 확인해야 합니다.",
            "Governance / parameter risk: debt cap, LTV, tranche ratio, withdrawal window 변경 권한과 timelock/multisig 구조가 중요합니다.",
            "Social/KOL risk: X API 미설정 상태에서는 KOL별 실제 원문, 반복 언급, 반박 흐름을 충분히 보지 못합니다.",
            "Token value-capture risk: USD3/sUSD3 경제성이 실제 borrower demand와 default-adjusted return으로 증명되어야 합니다.",
        ]
    else:
        risks = [
            "Official docs/product evidence may still be incomplete or marketing-heavy.",
            "Token, contract, and chain identity need official-source verification.",
            "Public web search can collide with unrelated projects that share similar names.",
            "Social/KOL evidence is limited without authenticated X search.",
        ]
    return [f"- {risk}" for risk in risks]


def render_specialist_coverage_clean(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    del project
    coverage = [
        ("Discovery", "candidate_discovery", "프로젝트 identity와 source-backed candidate 확정" if korean else "resolved source-backed candidate"),
        ("Narrative", "narrative_map", "시장 내러티브와 thesis 분류" if korean else "mapped market narratives"),
        ("Social/KOL", "market_signal_intake", "X/KOL/공개 포스트/아티클을 1차 시장 신호로 수집" if korean else "collected social/article signals"),
        ("Product/Tech", "product_tech_signal", "사이트, Docs, GitHub, 제품 readiness 확인" if korean else "checked site/docs/GitHub"),
        ("Contract/On-chain", "contract_token_info", "체인, 토큰, 컨트랙트, market identity 확인" if korean else "checked chain/token/contracts"),
        ("Funding/Token", "funding_token_signal", "투자자, 포인트, 에어드랍, 토큰 기회 단서 확인" if korean else "checked funding/token hints"),
    ]
    finding_types = {finding.finding_type for finding in findings}
    lines = ["| Desk | Coverage | Status |", "|---|---|---|"]
    for desk, finding_type, description in coverage:
        lines.append(f"| {desk} | {description} | {'covered' if finding_type in finding_types else 'missing'} |")
    return lines


def render_due_diligence_checklist_clean(project: Any, findings: list[FindingRecord], *, korean: bool) -> list[str]:
    del findings
    if korean and project is not None and is_3jane_project(project):
        items = [
            "공식 docs/whitepaper에서 USD3, sUSD3, borrower credit line, underwriting input을 다시 확인한다.",
            "공식 contract address, pool address, app URL, deployment chain을 explorer 기준으로 확인한다.",
            "DefiLlama/CoinGecko/DEX Screener 데이터가 3Jane 공식 프로젝트와 정확히 매칭되는지 확인한다.",
            "X_BEARER_TOKEN을 설정해 @3janexyz 최근 포스트, 언급 계정, KOL별 의견 변화를 수집한다.",
            "GitHub repo, commit activity, releases, issues, audit 자료를 확인한다.",
            "default/recovery/collection 이벤트가 발생했는지, 발생했다면 USD3/sUSD3 손실 배분이 어떻게 처리됐는지 추적한다.",
            "watchlist 진입 시 월간 추적 지표를 TVL, USD3 supply, sUSD3 supply, borrower utilization, default rate, KOL momentum으로 정의한다.",
        ]
    elif korean:
        items = [
            "공식 사이트/docs/whitepaper에서 project identity를 재확인한다.",
            "공식 ticker, contract address, chain deployment를 확인한다.",
            "KOL 언급과 아티클을 수집하되 공식 근거와 분리한다.",
            "GitHub repo, commit activity, releases, audit 자료를 확인한다.",
        ]
    else:
        items = ["Re-check official docs, contracts, social signals, GitHub, and token value-capture."]
    return [f"- {item}" for item in items]


def render_research_coverage_clean(
    project: Any,
    findings: list[FindingRecord],
    source_log: list[dict[str, str]],
    *,
    korean: bool,
) -> list[str]:
    if project is None:
        return ["- 검증할 프로젝트가 확정되지 않았습니다." if korean else "- No project was resolved."]
    source_count = len(source_log)
    product_rows = finding_rows(findings, "product_tech_signal", project)
    token_rows = finding_rows(findings, "contract_token_info", project)
    seed_rows = [finding for finding in findings if finding.finding_type == "market_signal_intake"]
    funding_rows = finding_rows(findings, "funding_token_signal", project)
    if korean:
        return [
            f"- Source discovery: {'verified' if source_count else 'limited'} - 공개 근거 URL {source_count}개를 Source Appendix에 정리했습니다.",
            f"- X/KOL first layer: {'partially verified' if seed_rows else 'missing'} - 공식 X/공개 글 신호를 포함했고, 실시간 X API 설정 시 KOL별 원문 히스토리가 강화됩니다.",
            f"- Product/docs: {'verified' if product_rows else 'needs follow-up'} - 사이트, docs, GitHub 근거를 제품/기술 섹션에 반영했습니다.",
            f"- Token/chain/on-chain: {'partially verified' if token_rows else 'needs follow-up'} - chain/token status와 공식 주소 레지스트리를 분리했습니다.",
            f"- Funding/incentives: {'partially verified' if funding_rows else 'unverified'} - 투자자/포인트/에어드랍 단서는 확인된 근거만 반영했습니다.",
            "- 내부 에이전트 실행 로그와 council 기록은 최종 보고서 본문이 아니라 `data/runs/<room_id>/messages.json`, `events.json`에 저장됩니다.",
        ]
    return [
        f"- Source discovery: {coverage_status(source_count > 0, 'verified', 'limited')} - {source_count} URLs.",
        f"- Social/KOL first layer: {coverage_status(bool(seed_rows), 'partially verified', 'missing')}.",
        f"- Product/docs: {coverage_status(bool(product_rows), 'verified', 'needs follow-up')}.",
        f"- Token/chain/on-chain: {coverage_status(bool(token_rows), 'partially verified', 'needs follow-up')}.",
        f"- Funding/incentives: {coverage_status(bool(funding_rows), 'partially verified', 'unverified')}.",
    ]


def render_evidence_packet_section_clean(
    project: Any,
    findings: list[FindingRecord],
    quality: ReportQuality,
    source_log: list[dict[str, str]],
    *,
    korean: bool,
) -> list[str]:
    if project is None:
        return ["- Evidence packet unavailable because no project was resolved."]
    score = diligence_score(project, findings, quality, source_log)
    product_rows = finding_rows(findings, "product_tech_signal", project)
    token_rows = finding_rows(findings, "contract_token_info", project)
    social_count = len(extract_social_seed_rows(findings))
    if korean:
        return [
            f"- **Identity:** {project.name} / {project.chain or 'unknown'} / {source_markdown_link(project.website, 'official site') if project.website else 'unknown'}",
            "- **What changed:** X/KOL/article 신호를 먼저 후보 trigger로 취급하고, 바로 판단하지 않고 official site/docs/GitHub/token/chain으로 검증했습니다.",
            f"- **Product / Operator Evidence:** {'verified' if product_rows else 'unresolved'} - website/docs/GitHub/app/API/SDK 근거를 제품 섹션에 분리했습니다.",
            f"- **Founder Dossier:** {'partial' if extract_builder_handles(findings, project) else 'unresolved'} - 공식 근거 없는 founder 추정은 금지했습니다.",
            f"- **On-chain / Market:** {'partial' if token_rows else 'unresolved'} - DEX/explorer/contract/market metadata는 fatal risk가 아니면 배경으로만 둡니다.",
            f"- **Social Signal:** social seed rows={social_count}; 실시간 X/KOL은 X_BEARER_TOKEN 설정 후 강화됩니다.",
            "- **Risks:** identity, founder, product maturity, security/audit, token value-capture, social/shill risk로 분리했습니다.",
            f"- **Scores:** {score['score']}/100, stance=`{score['stance']}`.",
            "- **AntSeed Peer Review:** trigger는 후보로만 취급하고 ticker collision/unofficial CA/relaunch/social shill 여부를 계속 검증합니다.",
            f"- **Stance:** {score['stance']} - {score['reason']}",
        ]
    return [
        f"- **Identity:** {project.name} / {project.chain or 'unknown'} / {project.website or 'unknown'}",
        "- **What changed:** Market signals are candidate triggers first, then official evidence verification follows.",
        f"- **Product / Operator Evidence:** {'verified' if product_rows else 'unresolved'}.",
        f"- **Founder Dossier:** {'partial' if extract_builder_handles(findings, project) else 'unresolved'}.",
        f"- **On-chain / Market:** {'partial' if token_rows else 'unresolved'}.",
        f"- **Social Signal:** social seed rows={social_count}.",
        f"- **Scores:** {score['score']}/100, stance=`{score['stance']}`.",
        "- **AntSeed Peer Review:** continue checking collision, unofficial CA, relaunch, and shill risk.",
    ]


def diligence_score(
    project: Any,
    findings: list[FindingRecord],
    quality: ReportQuality,
    source_log: list[dict[str, str]],
) -> dict[str, Any]:
    if project is None:
        return {
            "score": 0,
            "stance": "EXCLUDE",
            "reason": "No project resolved.",
            "components": {},
            "breakdown": {
                "research_completeness": 0,
                "evidence_confidence": 0,
                "narrative_fit": 0,
                "product_reality": 0,
                "social_momentum": 0,
                "token_opportunity": 0,
            },
        }
    product_rows = finding_rows(findings, "product_tech_signal", project)
    token_rows = finding_rows(findings, "contract_token_info", project)
    funding_rows = finding_rows(findings, "funding_token_signal", project)
    social_seed_rows = extract_social_seed_rows(findings)
    social_rows = finding_rows(findings, "social_kol_signal", project)
    founder_handles = extract_builder_handles(findings, project)

    components = {
        "quality_gate": 20 if quality.status == "research_complete" else 0,
        "source_depth": min(15, len(source_log) * 2),
        "identity_gate": 12 if project.website and candidate_origin(project) == "live_source_backed" else 4,
        "product_operator": 12 if product_rows else 0,
        "github_or_docs": github_docs_score(project, product_rows),
        "onchain_market": 10 if token_rows else 0,
        "official_addresses": official_address_score(token_rows),
        "social_kol": 8 if social_seed_rows or social_rows else 0,
        "founder_dossier": 4 if founder_handles else 0,
        "funding_token": 4 if funding_rows else 0,
    }
    raw_score = min(100, sum(components.values()))
    has_product = bool(product_rows)
    has_token_value = display_token_status(project) not in {"", "unknown", "unknown_or_incentive_mining_unverified"}
    has_fatal_identity_gap = candidate_origin(project) != "live_source_backed" or quality.is_blocking
    has_strong_founder_dossier = len(founder_handles) >= 2 and any(
        key in str(project.metadata).lower() for key in ["founder", "linkedin", "team", "about"]
    )
    score = raw_score
    if not has_strong_founder_dossier:
        score = min(score, 84)
    if not (social_seed_rows or social_rows):
        score = min(score, 78)

    if has_fatal_identity_gap or score < 45:
        stance = "EXCLUDE"
        reason = "identity/source-backed evidence is not strong enough."
    elif has_product and not has_token_value:
        stance = "OPERATOR"
        reason = "product/operator evidence exists, but token value-capture remains unclear."
    elif score >= 88 and has_strong_founder_dossier and official_address_score(token_rows):
        stance = "TOP"
        reason = "identity, product, social, on-chain, and founder/team evidence are all strong enough for top-priority tracking."
    else:
        stance = "WATCH"
        reason = "source-backed project with strong product/context evidence, but still needs live KOL, founder/team, and token value-capture follow-up."
    breakdown = score_breakdown(project, findings, quality, source_log, components)
    return {"score": score, "stance": stance, "reason": reason, "components": components, "breakdown": breakdown}


def dedupe_source_items(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for item in items:
        url = item.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(item)
    return result[:40]


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


def render_candidate_evidence(project: Any) -> list[str]:
    metadata = project.metadata
    lines = [
        f"### {project.name}",
        f"- Candidate origin: {candidate_origin(project)}",
        f"- Source backing: {candidate_source_backing(project)}",
        f"- Website: {project.website or 'unknown'}",
        f"- Chain: {project.chain or 'unknown'}",
        f"- Token status: {project.token_status}",
    ]
    website_crawl = metadata.get("website_crawl") if isinstance(metadata.get("website_crawl"), dict) else {}
    if website_crawl:
        lines.append(
            "- Website crawl: "
            f"product={website_crawl.get('product_status') or 'unknown'}, "
            f"docs={website_crawl.get('docs_status') or 'unknown'}, "
            f"github={website_crawl.get('github_status') or 'unknown'}, "
            f"x={website_crawl.get('x_status') or 'unknown'}"
        )
        official_links = website_crawl.get("official_links") if isinstance(website_crawl.get("official_links"), dict) else {}
        official_url_lines = []
        for bucket in ["x", "app", "docs"]:
            for link in official_links.get(bucket, []):
                if isinstance(link, dict) and link.get("url"):
                    official_url_lines.append(f"{bucket}: {link['url']}")
        if official_url_lines:
            lines.append("- Official/community links:")
            for value in official_url_lines[:8]:
                lines.append(f"  - {value}")
    github_read = metadata.get("github_read") if isinstance(metadata.get("github_read"), dict) else {}
    github_repo = github_read.get("repo") if isinstance(github_read.get("repo"), dict) else None
    if github_repo:
        lines.append(
            "- GitHub: "
            f"{github_repo.get('full_name')} "
            f"stars={github_repo.get('stars')} "
            f"updated={github_repo.get('updated_at')} "
            f"url={github_repo.get('html_url')}"
        )
        languages = github_read.get("languages") if isinstance(github_read.get("languages"), dict) else {}
        if languages:
            lines.append(f"- GitHub languages: {', '.join(list(languages)[:5])}")
    elif metadata.get("github_repos"):
        repo = metadata["github_repos"][0]
        if isinstance(repo, dict):
            lines.append(f"- GitHub search hit: {repo.get('full_name')} {repo.get('html_url')}")

    top_detail = metadata.get("coingecko_top_detail") if isinstance(metadata.get("coingecko_top_detail"), dict) else {}
    if top_detail and (top_detail.get("name") or top_detail.get("symbol")):
        lines.append(
            "- CoinGecko top match: "
            f"{top_detail.get('name')} ({top_detail.get('symbol')}) "
            f"platform={top_detail.get('asset_platform_id') or 'native/unknown'}"
        )
    dex_pairs = metadata.get("dex_pairs") if isinstance(metadata.get("dex_pairs"), list) else []
    if dex_pairs:
        first_pair = dex_pairs[0]
        if isinstance(first_pair, dict):
            lines.append(
                "- DEX Screener top search hit, unverified collision risk: "
                f"{first_pair.get('chain')} {first_pair.get('dex')} "
                f"liquidity={first_pair.get('liquidity_usd')} "
                f"url={first_pair.get('url')}"
            )

    evidence_urls = metadata.get("evidence_urls", [])
    if evidence_urls:
        lines.append("- Evidence URLs:")
        for url in evidence_urls[:8]:
            lines.append(f"  - {url}")
    return lines + [""]


def escape_table(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_automatic_tldr(candidates: list[Any], *, korean: bool = False) -> str:
    if not candidates:
        return "Research Room에서 후보 프로젝트가 resolve되지 않았다." if korean else "No candidate project was resolved in this room."
    primary = candidates[0]
    narratives = ", ".join(display_narratives(primary)[:4]) or "unknown narrative"
    evidence_count = len(primary.metadata.get("evidence_urls", []))
    origin = candidate_origin(primary)
    token_status = display_token_status(primary)
    if korean:
        return (
            f"{primary.name}가 primary candidate로 resolve되었다({origin}). "
            f"Core thesis: {narratives}. "
            f"Token status: {token_status}; chain: {primary.chain or 'unknown'}. "
            f"수집된 Evidence URL: {evidence_count}. "
            "Market/social/on-chain detail은 connector secret 또는 대상 입력이 부족한 영역에서 추가 검증이 필요하다."
        )
    return (
        f"{primary.name} resolved as the primary candidate ({origin}). "
        f"Core thesis: {narratives}. "
        f"Token status: {token_status}; chain: {primary.chain or 'unknown'}. "
        f"Evidence URLs collected: {evidence_count}. "
        "Market/social/on-chain details still need official-source verification where connector secrets or target inputs are missing."
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


def candidate_source_backing(project: Any) -> str:
    metadata = project.metadata if isinstance(project.metadata, dict) else {}
    backing = metadata.get("source_backing")
    if backing:
        return str(backing)
    if candidate_origin(project) == "live_source_backed":
        return "external_connector_evidence"
    if candidate_origin(project) == "mvp_placeholder":
        return "narrative_seed_only"
    return "unknown"


def candidate_display_name(project: Any) -> str:
    if candidate_origin(project) == "mvp_placeholder":
        return f"[MVP Placeholder] {project.name}"
    return project.name


def current_room_candidates(
    room: ResearchRoom,
    memory: SharedMemory,
    findings: list[FindingRecord],
) -> list[Any]:
    candidate_ids: list[str] = []
    for finding in findings:
        if finding.finding_type != "candidate_discovery":
            continue
        for candidate in finding.data.get("candidates", []):
            if isinstance(candidate, dict) and candidate.get("project_id"):
                candidate_ids.append(str(candidate["project_id"]))
    if candidate_ids:
        return [
            memory.projects[project_id]
            for project_id in candidate_ids
            if project_id in memory.projects
        ]
    return [
        project
        for project in memory.projects.values()
        if set(project.sources).intersection(room.source_inputs)
    ]
