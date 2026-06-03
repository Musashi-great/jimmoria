from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from crypto_research_agents.agents.base import AgentResult, BaseAgent
from crypto_research_agents.core.bus import CollaborationBus
from crypto_research_agents.core.company_settings import CompanySettings
from crypto_research_agents.core.memory import FindingRecord, SharedMemory
from crypto_research_agents.core.room import ResearchRoom
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

        summary = quality.result_summary(report_path)
        confidence = 0.35 if quality.is_blocking else 0.7
        finding = self.write_finding(
            room=room,
            memory=memory,
            finding_type="report",
            summary=summary,
            data={
                "report_path": str(report_path),
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
            payload={"report_path": str(report_path), "finding_id": finding.finding_id},
        )
        return AgentResult(
            self.agent_id,
            summary,
            {
                "finding_id": finding.finding_id,
                "report_path": str(report_path),
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
    korean = wants_korean_report(room, settings)
    primary = candidates[0] if candidates else None
    title_name = primary.name if primary else room.topic
    narrative_text = ", ".join(display_narratives(primary)) if primary else "unknown"
    source_log = collect_source_log(primary, sources) if primary else []
    source_summary = ", ".join(f"[{item['label']}]({item['url']})" for item in source_log[:12]) or "source log unavailable"

    lines: list[str] = [
        f"# {title_name} 리서치 보고서 - 통합본" if korean else f"# {title_name} Research Report - Integrated Dossier",
        "",
        f"- 작성일: {room.created_at}" if korean else f"- Created at: {room.created_at}",
        f"- 목적: {room.topic}" if korean else f"- Purpose: {room.topic}",
        f"- 분석 대상: {title_name}" if korean else f"- Subject: {title_name}",
        f"- 주요 체인: {primary.chain or 'unknown'}" if primary and korean else f"- Primary chain: {(primary.chain if primary else None) or 'unknown'}",
        f"- 핵심 내러티브: {narrative_text}" if korean else f"- Core narratives: {narrative_text}",
        f"- 주요 출처: {source_summary}" if korean else f"- Main sources: {source_summary}",
        "",
        "---",
        "",
        "## 1. 핵심 결론" if korean else "## 1. Core Conclusion",
    ]
    lines.extend(render_core_conclusion(primary, quality, korean=korean))

    lines.extend(
        [
            "",
            f"## 2. {title_name}는 무엇을 하려는 프로젝트인가" if korean else f"## 2. What {title_name} Is Trying To Build",
        ]
    )
    lines.extend(render_project_overview(primary, korean=korean))

    lines.extend(["", "## 3. 제품 / 기술 구조" if korean else "## 3. Product / Technical Structure"])
    lines.extend(render_product_structure(primary, findings, korean=korean))

    lines.extend(["", "## 4. 토큰 / 체인 / 온체인 상태" if korean else "## 4. Token / Chain / On-chain State"])
    lines.extend(render_token_chain_section(primary, findings, korean=korean))

    lines.extend(["", "## 5. 소셜 / KOL / 커뮤니티 신호" if korean else "## 5. Social / KOL / Community Signals"])
    lines.extend(render_social_section(primary, findings, korean=korean))

    lines.extend(["", "## 6. 펀딩 / 인센티브 / 에어드랍 단서" if korean else "## 6. Funding / Incentive / Airdrop Hints"])
    lines.extend(render_funding_section(primary, findings, korean=korean))

    lines.extend(["", "## 7. 리서치 Thesis" if korean else "## 7. Research Thesis"])
    lines.extend(render_research_thesis(primary, korean=korean))

    lines.extend(["", "## 8. 강점" if korean else "## 8. Strengths"])
    lines.extend(render_strengths(primary, findings, korean=korean))

    lines.extend(["", "## 9. 약점 / 리스크" if korean else "## 9. Weaknesses / Risks"])
    lines.extend(render_risks(primary, findings, korean=korean))

    lines.extend(["", "## 10. 앞으로 확인해야 할 것" if korean else "## 10. Follow-up Checklist"])
    lines.extend(render_due_diligence_checklist(primary, findings, korean=korean))

    lines.extend(["", "## 11. 검증 상태 / 리서치 범위" if korean else "## 11. Verification Status / Research Coverage"])
    lines.extend(render_research_coverage(primary, findings, source_log, korean=korean))

    lines.extend(["", "## 12. Source Log"])
    if source_log:
        for item in source_log:
            lines.append(f"- [{item['label']}]({item['url']})")
    else:
        lines.append("- No source URL was collected.")

    lines.extend(
        [
            "",
            "## 13. Research Quality Gate",
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
    if website.get("meta_description"):
        return str(website["meta_description"])
    for result in metadata.get("web_results", []):
        if isinstance(result, dict) and result.get("snippet"):
            return str(result["snippet"])
    return "공개 근거로 식별된 초기 crypto project candidate이며, 세부 제품 정의는 공식 문서와 source log를 통해 추가 확인해야 한다."


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


def collect_source_log(project: Any, sources: list[Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if project is not None:
        for url in project.metadata.get("evidence_urls", []):
            items.append({"label": source_label(url), "url": str(url)})
        website = project.metadata.get("website_crawl") if isinstance(project.metadata.get("website_crawl"), dict) else {}
        official_links = website.get("official_links") if isinstance(website.get("official_links"), dict) else {}
        for bucket, links in official_links.items():
            if not isinstance(links, list):
                continue
            for link in links:
                if isinstance(link, dict) and link.get("url"):
                    items.append({"label": f"{bucket}: {source_label(link['url'])}", "url": str(link["url"])})
    for source in sources:
        if getattr(source, "url", None):
            items.append({"label": getattr(source, "title", "source"), "url": str(source.url)})
    return dedupe_source_items(items)


def source_label(url: object) -> str:
    value = str(url)
    cleaned = value.removeprefix("https://").removeprefix("http://").strip("/")
    return cleaned[:80] or value


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
