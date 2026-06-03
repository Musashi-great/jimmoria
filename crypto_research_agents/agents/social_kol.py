from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from crypto_research_agents.agents.base import AgentResult, BaseAgent
from crypto_research_agents.agents.discovery import extract_project_query, project_identity_hints
from crypto_research_agents.core.bus import CollaborationBus
from crypto_research_agents.core.memory import SharedMemory
from crypto_research_agents.core.message import MessageType
from crypto_research_agents.core.room import ResearchRoom
from crypto_research_agents.core.source_quality import (
    is_project_social_url,
    project_tokens,
    result_mentions_project,
    select_best_official_site,
)


class SocialKOLAgent(BaseAgent):
    agent_id = "social_kol_agent"
    name = "Social / KOL Agent"
    task_type = "social_summary"

    def run(self, room: ResearchRoom, memory: SharedMemory, bus: CollaborationBus, **kwargs: Any) -> AgentResult:
        requests = bus.find(room_id=room.room_id, to_agent=self.agent_id, message_type=MessageType.REQUEST)
        if kwargs.get("seed_mode") or not requests:
            return self._run_market_signal_intake(room, memory, bus, **kwargs)

        candidate_ids = _collect_candidate_ids(requests)
        rows = []
        for project_id in candidate_ids:
            project = memory.projects[project_id]
            tool_result = self.tool_gateway.call(
                self.agent_id,
                "x_search_posts",
                room_id=room.room_id,
                query=project.name,
            )
            website_result = self.tool_gateway.call(
                self.agent_id,
                "crawl_website",
                room_id=room.room_id,
                url=_select_social_source_url(project),
                project_name=project.name,
            )
            web_result = self.tool_gateway.call(
                self.agent_id,
                "web_search",
                room_id=room.room_id,
                query=f"{project.name} X Twitter official community",
                limit=5,
            )
            website_data = website_result.get("data") if isinstance(website_result.get("data"), dict) else {}
            web_data = web_result.get("data") if isinstance(web_result.get("data"), dict) else {}
            raw_web_results = [
                tag_search_result(result, query=f"{project.name} X Twitter official community")
                for result in web_data.get("results", [])
                if isinstance(result, dict)
            ]
            social_urls = _social_urls(project, website_data, raw_web_results)
            seed = project.metadata.get("social_seed", {}) if isinstance(project.metadata, dict) else {}
            seed_results = seed_items(seed, ["public_x_results", "official_social_sources", "kol_opinion_results"])
            handles = extract_handles_from_social_results(
                [
                    *seed_results,
                    *raw_web_results,
                    *({"url": url, "title": f"{project.name} social source", "source": "candidate_social_url"} for url in social_urls),
                ]
            )[:5]
            timeline_results = fetch_timelines(self, room.room_id, handles, limit=8)
            public_x_results = [result for result in raw_web_results if _is_x_result(result)]
            kol_opinion_results = extract_kol_opinion_results([*raw_web_results, *seed_results])
            who_said_what = build_who_said_what(
                project_name=project.name,
                x_posts=_posts_from_result(tool_result),
                timeline_results=timeline_results,
                public_x_results=[*public_x_results, *seed_items(seed, ["public_x_results"])],
                kol_profiles=seed_items(seed, ["kol_profiles"]),
                kol_opinion_results=kol_opinion_results,
                official_social_sources=[{"title": f"{project.name} social source", "url": url} for url in social_urls],
            )
            rows.append(
                {
                    "project_id": project_id,
                    "project_name": project.name,
                    "mention_trend": _mention_trend(tool_result),
                    "key_accounts": social_urls,
                    "handles_checked": handles,
                    "timeline_results": timeline_results,
                    "public_x_results": public_x_results[:8],
                    "kol_opinion_results": kol_opinion_results[:8],
                    "who_said_what": who_said_what[:12],
                    "who_said_what_count": len(who_said_what),
                    "community_signal": _community_signal(social_urls, tool_result["status"], web_result["status"]),
                    "tool_status": tool_result["status"],
                    "website_status": website_result["status"],
                    "web_search_status": web_result["status"],
                }
            )

        linked = sum(1 for row in rows if row["key_accounts"])
        statement_count = sum(int(row.get("who_said_what_count") or 0) for row in rows)
        summary = (
            f"Social/KOL check found public social links for {linked}/{len(rows)} candidates and mapped {statement_count} who-said-what statement(s); live X status: {_status_summary(rows)}."
            if rows
            else "Social/KOL check found no candidate projects to inspect."
        )
        llm_analysis = self.llm_analysis_pass(
            room=room,
            objective="Interpret public web and X/KOL evidence, separate official links from live mention history, and avoid private chat connectors.",
            evidence={"rows": rows},
            fallback_summary=summary,
        )
        finding = self.write_finding(
            room=room,
            memory=memory,
            finding_type="social_kol_signal",
            summary=summary,
            data={"rows": rows, "llm_analysis": llm_analysis},
            confidence=0.35,
        )
        for request in requests:
            bus.response(
                request=request,
                from_agent=self.agent_id,
                result={"rows": rows, "finding_id": finding.finding_id, "llm_analysis": llm_analysis},
                confidence=finding.confidence,
                notes=[summary, str(llm_analysis.get("summary", summary))],
            )
        return AgentResult(self.agent_id, summary, {"finding_id": finding.finding_id, "rows": rows, "llm_analysis": llm_analysis}, confidence=finding.confidence)

    def _run_market_signal_intake(
        self,
        room: ResearchRoom,
        memory: SharedMemory,
        bus: CollaborationBus,
        **kwargs: Any,
    ) -> AgentResult:
        project_query = str(kwargs.get("project_query") or extract_project_query(room.topic)).strip()
        if not project_query:
            summary = "Market signal intake skipped because no project or narrative query was resolved."
            finding = self.write_finding(
                room=room,
                memory=memory,
                finding_type="market_signal_intake",
                summary=summary,
                data={"project_query": "", "rows": []},
                confidence=0.15,
            )
            return AgentResult(self.agent_id, summary, {"finding_id": finding.finding_id, "project_query": ""}, confidence=0.15)

        x_query = build_x_search_query(project_query)
        x_result = self.tool_gateway.call(
            self.agent_id,
            "x_search_posts",
            room_id=room.room_id,
            query=x_query,
            limit=20,
        )
        kol_result = self.tool_gateway.call(
            self.agent_id,
            "x_build_kol_list",
            room_id=room.room_id,
            query=f"{project_query} crypto web3",
            limit=20,
        )

        web_results: list[dict[str, Any]] = []
        web_queries = build_public_social_queries(project_query, room.topic)
        for query in web_queries:
            web_result = self.tool_gateway.call(
                self.agent_id,
                "web_search",
                room_id=room.room_id,
                query=query,
                limit=6,
            )
            if web_result.get("status") == "success":
                data = web_result.get("data") if isinstance(web_result.get("data"), dict) else {}
                web_results.extend(
                    tag_search_result(result, query=query)
                    for result in data.get("results", [])
                    if isinstance(result, dict)
                )

        web_results.extend(source_social_results(room, memory, project_query))
        web_results.extend(identity_social_results(project_query))
        web_results = [
            result
            for result in dedupe_result_dicts(web_results)
            if is_relevant_market_signal_result(project_query, result)
        ]
        public_x_results = [result for result in web_results if _is_x_result(result)]
        article_results = [result for result in web_results if not _is_x_result(result)]
        x_posts = _posts_from_result(x_result)
        kol_profiles = _profiles_from_result(kol_result)
        official_social_sources = select_official_social_sources(project_query, public_x_results)
        handles = extract_handles_from_social_results(
            [
                *public_x_results,
                *official_social_sources,
                *x_posts,
                *kol_profiles,
            ]
        )[:6]
        timeline_results = fetch_timelines(self, room.room_id, handles, limit=8)
        kol_opinion_results = extract_kol_opinion_results(web_results)
        who_said_what = build_who_said_what(
            project_name=project_query,
            x_posts=x_posts,
            timeline_results=timeline_results,
            public_x_results=public_x_results,
            kol_profiles=kol_profiles,
            kol_opinion_results=kol_opinion_results,
            official_social_sources=official_social_sources,
        )
        rows = [
            {
                "project_query": project_query,
                "x_query": x_query,
                "public_web_queries": web_queries,
                "social_search_plan": [
                    "1. Search live X recent posts when X_BEARER_TOKEN exists.",
                    "2. Search public X pages and KOL/article/thesis pages through public web.",
                    "3. Extract handles and read timelines where possible.",
                    "4. Pass only source-backed social evidence to Discovery/Product/Report.",
                ],
                "x_api_status": x_result.get("status"),
                "kol_builder_status": kol_result.get("status"),
                "x_post_count": len(x_posts),
                "public_x_result_count": len(public_x_results),
                "article_result_count": len(article_results),
                "official_social_sources": official_social_sources[:8],
                "handles_checked": handles,
                "timeline_results": timeline_results[:8],
                "kol_opinion_results": kol_opinion_results[:12],
                "kol_opinion_count": len(kol_opinion_results),
                "who_said_what": who_said_what[:16],
                "who_said_what_count": len(who_said_what),
                "x_posts": x_posts[:12],
                "kol_profiles": kol_profiles[:12],
                "public_x_results": public_x_results[:12],
                "article_results": article_results[:12],
                "source_priority": "x_kol_posts_first_then_official_site_docs_github",
            }
        ]
        summary = (
            "Market signal intake collected "
            f"{len(x_posts)} live X posts, {len(kol_profiles)} KOL profiles, "
            f"{len(public_x_results)} public X web hits, {len(kol_opinion_results)} KOL/article opinion hits, "
            f"and {len(who_said_what)} who-said-what rows before official-source verification."
        )
        llm_analysis = self.llm_analysis_pass(
            room=room,
            objective=(
                "Use X/Twitter, KOL, public posts, and article evidence as the first market-signal layer. "
                "Do not verify product claims here; hand those to product/docs/GitHub agents."
            ),
            evidence={"rows": rows},
            fallback_summary=summary,
        )
        finding = self.write_finding(
            room=room,
            memory=memory,
            finding_type="market_signal_intake",
            summary=summary,
            data={"rows": rows, "llm_analysis": llm_analysis},
            confidence=_seed_confidence(rows[0]),
        )
        bus.update(
            room_id=room.room_id,
            from_agent=self.agent_id,
            summary="Market signal intake completed before candidate discovery.",
            payload={"finding_id": finding.finding_id, "project_query": project_query, "rows": rows},
        )
        return AgentResult(
            self.agent_id,
            summary,
            {"finding_id": finding.finding_id, "project_query": project_query, "rows": rows, "llm_analysis": llm_analysis},
            confidence=finding.confidence,
        )


