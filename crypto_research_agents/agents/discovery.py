from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlparse

from crypto_research_agents.agents.base import AgentResult, BaseAgent
from crypto_research_agents.core.bus import CollaborationBus
from crypto_research_agents.core.memory import ProjectCandidate, SharedMemory
from crypto_research_agents.core.message import MessageType
from crypto_research_agents.core.room import ResearchRoom
from crypto_research_agents.core.source_quality import (
    is_generic_platform_url,
    is_low_signal_url as is_low_signal_research_url,
    score_official_url,
    select_best_official_site,
)


class DiscoveryAgent(BaseAgent):
    agent_id = "discovery_agent"
    name = "Discovery Agent"
    task_type = "candidate_discovery"

    def run(self, room: ResearchRoom, memory: SharedMemory, bus: CollaborationBus, **kwargs: Any) -> AgentResult:
        narrative_messages = bus.find(room_id=room.room_id, to_agent=self.agent_id, message_type=MessageType.REQUEST)
        narratives = []
        for message in narrative_messages:
            narratives.extend(message.context.get("narratives", []))
        narratives = sorted(set(narratives)) or ["Unclassified Early Crypto"]

        project_query = extract_project_query(room.topic)
        social_seed = collect_social_seed(memory, room.room_id)
        live_data = collect_live_discovery(self, room, project_query)
        merge_social_seed(live_data, social_seed)
        candidates = build_live_candidates(narratives, room.source_inputs, room.topic, project_query, live_data)
        if not candidates:
            candidates = build_candidates(narratives, room.source_inputs)
        for candidate in candidates:
            memory.upsert_project(candidate)

        used_live_data = bool(
            live_data.get("web_results")
            or live_data.get("github_repos")
            or live_data.get("dex_pairs")
            or live_data.get("coingecko_coins")
            or social_seed_has_signal(live_data.get("social_seed", {}) if isinstance(live_data.get("social_seed"), dict) else {})
        )
        summary = (
            f"Discovered {len(candidates)} candidate projects using web/GitHub/market search signals."
            if used_live_data
            else f"Discovered {len(candidates)} MVP candidate placeholders from narrative signals."
        )
        llm_analysis = self.llm_analysis_pass(
            room=room,
            objective="Review candidate discovery evidence and identify whether the leads are source-backed or placeholders.",
            evidence={
                "narratives": narratives,
                "project_query": project_query,
                "social_seed": social_seed,
                "used_live_data": used_live_data,
                "live_discovery": live_data,
                "candidates": [candidate.to_dict() for candidate in candidates],
            },
            fallback_summary=summary,
        )
        finding = self.write_finding(
            room=room,
            memory=memory,
            finding_type="candidate_discovery",
            summary=summary,
            data={
                "project_query": project_query,
                "live_discovery": live_data,
                "candidates": [candidate.to_dict() for candidate in candidates],
                "llm_analysis": llm_analysis,
            },
            sources=room.source_inputs,
            confidence=0.72 if used_live_data else 0.55,
        )

        candidate_context = {"candidate_ids": [candidate.project_id for candidate in candidates]}
        bus.request(
            room_id=room.room_id,
            from_agent=self.agent_id,
            to_agent="social_kol_agent",
            objective="Check whether candidates have social/KOL momentum.",
            required_output=["mention_trend", "key_accounts", "community_signal", "sources"],
            context=candidate_context,
        )
        bus.request(
            room_id=room.room_id,
            from_agent=self.agent_id,
            to_agent="contract_onchain_agent",
            objective="Check candidate chain, token status, and contract info.",
            required_output=["chain", "token_status", "contract_address", "dex_pair", "sources"],
            context=candidate_context,
        )
        bus.request(
            room_id=room.room_id,
            from_agent=self.agent_id,
            to_agent="product_tech_agent",
            objective="Check candidate website, docs, GitHub, and product state.",
            required_output=["product_status", "docs_status", "github_status", "sources"],
            context=candidate_context,
        )
        bus.request(
            room_id=room.room_id,
            from_agent=self.agent_id,
            to_agent="funding_token_agent",
            objective="Check funding, points, airdrop, and token opportunity signals.",
            required_output=["funding_status", "points_status", "token_opportunity", "sources"],
            context=candidate_context,
        )
        return AgentResult(
            self.agent_id,
            summary,
            {"finding_id": finding.finding_id, "candidate_ids": candidate_context["candidate_ids"]},
            room.source_inputs,
            finding.confidence,
        )


