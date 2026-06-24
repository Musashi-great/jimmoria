from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from urllib.parse import urlparse

from jimmoria.core.project_identity import IdentityCandidate

URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"']+", re.IGNORECASE)
CONTRACT_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
TICKER_RE = re.compile(r"(?<![\w/])\$([A-Za-z][A-Za-z0-9_]{1,15})\b")
X_HANDLE_RE = re.compile(r"(?<![\w/])@([A-Za-z0-9_]{2,15})\b")
PROJECT_NAME_RE = re.compile(
    r"(?:^|\s)([A-Za-z0-9][A-Za-z0-9._-]{1,40})(?:\s+프로젝트|\s+project|\s+리서치|\s+보고서|\s+분석)",
    re.IGNORECASE,
)

ARTICLE_HOST_HINTS = (
    "medium.com",
    "mirror.xyz",
    "substack.com",
    "coindesk.com",
    "cointelegraph.com",
    "theblock.co",
    "decrypt.co",
)
EXPLORER_HOST_HINTS = (
    "etherscan.io",
    "basescan.org",
    "arbiscan.io",
    "optimistic.etherscan.io",
    "polygonscan.com",
    "bscscan.com",
    "snowtrace.io",
    "solscan.io",
)
DOCS_HOST_HINTS = ("docs.", "gitbook.io", "readme.io", "notion.site", "notion.so")