def _collect_candidate_ids(requests: list[Any]) -> list[str]:
    candidate_ids: list[str] = []
    for request in requests:
        candidate_ids.extend(request.context.get("candidate_ids", []))
    return sorted(set(candidate_ids))


def _select_social_source_url(project: Any) -> str | None:
    metadata = project.metadata if isinstance(project.metadata, dict) else {}
    project_query = str(metadata.get("project_query") or project.name)
    web_results = metadata.get("web_results", []) if isinstance(metadata.get("web_results"), list) else []
    return select_best_official_site(project_query, web_results) or project.website


def _social_urls(project: Any, website_data: dict[str, Any], web_results: list[Any]) -> list[str]:
    urls: list[str] = []
    metadata = project.metadata if isinstance(project.metadata, dict) else {}
    official_links = website_data.get("official_links") if isinstance(website_data.get("official_links"), dict) else {}
    for bucket in ["x"]:
        for link in official_links.get(bucket, []):
            if isinstance(link, dict):
                urls.extend(_maybe_social_url(project, link.get("url"), trusted=True))
    for result in metadata.get("web_results", []):
        if isinstance(result, dict):
            urls.extend(_maybe_social_url(project, result.get("url")))
    for result in web_results:
        if isinstance(result, dict):
            urls.extend(_maybe_social_url(project, result.get("url")))
    return _dedupe(urls)[:10]