def collect_live_discovery(agent: BaseAgent, room: ResearchRoom, project_query: str) -> dict[str, Any]:
    data: dict[str, Any] = {
        "project_query": project_query,
        "web_queries": [],
        "web_results": [],
        "github_repos": [],
        "coingecko_coins": [],
        "dex_pairs": [],
        "social_seed": {},
    }
    if not project_query:
        return data
    if not should_live_discover(room.topic, project_query):
        data["skipped_reason"] = "topic_does_not_look_like_single_project_lookup"
        return data

    identity_results = project_identity_hints(project_query)
    if external_search_disabled():
        data["web_results"] = rank_results(project_query, dedupe_results(identity_results))[:12]
        data["skipped_reason"] = "external_search_disabled"
        return data

    web_queries = build_web_queries(project_query, room.topic)
    data["web_queries"] = web_queries
    for query in web_queries:
        result = agent.tool_gateway.call(
            agent.agent_id,
            "web_search",
            room_id=room.room_id,
            query=query,
            limit=6,
        )
        if result.get("status") == "success":
            result_data = result.get("data") if isinstance(result.get("data"), dict) else {}
            data["web_results"].extend(result_data.get("results", []))
    data["web_results"].extend(identity_results)

    github_query = f"{project_query} blockchain crypto"
    github_result = agent.tool_gateway.call(
        agent.agent_id,
        "github_search_repos",
        room_id=room.room_id,
        query=github_query,
        limit=5,
    )
    if github_result.get("status") == "success":
        github_data = github_result.get("data") if isinstance(github_result.get("data"), dict) else {}
        data["github_repos"] = github_data.get("repos", [])

    coingecko_result = agent.tool_gateway.call(
        agent.agent_id,
        "coingecko_coin_metadata",
        room_id=room.room_id,
        query=project_query,
        include_detail=True,
    )
    if coingecko_result.get("status") == "success":
        coingecko_data = coingecko_result.get("data") if isinstance(coingecko_result.get("data"), dict) else {}
        data["coingecko_coins"] = coingecko_data.get("coins", [])
        if coingecko_data.get("top_detail"):
            data["coingecko_top_detail"] = coingecko_data.get("top_detail")

    dex_result = agent.tool_gateway.call(
        agent.agent_id,
        "dexscreener_search_pairs",
        room_id=room.room_id,
        query=project_query,
        limit=5,
    )
    if dex_result.get("status") == "success":
        dex_data = dex_result.get("data") if isinstance(dex_result.get("data"), dict) else {}
        data["dex_pairs"] = dex_data.get("pairs", [])

    data["web_results"] = rank_results(project_query, dedupe_results(data["web_results"]))[:12]
    return data


def external_search_disabled() -> bool:
    return os.getenv("JIMMORIA_SKIP_EXTERNAL_SEARCH", "").strip().lower() in {"1", "true", "yes", "on"}


def build_web_queries(project_query: str, topic: str) -> list[str]:
    queries = [
        f"{project_query} crypto project official",
        f"{project_query} blockchain github",
        f"{project_query} token whitepaper",
    ]
    lowered = topic.lower()
    if "pow" in lowered or "proof" in lowered or "작업증명" in topic:
        queries.insert(0, f"{project_query} Proof of Work crypto")
    if "pearl" in project_query.lower():
        queries.insert(0, "Pearl Research Labs PRL Proof of Useful Work")
        queries.insert(1, "site:pearlresearch.ai Pearl Research Labs")
    if project_query.lower().strip() == "3jane":
        queries.insert(0, "3Jane Protocol credit based money market")
        queries.insert(1, "site:3jane.xyz 3Jane whitepaper")
        queries.insert(2, "3Jane Protocol GitHub")
    return dedupe_strings(queries)[:4]


