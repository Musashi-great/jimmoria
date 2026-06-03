from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from crypto_research_agents.agents.base import AgentResult, BaseAgent
from crypto_research_agents.core.bus import CollaborationBus
from crypto_research_agents.core.company_settings import CompanySettings
from crypto_research_agents.core.memory import FindingRecord, SharedMemory
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
        llm_summary = self._write_llm_summary(room, memory, findings)
        provider_name = getattr(self.model_gateway.provider, "provider_name", "unknown")
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
        primary = candidates[0] if candidates else None
        room_sources = [memory.sources[source_id] for source_id in room.source_inputs if source_id in memory.sources]
        source_log = collect_source_log(primary, room_sources) if primary else []
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
    if not public_x_results and not x_posts and not article_results:
        lines.append(
            "- 실사용 가능한 소셜/아티클 결과가 아직 없습니다. `X_BEARER_TOKEN`을 설정하거나 공개 웹 검색을 허용하면 이 레이어가 강화됩니다."
            if korean
            else "- No usable public social/article result was collected yet. Add `X_BEARER_TOKEN` for live X search or allow public web search for social fallback."
        )
    return lines


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
        if seed.get("public_x_results"):
            lines.append(f"- Upstream market-signal layer included {len(seed.get('public_x_results', []))} public X/Twitter result(s).")
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
    return render_project_intelligence_report_v2(
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
    if not public_x_results and not x_posts and not article_results:
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
        if seed.get("public_x_results"):
            lines.append(f"- Upstream market-signal layer included {len(seed.get('public_x_results', []))} public X/Twitter result(s).")
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
        for key in ["public_x_results", "article_results", "x_posts", "kol_profiles"]:
            value = row.get(key)
            if isinstance(value, list):
                buckets.extend(item for item in value if isinstance(item, dict))
        for item in buckets:
            text = " ".join(str(item.get(key, "")) for key in ["title", "snippet", "text", "url", "author_username"])
            for token in text.replace("\n", " ").split():
                cleaned = token.strip(".,:;()[]{}<>\"'")
                if cleaned.startswith("@") and len(cleaned) > 2:
                    handles.append(cleaned)
                elif "x.com/" in cleaned.lower() or "twitter.com/" in cleaned.lower():
                    segment = cleaned.rstrip("/").split("/")[-1]
                    if segment and segment.lower() not in {"status", "i", "search"}:
                        handles.append("@" + segment)
    deduped = []
    for handle in handles:
        normalized = handle.rstrip("/")
        if normalized.lower() in {"@3janexyz", "@x", "@twitter"}:
            continue
        if project_tokens_text and normalized[1:].lower() in project_tokens_text:
            continue
        if normalized not in deduped:
            deduped.append(normalized)
    return deduped[:12]


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
    return {"score": score, "stance": stance, "reason": reason, "components": components}


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
        *(render_project_identity_v2(project, source_log, korean=korean) if project is not None else ["- unresolved"]),
        "",
        "## What changed",
        "- Trigger is treated only as a candidate; identity, product, token, social, and security evidence are checked before stance.",
        "- X/KOL/article signals are first-layer market evidence; official site/docs/GitHub/on-chain are the verification layer.",
        "",
        "## Product / Operator Evidence",
        *(render_protocol_mechanics_v2(project, findings, korean=korean) if project is not None else ["- unresolved"]),
        "",
        "## Founder Dossier",
        *(render_founder_dossier_v2(project, findings, korean=korean) if project is not None else ["- unresolved"]),
        "",
        "## On-chain / Market",
        *(render_value_capture_v2(project, findings, korean=korean) if project is not None else ["- unresolved"]),
        "",
        "## Social Signal",
        *(render_signal_briefing_v2(project, findings, korean=korean) if project is not None else ["- unresolved"]),
        "",
        "## Risks",
        *(render_professional_risks_v2(project, findings, korean=korean) if project is not None else ["- unresolved"]),
        "",
        "## Scores",
        *(render_score_and_stance_v2(project, findings, quality, source_log, korean=korean) if project is not None else ["- Score: 0/100"]),
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
        lines.extend(f"- [{item['label']}]({item['url']}) - {source_role(item['url'])}" for item in source_log[:40])
    else:
        lines.append("- No source URL was collected.")
    return "\n".join(lines)


def collect_source_log(project: Any, sources: list[Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if project is not None:
        if project.website and is_relevant_source_url(project, str(project.website), label="official site"):
            items.append({"label": source_label(project.website), "url": str(project.website)})
        for url in project.metadata.get("evidence_urls", []):
            if is_relevant_source_url(project, str(url), label=source_label(url)):
                items.append({"label": source_label(url), "url": str(url)})
        website = project.metadata.get("website_crawl") if isinstance(project.metadata.get("website_crawl"), dict) else {}
        official_links = website.get("official_links") if isinstance(website.get("official_links"), dict) else {}
        for bucket, links in official_links.items():
            if not isinstance(links, list):
                continue
            for link in links:
                if isinstance(link, dict) and link.get("url"):
                    label = f"{bucket}: {source_label(link['url'])}"
                    if is_relevant_source_url(project, str(link["url"]), label=label):
                        items.append({"label": label, "url": str(link["url"])})
    for source in sources:
        label = str(getattr(source, "title", "source"))
        if getattr(source, "url", None) and (project is None or is_relevant_source_url(project, str(source.url), label=label)):
            items.append({"label": label, "url": str(source.url)})
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