def _maybe_social_url(project: Any, value: object, *, trusted: bool = False) -> list[str]:
    url = str(value or "")
    if is_project_social_url(project, url, trusted_official_link=trusted):
        return [url]
    return []


def _community_signal(social_urls: list[str], x_status: str, web_status: str) -> str:
    if social_urls:
        if x_status == "success":
            return "Official/community social links found and live X search returned post evidence."
        return "Official X/community links found through public web evidence; private Telegram/Discord connectors are intentionally out of scope."
    if x_status in {"missing_secret", "unconfigured"} and web_status == "success":
        return "No X handle resolved from web search; continue with public website, docs, GitHub, and market metadata evidence."
    if x_status == "success":
        return "Live X connector returned data, but no official/community URL was resolved."
    return "Live social connector did not return usable evidence yet."


def _mention_trend(tool_result: dict[str, Any]) -> str:
    status = str(tool_result.get("status") or "")
    if status == "success":
        posts = tool_result.get("data", {}).get("posts", []) if isinstance(tool_result.get("data"), dict) else []
        return f"live_posts:{len(posts)}"
    return status or "unknown"


def _status_summary(rows: list[dict[str, Any]]) -> str:
    statuses = sorted({str(row.get("tool_status") or "unknown") for row in rows})
    return ", ".join(statuses) if statuses else "unknown"


