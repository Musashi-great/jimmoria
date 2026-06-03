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
        live_data = collect_live_discovery(self, room, project_query)
        candidates = build_live_candidates(narratives, room.source_inputs, room.topic, project_query, live_data)
        if not candidates:
            candidates = build_candidates(narratives, room.source_inputs)
        for candidate in candidates:
            memory.upsert_project(candidate)

        used_live_data = bool(live_data.get("web_results") or live_data.get("github_repos") or live_data.get("dex_pairs") or live_data.get("coingecko_coins"))
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
        "source_backing": "web_github_market_search",
        "project_query": project_query,
        "evidence_urls": evidence_urls(web_results, github_repos),
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
            reason_found=f"Resolved from live search query `{project_query}` with web/GitHub/market evidence.",
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
        ignored = {
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
        "GPU Mining": ["gpu mining", "mining", "block reward"],
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
    if " prl" in f" {lowered}" or "ticker prl" in lowered or "block reward" in lowered or "mining" in lowered:
        return "native_coin_reported"
    if "usd3" in lowered:
        return "usd3_yieldcoin_or_credit_asset_reported"
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
    scored: list[tuple[int, str]] = []
    query_tokens = [token.lower() for token in project_query.split() if len(token) > 2]
    for result in web_results:
        if not isinstance(result, dict):
            continue
        url = str(result.get("url") or "")
        if not url.startswith(("http://", "https://")):
            continue
        host = urlparse(url).netloc.lower()
        text = f"{result.get('title', '')} {result.get('snippet', '')} {host}".lower()
        score = 0
        if result.get("source") == "identity_hint":
            score += 30
        if any(token in host for token in query_tokens):
            score += 8
        if any(word in text for word in ["official", "whitepaper", "docs", "foundation", "research labs"]):
            score += 4
        if any(word in text for word in ["proof-of-useful-work", "proof of useful work", "pearl network"]):
            score += 8
        if host in {"github.com", "x.com", "twitter.com"} or any(bad in host for bad in ["coinmarketcap", "coingecko", "tomshardware", "reddit", "lablockchainsummit"]):
            score -= 8
        scored.append((score, url))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1] if scored and scored[0][0] > 0 else None


def evidence_urls(web_results: list[Any], github_repos: list[Any]) -> list[str]:
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
    return dedupe_strings(urls)[:12]


def is_low_signal_url(url: str) -> bool:
    lowered = url.lower()
    return any(
        marker in lowered
        for marker in [
            "cookie-policy",
            "privacy-policy",
            "accessibility",
            "/terms",
            "/legal",
        ]
    )


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
                "title": "GitHub - 3jane-protocol",
                "url": "https://github.com/3jane-protocol",
                "snippet": "3Jane Protocol public GitHub organization.",
                "host": "github.com",
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