@dataclass(slots=True)
class ResolvedResearchInput:
    """Deterministic first-pass interpretation of user research input."""

    raw_input: str
    normalized_text: str
    input_types: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    primary_url: str = ""
    official_urls: list[str] = field(default_factory=list)
    x_urls: list[str] = field(default_factory=list)
    github_urls: list[str] = field(default_factory=list)
    article_urls: list[str] = field(default_factory=list)
    docs_urls: list[str] = field(default_factory=list)
    explorer_urls: list[str] = field(default_factory=list)
    contract_addresses: list[str] = field(default_factory=list)
    tickers: list[str] = field(default_factory=list)
    x_handles: list[str] = field(default_factory=list)
    project_names: list[str] = field(default_factory=list)
    natural_language_query: str = ""
    identity_candidates: list[IdentityCandidate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_required_evidence: list[str] = field(default_factory=list)
    required_link_present: bool = False
    needs_link_for_research: bool = False
    suggested_prompt: str = ""

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["identity_candidates"] = [candidate.to_dict() for candidate in self.identity_candidates]
        return data


def resolve_research_input(text: str) -> ResolvedResearchInput:
    raw = text or ""
    normalized = " ".join(raw.strip().split())
    urls = _unique(_clean_url(match.group(0)) for match in URL_RE.finditer(raw))
    contracts = _unique(match.group(0) for match in CONTRACT_RE.finditer(raw))
    tickers = _unique(match.group(1).upper() for match in TICKER_RE.finditer(raw))
    handles = _unique(match.group(1) for match in X_HANDLE_RE.finditer(raw))
    project_names = _unique(_clean_project_name(match.group(1)) for match in PROJECT_NAME_RE.finditer(raw))

    resolver = ResolvedResearchInput(
        raw_input=raw,
        normalized_text=normalized,
        urls=urls,
        primary_url=urls[0] if urls else "",
        contract_addresses=contracts,
        tickers=tickers,
        x_handles=handles,
        project_names=project_names,
        natural_language_query=_strip_structured_tokens(normalized, urls, contracts, tickers, handles),
    )
    _classify_urls(resolver)
    _classify_input_types(resolver)
    _build_identity_candidates(resolver)
    _build_warnings(resolver)
    _build_missing_evidence(resolver)
    resolver.required_link_present = bool(resolver.urls)
    resolver.needs_link_for_research = not resolver.required_link_present
    resolver.suggested_prompt = _suggested_prompt(resolver)
    return resolver


def has_required_research_link(text: str) -> bool:
    return resolve_research_input(text).required_link_present


def _classify_urls(resolver: ResolvedResearchInput) -> None:
    for url in resolver.urls:
        kind = classify_url(url)
        if kind == "x_account":
            resolver.x_urls.append(url)
        elif kind == "github_repo":
            resolver.github_urls.append(url)
        elif kind == "docs":
            resolver.docs_urls.append(url)
        elif kind == "explorer":
            resolver.explorer_urls.append(url)
        elif kind == "article":
            resolver.article_urls.append(url)
        else:
            resolver.official_urls.append(url)


def classify_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.strip("/")
    if host in {"x.com", "twitter.com", "mobile.twitter.com"}:
        return "x_account" if path and not path.startswith(("search", "hashtag", "i/", "home")) else "social_search"
    if host == "github.com" and len([part for part in path.split("/") if part]) >= 2:
        return "github_repo"
    if any(hint in host for hint in DOCS_HOST_HINTS) or "/docs" in parsed.path.lower():
        return "docs"
    if any(hint in host for hint in EXPLORER_HOST_HINTS):
        return "explorer"
    if any(host == hint or host.endswith(f".{hint}") for hint in ARTICLE_HOST_HINTS):
        return "article"
    return "official_website"


def _classify_input_types(resolver: ResolvedResearchInput) -> None:
    types: list[str] = []
    if resolver.project_names:
        types.append("project_name")
    if resolver.tickers:
        types.append("ticker")
    if resolver.contract_addresses:
        types.append("contract_address")
    if resolver.official_urls:
        types.append("official_website_url")
    if resolver.x_urls or resolver.x_handles:
        types.append("x_account")
    if resolver.github_urls:
        types.append("github_repo")
    if resolver.article_urls:
        types.append("article_url")
    if resolver.docs_urls:
        types.append("docs_url")
    if resolver.explorer_urls:
        types.append("explorer_url")
    if resolver.natural_language_query:
        types.append("natural_language_query")
    resolver.input_types = _unique(types)


def _build_identity_candidates(resolver: ResolvedResearchInput) -> None:
    candidates: list[IdentityCandidate] = []
    for url in resolver.official_urls:
        candidates.append(IdentityCandidate(label=_host_label(url), source_type="official_website", value=url, confidence=0.78, evidence_urls=[url]))
    for url in resolver.x_urls:
        candidates.append(IdentityCandidate(label=_x_label(url), source_type="official_x_candidate", value=url, confidence=0.72, evidence_urls=[url]))
    for url in resolver.github_urls:
        candidates.append(IdentityCandidate(label=_github_label(url), source_type="github_repo", value=url, confidence=0.64, evidence_urls=[url]))
    for contract in resolver.contract_addresses:
        candidates.append(IdentityCandidate(label=contract[:10] + "…", source_type="contract_address", value=contract, confidence=0.58, evidence_urls=list(resolver.explorer_urls)))
    for ticker in resolver.tickers:
        candidates.append(IdentityCandidate(label=f"${ticker}", source_type="ticker", value=ticker, confidence=0.35, evidence_urls=list(resolver.urls), warnings=["ticker_collision_check_required"]))
    for name in resolver.project_names:
        candidates.append(IdentityCandidate(label=name, source_type="project_name", value=name, confidence=0.42, evidence_urls=list(resolver.urls)))
    resolver.identity_candidates = candidates


def _build_warnings(resolver: ResolvedResearchInput) -> None:
    warnings: list[str] = []
    if not resolver.urls:
        warnings.append("source_link_required_for_research")
    if resolver.tickers and not (resolver.official_urls or resolver.x_urls or resolver.github_urls or resolver.contract_addresses):
        warnings.append("ticker_collision_risk")
    if resolver.contract_addresses and not resolver.explorer_urls:
        warnings.append("contract_explorer_verification_missing")
    if resolver.contract_addresses and resolver.official_urls and not resolver.explorer_urls:
        warnings.append("contract_source_match_unverified")
    if resolver.article_urls and not (resolver.official_urls or resolver.x_urls or resolver.github_urls):
        warnings.append("article_is_context_not_official_identity")
    resolver.warnings = _unique(warnings)


def _build_missing_evidence(resolver: ResolvedResearchInput) -> None:
    missing: list[str] = []
    if not resolver.official_urls:
        missing.append("official_website")
    if not resolver.x_urls:
        missing.append("official_x")
    if not resolver.docs_urls:
        missing.append("docs")
    if not resolver.github_urls:
        missing.append("github")
    if not resolver.contract_addresses:
        missing.append("contract")
    if not resolver.explorer_urls:
        missing.append("explorer_verification")
    resolver.missing_required_evidence = missing


def _suggested_prompt(resolver: ResolvedResearchInput) -> str:
    if resolver.required_link_present:
        return "이 링크를 기준 source로 삼고 공식 웹사이트/X/docs/GitHub/컨트랙트 일치 여부를 검증해줘."
    return "프로젝트 리서치를 시작하려면 공식 웹사이트, X, GitHub, 기사, 또는 explorer/DEX 링크 중 최소 1개를 함께 보내주세요."


def _clean_url(url: str) -> str:
    return url.rstrip(".,;)】]\n\t")


def _clean_project_name(name: str) -> str:
    return name.strip(" .,;:()[]{}'\"")


def _strip_structured_tokens(normalized: str, urls: list[str], contracts: list[str], tickers: list[str], handles: list[str]) -> str:
    text = normalized
    for token in [*urls, *contracts, *(f"${ticker}" for ticker in tickers), *(f"@{handle}" for handle in handles)]:
        text = text.replace(token, " ")
    return " ".join(text.split())


def _host_label(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.") or url


def _x_label(url: str) -> str:
    path = urlparse(url).path.strip("/")
    return f"@{path.split('/')[0]}" if path else _host_label(url)


def _github_label(url: str) -> str:
    parts = [part for part in urlparse(url).path.strip("/").split("/") if part]
    return "/".join(parts[:2]) if parts else _host_label(url)


def _unique(values) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not value:
            continue
        key = str(value).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(str(value))
    return out