def build_x_search_query(project_query: str) -> str:
    quoted = f'"{project_query}"' if " " in project_query else project_query
    return f"{quoted} (crypto OR web3 OR protocol OR token OR chain OR DeFi) -is:retweet"


def build_public_social_queries(project_query: str, topic: str) -> list[str]:
    quoted = f'"{project_query}"'
    queries = [
        f"site:x.com {quoted} crypto",
        f"site:x.com {quoted} official",
        f"{quoted} crypto KOL opinion",
        f"{quoted} crypto thesis thread",
        f"{quoted} crypto article analysis",
        f"{quoted} web3 thread",
    ]
    lowered = topic.lower()
    if "pow" in lowered or "proof" in lowered:
        queries.insert(1, f"site:x.com {quoted} proof of work crypto")
    return _dedupe(queries)[:7]


def tag_search_result(result: dict[str, Any], *, query: str) -> dict[str, Any]:
    tagged = dict(result)
    tagged["query"] = query
    tagged["search_intent"] = classify_social_query(query)
    return tagged


def classify_social_query(query: str) -> str:
    lowered = query.lower()
    if "site:x.com" in lowered or "twitter" in lowered:
        return "x_public_post_search"
    if any(word in lowered for word in ["kol", "opinion", "thesis", "thread"]):
        return "kol_opinion_search"
    if "article" in lowered or "analysis" in lowered:
        return "article_analysis_search"
    return "public_market_signal_search"