def build_live_candidates(
    narratives: list[str],
    source_ids: list[str],
    topic: str,
    project_query: str,
    live_data: dict[str, Any],
) -> list[ProjectCandidate]:
    web_results = live_data.get("web_results", [])
    github_repos = live_data.get("github_repos", [])
    coingecko_coins = live_data.get("coingecko_coins", [])
    dex_pairs = live_data.get("dex_pairs", [])
    social_seed = live_data.get("social_seed", {}) if isinstance(live_data.get("social_seed"), dict) else {}
    has_social_signal = social_seed_has_signal(social_seed)
    if not any([web_results, github_repos, coingecko_coins, dex_pairs]):
        return []

    evidence_text = " ".join(
        str(value)
        for value in [
            topic,
            project_query,
            *[result.get("title", "") for result in web_results if isinstance(result, dict)],
            *[result.get("snippet", "") for result in web_results if isinstance(result, dict)],
            *[repo.get("description", "") for repo in github_repos if isinstance(repo, dict)],
            social_seed,
        ]
    )
    name = infer_project_name(project_query, evidence_text, coingecko_coins)
    website = select_project_website(project_query, web_results)
    selected_narratives = merge_narratives(narratives, evidence_text)
    token_status = infer_token_status(evidence_text, coingecko_coins, dex_pairs)
    chain = infer_chain(evidence_text, dex_pairs)
    score = score_live_candidate(website, github_repos, coingecko_coins, dex_pairs, evidence_text)
    metadata = {
        "discovery_mode": "live_search",
        "candidate_origin": "live_source_backed",
        "source_backing": "social_first_web_github_market_search" if has_social_signal else "web_github_market_search",
        "project_query": project_query,
        "evidence_urls": evidence_urls(web_results, github_repos, social_seed),
        "social_seed": social_seed,
        "web_results": web_results[:8],
        "github_repos": github_repos[:5],
        "coingecko_coins": coingecko_coins[:5],
        "coingecko_top_detail": live_data.get("coingecko_top_detail"),
        "dex_pairs": dex_pairs[:5],
    }
    return [
        ProjectCandidate(
            name=name,
            website=website,
            chain=chain,
            token_status=token_status,
            narratives=selected_narratives,
            score=score,
            reason_found=(
                f"Resolved from live search query `{project_query}` with X/KOL market-signal, web, GitHub, and market evidence."
                if has_social_signal
                else f"Resolved from live search query `{project_query}` with web/GitHub/market evidence."
            ),
            sources=list(source_ids),
            metadata=metadata,
        )
    ]


def build_candidates(narratives: list[str], source_ids: list[str]) -> list[ProjectCandidate]:
    candidates: list[ProjectCandidate] = []
    for index, narrative in enumerate(narratives[:5], start=1):
        slug = (
            narrative.replace(" x ", " ")
            .replace("/", " ")
            .replace("-", " ")
            .replace("  ", " ")
            .strip()
            .split()[0]
        )
        candidates.append(
            ProjectCandidate(
                name=f"{slug} Candidate {index}",
                reason_found=f"Seed candidate generated from narrative: {narrative}",
                token_status="unknown",
                narratives=[narrative],
                score=max(45.0, 70.0 - index * 5),
                sources=list(source_ids),
                metadata={
                    "mvp_generated": True,
                    "candidate_origin": "mvp_placeholder",
                    "source_backing": "narrative_seed_only",
                },
            )
        )
    return candidates


def extract_project_query(topic: str) -> str:
    english_tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9_.-]*", topic)
    if english_tokens:
        known_identity_tokens = {"3jane", "pearl"}
        for token in english_tokens:
            if token.lower() in known_identity_tokens:
                return token.lower()
        ignored = {
            "official",
            "source",
            "sources",
            "twitter",
            "x",
            "kol",
            "kols",
            "posting",
            "post",
            "posts",
            "article",
            "articles",
            "thread",
            "threads",
            "docs",
            "github",
            "site",
            "website",
            "crypto",
            "project",
            "pow",
            "research",
            "report",
            "dossier",
            "token",
            "coin",
            "investment",
            "create",
            "write",
            "generate",
            "make",
            "new",
        }
        kept = [
            token
            for token in english_tokens
            if token.lower() not in ignored and any(char.isalpha() for char in token)
        ]
        return " ".join(kept[:4] or english_tokens[:4]).strip()
    cleaned = re.sub(r"[/@#]", " ", topic)
    for term in [
        "크립토",
        "프로젝트",
        "리서칭",
        "리서치",
        "조사",
        "분석",
        "진행",
        "보고서",
        "리포트",
        "레포트",
        "도시에",
        "대해서",
        "대해",
        "관련",
        "투자",
        "만들어봐",
        "만들어",
        "작성해봐",
        "작성해",
        "생성해봐",
        "생성해",
        "줘",
    ]:
        cleaned = cleaned.replace(term, " ")
    return " ".join(cleaned.split())[:80]


