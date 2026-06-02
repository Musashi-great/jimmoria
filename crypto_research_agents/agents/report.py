from __future__ import annotations

from pathlib import Path
from typing import Any

from crypto_research_agents.agents.base import AgentResult, BaseAgent
from crypto_research_agents.core.bus import CollaborationBus
from crypto_research_agents.core.memory import FindingRecord, SharedMemory
from crypto_research_agents.core.model_gateway import ModelGateway
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
        llm_summary = self._write_llm_summary(room, memory, findings)
        provider_name = getattr(self.model_gateway.provider, "provider_name", "unknown")
        report = render_project_dossier(
            room,
            memory,
            findings,
            decision.selected_model,
            provider_name,
            llm_summary,
        )
        report_path = reports_dir / f"{safe_filename(room.topic)}-{room.room_id}.md"
        report_path.write_text(report, encoding="utf-8")
        room.report_draft = report
        room.output_paths["report"] = str(report_path)

        finding = self.write_finding(
            room=room,
            memory=memory,
            finding_type="report",
            summary=f"Report written to {report_path}",
            data={
                "report_path": str(report_path),
                "model": decision.selected_model,
                "provider": provider_name,
                "llm_summary": llm_summary,
            },
            confidence=0.7,
        )
        bus.handoff(
            room_id=room.room_id,
            from_agent=self.agent_id,
            to_agent="obsidian_curator_agent",
            summary="Final report is ready for Obsidian sync.",
            payload={"report_path": str(report_path), "finding_id": finding.finding_id},
        )
        return AgentResult(
            self.agent_id,
            f"Report written to {report_path}",
            {"finding_id": finding.finding_id, "report_path": str(report_path)},
            confidence=0.7,
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


def render_project_dossier(
    room: ResearchRoom,
    memory: SharedMemory,
    findings: list[FindingRecord],
    model_name: str,
    provider_name: str,
    llm_summary: str,
) -> str:
    candidates = current_room_candidates(room, memory, findings)
    sources = [memory.sources[source_id] for source_id in room.source_inputs if source_id in memory.sources]
    has_live_evidence = any(project.metadata.get("discovery_mode") == "live_search" for project in candidates)
    has_placeholder_candidates = any(candidate_origin(project) == "mvp_placeholder" for project in candidates)

    lines: list[str] = [
        f"# Project Research Dossier: {room.topic}",
        "",
        "## 1. TL;DR",
        f"- Room ID: `{room.room_id}`",
        "- Current judgment: Research More",
        f"- Automated synthesis: {render_automatic_tldr(candidates)}",
    ]
    if provider_name == "offline_fallback":
        lines.append("- Live LLM: not configured. Offline fallback generated deterministic summaries only.")
    elif should_show_llm_summary(llm_summary):
        lines.append(f"- LLM synthesis: {llm_summary}")
    lines.extend(
        [
            "- Candidate origin rule: MVP placeholders are not verified live candidates. Treat only `live_source_backed` rows as source-backed leads."
            if has_placeholder_candidates
            else "- Candidate origin rule: Current candidate rows are marked with their source-backing level.",
            "- Note: Live web/GitHub/market connectors were used where available; social APIs, RootData, and explorer/RPC may still be placeholders."
            if has_live_evidence
            else "- Note: This MVP uses local placeholders for live social/on-chain/product checks until connectors are configured.",
            "",
            "## 2. Goals",
        ]
    )
    lines.extend(f"- {goal}" for goal in room.goals)

    lines.extend(["", "## 3. Sources"])
    if sources:
        for source in sources:
            url = f" ({source.url})" if source.url else ""
            lines.append(f"- `{source.source_id}` - {source.title}{url}")
    else:
        lines.append("- No sources attached.")

    lines.extend(["", "## 4. Candidate Projects"])
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

    lines.extend(["", "## 5. Evidence Map"])
    if candidates:
        for project in candidates:
            lines.extend(render_candidate_evidence(project))
    else:
        lines.append("- No candidate evidence available.")

    lines.extend(["", "## 6. Agent Findings"])
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
            "## 7. Open Questions",
            "- If a candidate is marked `mvp_placeholder`, replace it with a `live_source_backed` candidate before treating it as a real project lead.",
            "- Configure live X/Twitter, Telegram, RootData, Explorer/RPC, and funding connectors.",
            "- Validate official social handles and KOL mention history.",
            "- Verify token mechanics against explorer/RPC or official chain data.",
            "- Add source-backed KOL mention history and social momentum scores.",
            "",
            "## 8. Runtime Metadata",
            f"- LLM provider: `{provider_name}`",
            f"- Report model route: `{model_name}`",
        ]
    )
    return "\n".join(lines)


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


def render_automatic_tldr(candidates: list[Any]) -> str:
    if not candidates:
        return "No candidate project was resolved in this room."
    primary = candidates[0]
    narratives = ", ".join(primary.narratives[:4]) or "unknown narrative"
    evidence_count = len(primary.metadata.get("evidence_urls", []))
    origin = candidate_origin(primary)
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