def identity_social_results(project_query: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for hint in project_identity_hints(project_query):
        if not isinstance(hint, dict):
            continue
        url = str(hint.get("url") or "")
        if _looks_like_social_url(url):
            results.append(
                {
                    **hint,
                    "search_intent": "identity_gate_official_social_hint",
                }
            )
    return results


def select_official_social_sources(project_query: str, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    tokens = project_tokens(project_query)
    for result in results:
        if not isinstance(result, dict) or not _is_x_result(result):
            continue
        text = " ".join(str(result.get(key, "")) for key in ["title", "snippet", "url", "source"]).lower()
        is_hint = result.get("source") in {"identity_hint", "user_supplied_social_source", "embedded_social_source"}
        looks_official = "official" in text or "profile" in text or any(token and token in text for token in tokens)
        if is_hint or looks_official:
            selected.append({**result, "social_role": "official_or_candidate_project_handle"})
    return dedupe_result_dicts(selected)[:8]


def seed_items(seed: dict[str, Any], keys: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not isinstance(seed, dict):
        return items
    for key in keys:
        value = seed.get(key)
        if isinstance(value, list):
            items.extend(item for item in value if isinstance(item, dict))
    return items


def extract_handles_from_social_results(results: list[dict[str, Any]]) -> list[str]:
    handles: list[str] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        for key in ["url", "author_username", "username", "handle", "speaker"]:
            value = str(result.get(key) or "").strip()
            if not value:
                continue
            if key == "url":
                handle = extract_handle_from_url(value)
                if handle:
                    handles.append(handle)
            elif value.startswith("@"):
                handles.append(value.lstrip("@"))
            elif re.fullmatch(r"[A-Za-z0-9_]{2,20}", value):
                handles.append(value)
        text = " ".join(str(result.get(key, "")) for key in ["title", "snippet", "text", "claim"])
        handles.extend(match.lstrip("@") for match in re.findall(r"@[A-Za-z0-9_]{2,20}", text))
    return _dedupe([handle for handle in handles if is_useful_handle(handle)])[:12]


def extract_handle_from_url(url: str) -> str | None:
    if not _looks_like_social_url(url):
        return None
    parsed = urlparse(url)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if not segments:
        return None
    candidate = segments[0].lstrip("@")
    return candidate if is_useful_handle(candidate) else None


def is_useful_handle(handle: str) -> bool:
    normalized = str(handle or "").strip().lstrip("@").lower()
    return bool(normalized) and normalized not in {
        "x",
        "twitter",
        "i",
        "search",
        "share",
        "status",
        "intent",
        "home",
        "explore",
        "notifications",
    }


def fetch_timelines(agent: Any, room_id: str, handles: list[str], *, limit: int = 8) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for handle in handles[:5]:
        result = agent.tool_gateway.call(
            agent.agent_id,
            "x_get_user_timeline",
            room_id=room_id,
            handle=handle,
            limit=limit,
        )
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        posts = data.get("posts") if isinstance(data.get("posts"), list) else []
        rows.append(
            {
                "handle": handle,
                "status": result.get("status"),
                "message": result.get("message"),
                "profile": data.get("user") if isinstance(data.get("user"), dict) else {},
                "posts": [post for post in posts if isinstance(post, dict)][:limit],
                "url": f"https://x.com/{handle}",
            }
        )
    return rows


def extract_kol_opinion_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        text = " ".join(str(result.get(key, "")) for key in ["title", "snippet", "url", "query", "search_intent"]).lower()
        if any(term in text for term in ["kol", "opinion", "thesis", "thread", "analysis", "coverage", "introduces"]):
            selected.append({**result, "social_role": result.get("social_role") or "kol_or_article_opinion"})
    return dedupe_result_dicts(selected)[:12]


def build_who_said_what(
    *,
    project_name: str,
    x_posts: list[dict[str, Any]],
    timeline_results: list[dict[str, Any]],
    public_x_results: list[dict[str, Any]],
    kol_profiles: list[dict[str, Any]],
    kol_opinion_results: list[dict[str, Any]],
    official_social_sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in official_social_sources[:4]:
        handle = extract_handle_from_url(str(source.get("url") or "")) or str(source.get("speaker") or "official project account")
        rows.append(
            social_statement(
                source_type="official_social_source",
                speaker=f"@{handle}" if handle and not str(handle).startswith("@") else handle,
                claim=f"Official/candidate X source identified for {project_name}.",
                url=source.get("url"),
                confidence="medium" if source.get("source") == "identity_hint" else "low",
            )
        )
    for post in x_posts[:8]:
        rows.append(
            social_statement(
                source_type="x_recent_post",
                speaker=f"@{post.get('author_username') or 'unknown'}",
                claim=post.get("text"),
                url=post.get("url"),
                created_at=post.get("created_at"),
                confidence="medium",
            )
        )
    for timeline in timeline_results[:5]:
        handle = str(timeline.get("handle") or "unknown").lstrip("@")
        posts = timeline.get("posts") if isinstance(timeline.get("posts"), list) else []
        if posts:
            for post in posts[:3]:
                if isinstance(post, dict):
                    rows.append(
                        social_statement(
                            source_type="x_timeline_post",
                            speaker=f"@{handle}",
                            claim=post.get("text"),
                            url=post.get("url") or f"https://x.com/{handle}",
                            created_at=post.get("created_at"),
                            confidence="medium",
                        )
                    )
        else:
            rows.append(
                social_statement(
                    source_type="x_timeline_status",
                    speaker=f"@{handle}",
                    claim=str(timeline.get("message") or timeline.get("status") or "Timeline was requested but not available."),
                    url=timeline.get("url"),
                    confidence="low",
                )
            )
    for result in public_x_results[:8]:
        handle = extract_handle_from_url(str(result.get("url") or "")) or "public X result"
        rows.append(
            social_statement(
                source_type="public_x_web_result",
                speaker=f"@{handle}" if handle != "public X result" else handle,
                claim=result.get("snippet") or result.get("title"),
                url=result.get("url"),
                confidence="low",
            )
        )
    for profile in kol_profiles[:6]:
        handle = str(profile.get("username") or profile.get("handle") or "").lstrip("@")
        rows.append(
            social_statement(
                source_type="kol_profile_candidate",
                speaker=f"@{handle}" if handle else str(profile.get("name") or "KOL profile"),
                claim=f"KOL/profile candidate connected to the query; followers={profile.get('followers', 'unknown')}.",
                url=profile.get("url") or (f"https://x.com/{handle}" if handle else None),
                confidence="low",
            )
        )
    for result in kol_opinion_results[:8]:
        speaker = extract_handle_from_url(str(result.get("url") or "")) or result.get("host") or "article/KOL source"
        rows.append(
            social_statement(
                source_type="kol_article_or_thread",
                speaker=f"@{speaker}" if _looks_like_social_url(str(result.get("url") or "")) and not str(speaker).startswith("@") else str(speaker),
                claim=result.get("snippet") or result.get("title"),
                url=result.get("url"),
                confidence="low",
            )
        )
    return dedupe_statement_rows(rows)[:20]


def social_statement(
    *,
    source_type: str,
    speaker: object,
    claim: object,
    url: object = None,
    created_at: object = None,
    confidence: str = "low",
) -> dict[str, Any]:
    return {
        "source_type": source_type,
        "speaker": clean_statement_text(speaker, fallback="unknown"),
        "claim": clean_statement_text(claim, fallback="No text captured."),
        "url": str(url or ""),
        "created_at": str(created_at or ""),
        "confidence": confidence,
    }


def clean_statement_text(value: object, *, fallback: str) -> str:
    text = " ".join(str(value or "").split())
    return text[:500] if text else fallback


def dedupe_statement_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        key = (str(row.get("source_type") or ""), str(row.get("speaker") or ""), str(row.get("url") or row.get("claim") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def dedupe_result_dicts(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for result in results:
        url = str(result.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(result)
    return deduped


def is_relevant_market_signal_result(project_query: str, result: dict[str, Any]) -> bool:
    if result.get("source") in {"user_supplied_social_source", "embedded_social_source"}:
        return True
    if _is_low_quality_public_signal_host(str(result.get("url") or "")):
        return False
    tokens = project_tokens(project_query)
    if not tokens:
        return True
    if _is_x_result(result):
        return result_mentions_project(result, tokens)
    return result_mentions_project_headline_or_url(result, tokens)


def result_mentions_project_headline_or_url(result: dict[str, Any], tokens: list[str]) -> bool:
    text = " ".join(str(result.get(key, "")) for key in ["title", "url", "host"]).lower()
    return any(token and token in text for token in tokens)


def _is_low_quality_public_signal_host(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host in {"x.com", "twitter.com"}:
        return False
    return host in {
        "facebook.com",
        "m.facebook.com",
        "instagram.com",
        "www-fallback.instagram.com",
        "youtube.com",
        "youtu.be",
        "tiktok.com",
        "coinmarketcap.com",
        "mexc.co",
        "scribd.com",
        "sorsa.io",
        "alphagrowth.io",
    }


def source_social_results(room: ResearchRoom, memory: SharedMemory, project_query: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for source_id in room.source_inputs:
        source = memory.sources.get(source_id)
        if source is None:
            continue
        url = str(source.url or "").strip()
        title = str(source.title or project_query or "source")
        content = str(source.content or "")
        if _looks_like_social_url(url):
            results.append(
                {
                    "title": f"User-supplied X/Twitter source for {project_query}",
                    "url": url,
                    "snippet": f"User supplied this social source while requesting research on {project_query}.",
                    "host": url.split("/")[2].lower() if "://" in url else "",
                    "source": "user_supplied_social_source",
                }
            )
        for embedded_url in extract_social_urls(content):
            results.append(
                {
                    "title": f"Embedded social source for {project_query}",
                    "url": embedded_url,
                    "snippet": content[:300],
                    "host": embedded_url.split("/")[2].lower() if "://" in embedded_url else "",
                    "source": "embedded_social_source",
                }
            )
    return results


def extract_social_urls(text: str) -> list[str]:
    values = []
    for token in str(text or "").replace("\n", " ").split():
        cleaned = token.strip("()[]{}<>,.;'\"")
        if _looks_like_social_url(cleaned):
            values.append(cleaned)
    return _dedupe(values)[:10]


def _looks_like_social_url(url: str) -> bool:
    lowered = str(url or "").lower()
    return lowered.startswith(("https://x.com/", "https://twitter.com/", "http://x.com/", "http://twitter.com/"))


def _is_x_result(result: dict[str, Any]) -> bool:
    url = str(result.get("url") or "").lower()
    host = str(result.get("host") or "").lower() or urlparse(url).netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    return host in {"x.com", "twitter.com"} or host.endswith(".twitter.com")


def _posts_from_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    posts = data.get("posts") if isinstance(data.get("posts"), list) else []
    return [post for post in posts if isinstance(post, dict)]


def _profiles_from_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    profiles = data.get("profiles") if isinstance(data.get("profiles"), list) else []
    return [profile for profile in profiles if isinstance(profile, dict)]


def _seed_confidence(row: dict[str, Any]) -> float:
    if row.get("who_said_what_count", 0) or row.get("official_social_sources"):
        return 0.66
    if row.get("x_post_count", 0) or row.get("public_x_result_count", 0):
        return 0.62
    if row.get("article_result_count", 0) or row.get("kol_opinion_count", 0):
        return 0.5
    return 0.28


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped
