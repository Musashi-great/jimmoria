from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping


PROJECT_DOSSIER_EVIDENCE_SLOTS: tuple[dict[str, str], ...] = (
    {"key": "official_website", "label_ko": "공식 웹사이트", "description": "Project-controlled homepage or canonical domain."},
    {"key": "official_x", "label_ko": "공식 X", "description": "Official Twitter/X account or announcement channel."},
    {"key": "docs", "label_ko": "문서/docs", "description": "Product, protocol, API, or token documentation."},
    {"key": "github", "label_ko": "GitHub", "description": "Official repository or organization with project code/activity."},
    {"key": "chain", "label_ko": "체인", "description": "Primary chain(s) where the token, contract, or product runs."},
    {"key": "contract", "label_ko": "컨트랙트", "description": "Official token or protocol contract address."},
    {"key": "dex_pair", "label_ko": "DEX 페어", "description": "DEX pair/pool used to verify live market identity."},
    {"key": "coingecko_cmc", "label_ko": "CoinGecko/CMC", "description": "CoinGecko or CoinMarketCap metadata existence."},
    {"key": "explorer_verification", "label_ko": "Explorer 검증", "description": "Block explorer source/metadata verification status."},
    {"key": "product_status", "label_ko": "제품 상태", "description": "What is live, demo-only, testnet-only, or still vapor."},
    {"key": "team_funding", "label_ko": "팀/투자자", "description": "Team, backers, grants, funding, or partner evidence."},
    {"key": "kol_social_mentions", "label_ko": "KOL/소셜", "description": "Notable social/KOL mentions and community signal."},
    {"key": "token_value_capture", "label_ko": "토큰/가치포착", "description": "Token utility, value-capture logic, unlocks, and supply context."},
    {"key": "risks", "label_ko": "리스크", "description": "Material risks, contradictions, missing controls, or fraud signals."},
    {"key": "unanswered_questions", "label_ko": "미확인 질문", "description": "Open questions that remain unverified after the dossier pass."},
)


@dataclass(slots=True)
class IdentityCandidate:
    """A source-backed candidate identity before Identity Gate confirmation."""

    label: str
    source_type: str
    value: str
    confidence: float = 0.5
    evidence_urls: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EvidenceSlot:
    """Required project-dossier evidence slot tracked as verified/partial/unverified."""

    key: str
    label_ko: str
    description: str
    status: str = "unverified"
    source_urls: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def empty_evidence_slots() -> dict[str, EvidenceSlot]:
    return {
        item["key"]: EvidenceSlot(
            key=item["key"],
            label_ko=item["label_ko"],
            description=item["description"],
        )
        for item in PROJECT_DOSSIER_EVIDENCE_SLOTS
    }


def evidence_slots_to_dict(slots: Mapping[str, EvidenceSlot]) -> dict[str, dict[str, Any]]:
    return {key: slot.to_dict() for key, slot in slots.items()}


def first_present(values: Iterable[Any]) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""
