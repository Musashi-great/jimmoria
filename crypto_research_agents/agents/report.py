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
                "Write a concise research TL;DR. Do not invent live data. "
                "Clearly mention when connector data is not configured."
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
            "- 참고: 가능한 곳에서는 live web/GitHub/market connector를 사용했다. Social API, RootData, explorer/RPC는 아직 placeholder일 수 있다."
            if korean
            else "- Note: Live web/GitHub/market connectors were used where available; social APIs, RootData, and explorer/RPC may still be placeholders."
        )
    else:
        connector_note = (
            "- 참고: connector 설정 전까지 live social/on-chain/product check는 local placeholder 또는 미설정 결과를 사용할 수 있다."
            if korean
            else "- Note: This MVP uses local placeholders for live social/on-chain/product checks until connectors are configured."
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
    elif should_show_llm_summary(llm_summary):
        lines.append(f"- {'LLM 종합' if korean else 'LLM synthesis'}: {llm_summary}")
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
            "- Live X/Twitter, Telegram, RootData, Explorer/RPC, funding connector 설정이 필요하다."
            if korean
            else "- Configure live X/Twitter, Telegram, RootData, Explorer/RPC, and funding connectors.",
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
        for bucket in ["x", "discord", "telegram", "app", "docs"]:
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
    narratives = ", ".join(primary.narratives[:4]) or "unknown narrative"
    evidence_count = len(primary.metadata.get("evidence_urls", []))
    origin = candidate_origin(primary)
    if korean:
        return (
            f"{primary.name}가 primary candidate로 resolve되었다({origin}). "
            f"Core thesis: {narratives}. "
            f"Token status: {primary.token_status}; chain: {primary.chain or 'unknown'}. "
            f"수집된 Evidence URL: {evidence_count}. "
            "Market/social/on-chain detail은 placeholder connector 영역에서 추가 검증이 필요하다."
        )
    return (
        f"{primary.name} resolved as the primary candidate ({origin}). "
        f"Core thesis: {narratives}. "
        f"Token status: {primary.token_status}; chain: {primary.chain or 'unknown'}. "
        f"Evidence URLs collected: {evidence_count}. "
        "Market/social/on-chain details still need official-source verification where connectors are placeholders."
    )


def should_show_llm_summary(summary: str) -> bool:
    cleaned = summary.strip()
    if not cleaned:
        return False
    if cleaned.startswith("Topic:") and "Goals:" in cleaned:
        return False
    return True


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
