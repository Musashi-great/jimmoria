from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from crypto_research_agents.core.memory import FindingRecord
from crypto_research_agents.core.project_identity import empty_evidence_slots, evidence_slots_to_dict


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



def build_project_dossier_evidence_pack(
    project: Any,
    findings: list[FindingRecord],
    source_log: list[dict[str, str]],
) -> dict[str, dict[str, Any]]:
    """Build the mandatory single-project dossier evidence slots.

    This complements the claim ledger: every slot is present even when evidence is
    missing, so the report can clearly mark unsupported areas as 미확인 instead
    of silently omitting them.
    """

    slots = empty_evidence_slots()
    if project is None:
        slots["unanswered_questions"].notes.append("프로젝트 후보가 아직 확정되지 않았습니다.")
        return evidence_slots_to_dict(slots)

    urls = [item.get("url", "") for item in source_log if item.get("url")]
    product_rows = finding_rows(findings, "product_tech_signal", project)
    token_rows = finding_rows(findings, "contract_token_info", project)
    funding_rows = finding_rows(findings, "funding_token_signal", project)
    social_rows = finding_rows(findings, "social_kol_signal", project)
    social_seed_rows = extract_social_seed_rows(findings)

    _confirm_slot(slots, "official_website", [getattr(project, "website", ""), *_urls_like(urls, "official")], "공식 도메인 후보가 source log에 있습니다.")
    _confirm_slot(slots, "official_x", _urls_like(urls, "x"), "공식/후보 X 링크가 source log에 있습니다.")
    _confirm_slot(slots, "docs", _urls_like(urls, "docs"), "문서/docs 링크가 확인되었습니다.")
    _confirm_slot(slots, "github", _urls_like(urls, "github"), "GitHub 링크가 확인되었습니다.")

    chain_values = _row_values(token_rows, ["chain", "chain_guess", "network"])
    contract_values = _row_values(token_rows, ["contract", "contract_address", "token_address"])
    dex_values = _row_values(token_rows, ["dex_pair", "pair", "pair_url"])
    cg_values = _row_values(token_rows, ["coingecko", "coinmarketcap", "coingecko_url", "cmc_url"])
    explorer_values = _row_values(token_rows, ["explorer", "explorer_url", "source_verified", "verified_source"])

    _confirm_slot(slots, "chain", chain_values, "체인/네트워크 힌트가 token/on-chain finding에 있습니다.")
    _confirm_slot(slots, "contract", contract_values, "컨트랙트/토큰 주소 finding이 있습니다.")
    _confirm_slot(slots, "dex_pair", dex_values, "DEX pair 후보 finding이 있습니다.")
    _confirm_slot(slots, "coingecko_cmc", cg_values, "CoinGecko/CMC 메타데이터 후보가 있습니다.")
    _confirm_slot(slots, "explorer_verification", [*_urls_like(urls, "explorer"), *explorer_values], "Explorer 검증 후보가 있습니다.")

    _confirm_slot(slots, "product_status", _row_values(product_rows, ["product_status", "status", "summary", "evidence"]), "제품/기술 상태 finding이 있습니다.")
    _confirm_slot(slots, "team_funding", _row_values(funding_rows, ["team", "funding", "funding_sources", "investors", "backers"]), "팀/투자자/펀딩 finding이 있습니다.")
    _confirm_slot(slots, "kol_social_mentions", _social_urls(social_rows, social_seed_rows), "KOL/social mention finding이 있습니다.")
    _confirm_slot(slots, "token_value_capture", _row_values(token_rows, ["token_value_capture", "token_utility", "value_capture", "tokenomics"]), "토큰 utility/value-capture finding이 있습니다.")

    risk_values = _row_values(findings, ["risk", "risks", "unclear_points", "red_flags"])
    _confirm_slot(slots, "risks", risk_values, "리스크/불명확 지점 finding이 있습니다.")

    missing = [slot.label_ko for slot in slots.values() if slot.status == "unverified" and slot.key != "unanswered_questions"]
    if missing:
        slots["unanswered_questions"].status = "partial"
        slots["unanswered_questions"].notes.append("미확인 evidence slots: " + ", ".join(missing[:8]))
    else:
        slots["unanswered_questions"].status = "confirmed"
        slots["unanswered_questions"].notes.append("필수 evidence slot이 모두 최소 partial 이상입니다.")
    return evidence_slots_to_dict(slots)


def _confirm_slot(slots: dict[str, Any], key: str, values: list[Any], note: str) -> None:
    cleaned = [str(value).strip() for value in values if str(value or "").strip() and str(value).strip().lower() not in {"none", "unknown", "n/a"}]
    if not cleaned:
        slots[key].notes.append("미확인")
        return
    urls = [value for value in cleaned if value.startswith(("http://", "https://"))]
    slots[key].status = "confirmed" if urls else "partial"
    slots[key].source_urls = list(dict.fromkeys(urls))[:8]
    slots[key].notes.append(note)


def _urls_like(urls: list[str], kind: str) -> list[str]:
    out: list[str] = []
    for url in urls:
        lower = url.lower()
        if kind == "x" and ("x.com/" in lower or "twitter.com/" in lower):
            out.append(url)
        elif kind == "github" and "github.com/" in lower:
            out.append(url)
        elif kind == "docs" and ("docs." in lower or "/docs" in lower or "gitbook.io" in lower):
            out.append(url)
        elif kind == "explorer" and any(host in lower for host in ["etherscan.io", "basescan.org", "arbiscan.io", "polygonscan.com", "bscscan.com", "solscan.io"]):
            out.append(url)
        elif kind == "official" and not any(marker in lower for marker in ["x.com/", "twitter.com/", "github.com/", "docs.", "gitbook.io", "etherscan.io", "basescan.org"]):
            out.append(url)
    return list(dict.fromkeys(out))


def _row_values(rows: list[Any], keys: list[str]) -> list[Any]:
    values: list[Any] = []
    for row in rows:
        data = row.data if isinstance(row, FindingRecord) else row
        if not isinstance(data, dict):
            continue
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                values.extend(value)
            elif isinstance(value, dict):
                values.extend(item for item in value.values() if item)
            elif value:
                values.append(value)
    return values


def _social_urls(*row_groups: list[dict[str, Any]]) -> list[str]:
    urls: list[str] = []
    for rows in row_groups:
        for row in rows:
            if not isinstance(row, dict):
                continue
            for value in row.values():
                if isinstance(value, str) and value.startswith(("http://", "https://")):
                    urls.append(value)
                elif isinstance(value, list):
                    urls.extend(str(item.get("url")) for item in value if isinstance(item, dict) and item.get("url"))
    return list(dict.fromkeys(urls))

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


def source_label(url: object) -> str:
    value = str(url)
    cleaned = value.removeprefix("https://").removeprefix("http://").strip("/")
    return cleaned[:80] or value


def source_markdown_link(url: object, label: object | None = None) -> str:
    value = str(url or "").strip()
    if not value:
        return "[source unavailable](#)"
    display = str(label or "").strip() or compact_source_label(value)
    display = compact_source_label(display)
    return f"[{display}]({value})"


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