def should_live_discover(topic: str, project_query: str) -> bool:
    lowered = topic.lower()
    if project_identity_hints(project_query):
        return True
    project_words = [
        "project",
        "protocol",
        "token",
        "coin",
        "pow",
        "pouw",
        "whitepaper",
        "프로젝트",
        "토큰",
        "코인",
        "리서칭",
        "조사",
        "보고서",
        "리포트",
        "레포트",
        "dossier",
        "report",
    ]
    query_tokens = project_query.split()
    return len(query_tokens) <= 4 and any(word in lowered for word in project_words)


def infer_project_name(project_query: str, evidence_text: str, coingecko_coins: list[Any]) -> str:
    lowered = evidence_text.lower()
    if "3jane" in lowered or project_query.lower().strip() == "3jane":
        return "3Jane Protocol"
    if "pearl research labs" in lowered or "proof-of-useful-work" in lowered or "proof of useful work" in lowered:
        return "Pearl Network"
    for coin in coingecko_coins:
        if isinstance(coin, dict) and str(coin.get("name") or "").strip():
            return str(coin["name"])
    return project_query.title()


def merge_narratives(narratives: list[str], evidence_text: str) -> list[str]:
    merged: list[str] = []
    lowered = evidence_text.lower()
    additions = {
        "Proof-of-Useful-Work": ["proof-of-useful-work", "proof of useful work", "pouw"],
        "AI Compute": ["ai compute", "matrix multiplication", "matmul", "gpu"],
        "GPU Mining": ["gpu mining", "block reward", "proof-of-useful-work", "proof of useful work"],
        "L1 Blockchain": [" l1 ", "layer 1", "blockchain"],
        "Crypto Credit": ["credit protocol", "credit-based", "credit based", "credit score"],
        "Undercollateralized Lending": ["undercollateralized", "unsecured lines of credit", "unsecured credit"],
        "zkTLS / Web Proofs": ["zktls", "web proof", "web proofs", "vantagescore"],
    }
    for narrative, triggers in additions.items():
        if any(trigger in lowered for trigger in triggers) and narrative not in merged:
            merged.append(narrative)
    for narrative in narratives:
        if narrative not in merged:
            merged.append(narrative)
    return merged[:8]


def infer_token_status(evidence_text: str, coingecko_coins: list[Any], dex_pairs: list[Any]) -> str:
    lowered = evidence_text.lower()
    if "usd3" in lowered:
        return "usd3_yieldcoin_or_credit_asset_reported"
    if " prl" in f" {lowered}" or "ticker prl" in lowered or "block reward" in lowered or "proof-of-useful-work" in lowered:
        return "native_coin_reported"
    if any(isinstance(coin, dict) and str(coin.get("symbol", "")).lower() == "prl" for coin in coingecko_coins):
        return "market_metadata_found"
    if dex_pairs:
        return "dex_pair_unverified_collision_risk"
    if coingecko_coins:
        return "market_metadata_unverified_collision_risk"
    return "unknown"


def infer_chain(evidence_text: str, dex_pairs: list[Any]) -> str | None:
    lowered = evidence_text.lower()
    if "pearl network" in lowered or "l1 protocol" in lowered or "l1 blockchain" in lowered:
        return "Pearl L1"
    if "3jane" in lowered and "ethereum" in lowered:
        return "Ethereum"
    for pair in dex_pairs:
        if isinstance(pair, dict) and pair.get("chain"):
            return str(pair["chain"])
    return None


def score_live_candidate(
    website: str | None,
    github_repos: list[Any],
    coingecko_coins: list[Any],
    dex_pairs: list[Any],
    evidence_text: str,
) -> float:
    score = 52.0
    if website:
        score += 10
    if github_repos:
        score += 8
    if coingecko_coins or dex_pairs:
        score += 7
    if any(keyword in evidence_text.lower() for keyword in ["whitepaper", "github", "mainnet", "partner", "proof"]):
        score += 7
    return min(score, 88.0)


def select_project_website(project_query: str, web_results: list[Any]) -> str | None:
    normalized = project_query.lower().strip()
    if normalized == "3jane":
        return "https://www.3jane.xyz/"
    if normalized == "pearl":
        return "https://pearlresearch.ai/"

    official_site = select_best_official_site(project_query, web_results)
    if official_site:
        return official_site

    scored: list[tuple[int, str]] = []
    for result in web_results:
        if not isinstance(result, dict):
            continue
        url = str(result.get("url") or "")
        if not url.startswith(("http://", "https://")):
            continue
        score = score_official_url(project_query, result)
        if is_generic_platform_url(url):
            score -= 12
        if urlparse(url).path.lower().endswith(".pdf"):
            score -= 12
        scored.append((score, url))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1] if scored and scored[0][0] > 0 else None


def collect_social_seed(memory: SharedMemory, room_id: str) -> dict[str, Any]:
    findings = [
        finding
        for finding in memory.get_room_findings(room_id)
        if finding.finding_type == "market_signal_intake"
    ]
    if not findings:
        return {}
    latest = findings[-1]
    rows = latest.data.get("rows") if isinstance(latest.data.get("rows"), list) else []
    if not rows or not isinstance(rows[0], dict):
        return {"finding_id": latest.finding_id, "summary": latest.summary}
    row = rows[0]
    return {
        "finding_id": latest.finding_id,
        "summary": latest.summary,
        "project_query": row.get("project_query"),
        "x_api_status": row.get("x_api_status"),
        "kol_builder_status": row.get("kol_builder_status"),
        "x_posts": row.get("x_posts", [])[:12],
        "kol_profiles": row.get("kol_profiles", [])[:12],
        "public_x_results": row.get("public_x_results", [])[:12],
        "official_social_sources": row.get("official_social_sources", [])[:8],
        "handles_checked": row.get("handles_checked", [])[:12],
        "timeline_results": row.get("timeline_results", [])[:8],
        "kol_opinion_results": row.get("kol_opinion_results", [])[:12],
        "who_said_what": row.get("who_said_what", [])[:16],
        "who_said_what_count": row.get("who_said_what_count", 0),
        "social_search_plan": row.get("social_search_plan", []),
        "article_results": row.get("article_results", [])[:12],
        "source_priority": row.get("source_priority"),
    }


def merge_social_seed(live_data: dict[str, Any], social_seed: dict[str, Any]) -> None:
    if not social_seed:
        return
    live_data["social_seed"] = social_seed
    web_results = live_data.setdefault("web_results", [])
    for result in social_seed.get("public_x_results", []):
        if isinstance(result, dict):
            web_results.append({**result, "source": result.get("source", "x_public_web_search")})
    for result in social_seed.get("official_social_sources", []):
        if isinstance(result, dict):
            web_results.append({**result, "source": result.get("source", "official_social_source")})
    for result in social_seed.get("kol_opinion_results", []):
        if isinstance(result, dict):
            web_results.append({**result, "source": result.get("source", "kol_opinion_source")})
    for result in social_seed.get("article_results", []):
        if isinstance(result, dict):
            web_results.append({**result, "source": result.get("source", "kol_article_web_search")})
    for post in social_seed.get("x_posts", []):
        if isinstance(post, dict) and post.get("url"):
            web_results.append(
                {
                    "title": f"X post by @{post.get('author_username') or 'unknown'}",
                    "url": post.get("url"),
                    "snippet": post.get("text"),
                    "host": "x.com",
                    "source": "x_api_recent_search",
                }
            )
    for statement in social_seed.get("who_said_what", []):
        if isinstance(statement, dict) and statement.get("url"):
            web_results.append(
                {
                    "title": f"Social statement by {statement.get('speaker') or 'unknown'}",
                    "url": statement.get("url"),
                    "snippet": statement.get("claim"),
                    "host": urlparse(str(statement.get("url") or "")).netloc.lower(),
                    "source": "who_said_what_social_signal",
                }
            )
    for timeline in social_seed.get("timeline_results", []):
        if isinstance(timeline, dict) and timeline.get("url"):
            web_results.append(
                {
                    "title": f"X timeline for @{timeline.get('handle') or 'unknown'}",
                    "url": timeline.get("url"),
                    "snippet": timeline.get("message") or timeline.get("status") or "X timeline source checked.",
                    "host": "x.com",
                    "source": "x_timeline_check",
                }
            )
    live_data["web_results"] = rank_results(
        str(live_data.get("project_query") or social_seed.get("project_query") or ""),
        dedupe_results(web_results),
    )[:12]


def social_seed_has_signal(social_seed: dict[str, Any]) -> bool:
    return any(
        social_seed.get(bucket)
        for bucket in [
            "x_posts",
            "kol_profiles",
            "public_x_results",
            "official_social_sources",
            "timeline_results",
            "kol_opinion_results",
            "who_said_what",
            "article_results",
        ]
    )


def evidence_urls(web_results: list[Any], github_repos: list[Any], social_seed: dict[str, Any] | None = None) -> list[str]:
    urls = [
        str(result.get("url"))
        for result in web_results
        if isinstance(result, dict) and result.get("url") and not is_low_signal_url(str(result.get("url")))
    ]
    urls.extend(
        str(repo.get("html_url"))
        for repo in github_repos
        if isinstance(repo, dict) and repo.get("html_url") and not is_low_signal_url(str(repo.get("html_url")))
    )
    if social_seed:
        for bucket in [
            "official_social_sources",
            "public_x_results",
            "kol_opinion_results",
            "article_results",
            "x_posts",
            "timeline_results",
            "who_said_what",
        ]:
            for item in social_seed.get(bucket, []):
                if isinstance(item, dict) and item.get("url"):
                    urls.append(str(item["url"]))
    return dedupe_strings(urls)[:12]


def is_low_signal_url(url: str) -> bool:
    return is_low_signal_research_url(url)


def project_identity_hints(project_query: str) -> list[dict[str, Any]]:
    normalized = project_query.lower().strip()
    if normalized == "3jane":
        return [
            {
                "title": "3Jane official website",
                "url": "https://www.3jane.xyz/",
                "snippet": "3Jane is a global credit protocol for crypto-native credit and undercollateralized lending.",
                "host": "www.3jane.xyz",
                "source": "identity_hint",
            },
            {
                "title": "3Jane Protocol whitepaper",
                "url": "https://www.3jane.xyz/pdf/whitepaper.pdf",
                "snippet": "3Jane Protocol whitepaper describes a credit-based money market on Ethereum and unsecured credit lines.",
                "host": "www.3jane.xyz",
                "source": "identity_hint",
            },
            {
                "title": "3Jane docs introduction",
                "url": "https://docs.3jane.xyz/introduction",
                "snippet": "3Jane docs describe a peer-to-pool credit-based money market enabling unsecured lines of credit underwritten against verifiable proofs of crypto and bank assets, future cash flows, and credit scores.",
                "host": "docs.3jane.xyz",
                "source": "identity_hint",
            },
            {
                "title": "3Jane docs suppliers",
                "url": "https://docs.3jane.xyz/architecture/core-money-market/suppliers",
                "snippet": "Supplier docs describe USDC deposits, USD3, sUSD3 first-loss exposure, lock periods, and how idle capital interacts with Aave and credit lines.",
                "host": "docs.3jane.xyz",
                "source": "identity_hint",
            },
            {
                "title": "3Jane docs risks",
                "url": "https://docs.3jane.xyz/risks",
                "snippet": "Risk docs describe supplier risks including smart-contract risk, fraud risk, credit default risk, liquidity risk, oracle/rate-feed risk, and governance risk.",
                "host": "docs.3jane.xyz",
                "source": "identity_hint",
            },
            {
                "title": "3Jane docs protocol global config",
                "url": "https://docs.3jane.xyz/protocol-global-config",
                "snippet": "Protocol config docs describe debt caps, LTV controls, tranche ratios, USD3/sUSD3 parameters, cooldowns, withdrawal windows, and markdown/default settings.",
                "host": "docs.3jane.xyz",
                "source": "identity_hint",
            },
            {
                "title": "3Jane docs developer addresses",
                "url": "https://docs.3jane.xyz/developers/addresses",
                "snippet": "Developer docs list Ethereum mainnet addresses for USD3, sUSD3, MorphoCredit, ProtocolConfig, JANE, rewards distribution, and permission contracts.",
                "host": "docs.3jane.xyz",
                "source": "identity_hint",
            },
            {
                "title": "GitHub - 3jane-protocol",
                "url": "https://github.com/3jane-protocol",
                "snippet": "3Jane Protocol public GitHub organization.",
                "host": "github.com",
                "source": "identity_hint",
            },
            {
                "title": "3Jane official X profile",
                "url": "https://x.com/3janexyz",
                "snippet": "Official 3Jane X/Twitter profile used as the primary social source for announcements and market signal tracking.",
                "host": "x.com",
                "source": "identity_hint",
            },
            {
                "title": "3Jane official seed round announcement",
                "url": "https://x.com/3janexyz/status/1930264347441615188",
                "snippet": "3Jane announced a $5.2M seed round led by Paradigm and positioned the protocol around crypto-native credit.",
                "host": "x.com",
                "source": "identity_hint",
            },
            {
                "title": "Wintermute Ventures on backing 3Jane",
                "url": "https://x.com/wmt_ventures/status/1930336436433367395",
                "snippet": "Wintermute Ventures publicly said it backed 3Jane and @_yakovsky building the credit protocol.",
                "host": "x.com",
                "source": "identity_hint",
            },
            {
                "title": "The Block: Paradigm leads 3Jane seed round",
                "url": "https://www.theblock.co/post/356872/paradigm-leads-5-million-seed-round-in-crypto-credit-startup-3jane",
                "snippet": "The Block reported Paradigm led a $5.2M seed round in 3Jane as the crypto credit startup emerged from stealth.",
                "host": "theblock.co",
                "source": "identity_hint",
            },
            {
                "title": "Delphi Digital: Engineering Real Credit Onchain",
                "url": "https://members.delphidigital.io/reports/engineering-real-credit-onchain-the-3jane-bet",
                "snippet": "Delphi Digital analyzed 3Jane's attempt to reimagine undercollateralized lending onchain after earlier credit-market failures.",
                "host": "members.delphidigital.io",
                "source": "identity_hint",
            },
            {
                "title": "3Jane official report: 3Jane is Evolving",
                "url": "https://www.3jane.xyz/reports/3jane-is-evolving",
                "snippet": "Official 3Jane report page used as a product and narrative update source.",
                "host": "www.3jane.xyz",
                "source": "identity_hint",
            },
            {
                "title": "Leviathan: 3Jane lending protocol explained",
                "url": "https://leviathannews.substack.com/p/3jane-lending-protocol-explained",
                "snippet": "Public article explaining 3Jane's lending protocol and how crypto borrowing works.",
                "host": "leviathannews.substack.com",
                "source": "identity_hint",
            },
            {
                "title": "DefiLlama 3Jane protocol profile",
                "url": "https://defillama.com/protocol/3jane",
                "snippet": "DefiLlama tracks 3Jane as a peer-to-pool credit-based money market with TVL, raise, and protocol metrics.",
                "host": "defillama.com",
                "source": "identity_hint",
            },
            {
                "title": "ETH Daily: 3Jane Introduces A Credit Market Protocol",
                "url": "https://ethdaily.io/603",
                "snippet": "ETH Daily covered 3Jane's credit-based money market protocol, soft collateral, USD3, and beta/public release framing.",
                "host": "ethdaily.io",
                "source": "identity_hint",
            },
        ]
    if normalized != "pearl":
        return []
    return [
        {
            "title": "Pearl Whitepaper",
            "url": "https://pearlresearch.ai/",
            "snippet": "Pearl Research Labs official whitepaper for the Pearl Network, a Proof-of-Useful-Work L1 protocol.",
            "host": "pearlresearch.ai",
            "source": "identity_hint",
        },
        {
            "title": "GitHub - pearl-research-labs/pearl",
            "url": "https://github.com/pearl-research-labs/pearl",
            "snippet": "Official Pearl Network monorepo.",
            "host": "github.com",
            "source": "identity_hint",
        },
    ]


def rank_results(project_query: str, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(results, key=lambda result: result_relevance(project_query, result), reverse=True)


def result_relevance(project_query: str, result: dict[str, Any]) -> int:
    query_tokens = [token.lower() for token in project_query.split() if len(token) > 2]
    text = f"{result.get('title', '')} {result.get('snippet', '')} {result.get('host', '')} {result.get('url', '')}".lower()
    score = 0
    if result.get("source") == "identity_hint":
        score += 50
    score += sum(5 for token in query_tokens if token in text)
    if any(keyword in text for keyword in ["proof-of-useful-work", "proof of useful work", "pearl network", "pearl research labs", "whitepaper"]):
        score += 12
    if any(keyword in text for keyword in ["github.com/pearl-research-labs", "pearlresearch.ai"]):
        score += 12
    if any(keyword in text for keyword in ["oyster protocol", "exit scam", "girl with a pearl", "kona pearl", "blackpearl", "bridged pearl"]):
        score -= 25
    if any(host in text for host in ["coinmarketcap.com", "coingecko.com", "tomshardware.com", "finance.yahoo.com"]):
        score -= 2
    return score


def dedupe_results(results: list[Any]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        url = str(result.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(result)
    return deduped


def dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped
