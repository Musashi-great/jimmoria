from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ThesisCard:
    thesis_id: str
    project: str
    signal_date: str = ""
    source_layer: list[str] = field(default_factory=list)
    primary_narrative: str = ""
    claimed_catalyst: str = ""
    identity_status: str = "unknown"
    token_status: str = "unknown"
    evidence_strength: float = 0.0
    stance: str = "WATCH"
    what_must_be_true: list[str] = field(default_factory=list)
    counter_thesis: list[str] = field(default_factory=list)
    similar_past_theses: list[dict[str, Any]] = field(default_factory=list)
    next_check_date: str = ""
    outcome_labels: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ThesisCard":
        return cls(
            thesis_id=str(data.get("thesis_id") or "").strip(),
            project=str(data.get("project") or "").strip(),
            signal_date=str(data.get("signal_date") or ""),
            source_layer=_string_list(data.get("source_layer")),
            primary_narrative=str(data.get("primary_narrative") or ""),
            claimed_catalyst=str(data.get("claimed_catalyst") or ""),
            identity_status=str(data.get("identity_status") or "unknown"),
            token_status=str(data.get("token_status") or "unknown"),
            evidence_strength=_float(data.get("evidence_strength")),
            stance=str(data.get("stance") or "WATCH"),
            what_must_be_true=_string_list(data.get("what_must_be_true")),
            counter_thesis=_string_list(data.get("counter_thesis")),
            similar_past_theses=_dict_list(data.get("similar_past_theses")),
            next_check_date=str(data.get("next_check_date") or ""),
            outcome_labels=_string_list(data.get("outcome_labels")),
            source_ids=_string_list(data.get("source_ids")),
        )

    def validate(self) -> None:
        missing = [field_name for field_name in ("thesis_id", "project", "source_ids") if not getattr(self, field_name)]
        if missing:
            raise ValueError(f"Thesis card missing required field(s): {', '.join(missing)}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "thesis_id": self.thesis_id,
            "project": self.project,
            "signal_date": self.signal_date,
            "source_layer": self.source_layer,
            "primary_narrative": self.primary_narrative,
            "claimed_catalyst": self.claimed_catalyst,
            "identity_status": self.identity_status,
            "token_status": self.token_status,
            "evidence_strength": self.evidence_strength,
            "stance": self.stance,
            "what_must_be_true": self.what_must_be_true,
            "counter_thesis": self.counter_thesis,
            "similar_past_theses": self.similar_past_theses,
            "next_check_date": self.next_check_date,
            "outcome_labels": self.outcome_labels,
            "source_ids": self.source_ids,
        }

    def search_text(self) -> str:
        parts = [
            self.thesis_id,
            self.project,
            self.primary_narrative,
            self.claimed_catalyst,
            self.identity_status,
            self.token_status,
            self.stance,
            " ".join(self.source_layer),
            " ".join(self.what_must_be_true),
            " ".join(self.counter_thesis),
            " ".join(self.outcome_labels),
            " ".join(self.source_ids),
        ]
        return " ".join(parts).lower()


@dataclass(slots=True)
class OutcomeLabel:
    thesis_id: str
    review_date: str
    review_window_days: int
    labels: list[str] = field(default_factory=list)
    evidence_delta: str = ""
    next_check_date: str | None = None
    source_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OutcomeLabel":
        return cls(
            thesis_id=str(data.get("thesis_id") or "").strip(),
            review_date=str(data.get("review_date") or "").strip(),
            review_window_days=int(data.get("review_window_days") or 0),
            labels=_string_list(data.get("labels")),
            evidence_delta=str(data.get("evidence_delta") or ""),
            next_check_date=_optional_string(data.get("next_check_date")),
            source_ids=_string_list(data.get("source_ids")),
        )

    def validate(self) -> None:
        missing = [
            field_name
            for field_name in ("thesis_id", "review_date", "review_window_days", "labels", "source_ids")
            if not getattr(self, field_name)
        ]
        if missing:
            raise ValueError(f"Outcome label missing required field(s): {', '.join(missing)}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "thesis_id": self.thesis_id,
            "review_date": self.review_date,
            "review_window_days": self.review_window_days,
            "labels": self.labels,
            "evidence_delta": self.evidence_delta,
            "next_check_date": self.next_check_date,
            "source_ids": self.source_ids,
        }


class ThesisMemoryStore:
    def __init__(
        self,
        path: str | Path,
        *,
        cards: list[ThesisCard] | None = None,
        outcomes: list[OutcomeLabel] | None = None,
    ) -> None:
        self.path = Path(path)
        self.cards = cards or []
        self.outcomes = outcomes or []

    @classmethod
    def load(cls, path: str | Path) -> "ThesisMemoryStore":
        target = Path(path)
        if not target.exists():
            return cls(target)
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid thesis memory JSON: {target}") from exc
        if isinstance(data, list):
            data = {"thesis_cards": data, "outcome_labels": []}
        if not isinstance(data, dict):
            raise ValueError(f"Thesis memory must be a JSON object: {target}")
        cards = [ThesisCard.from_dict(item) for item in _dict_list(data.get("thesis_cards"))]
        outcomes = [OutcomeLabel.from_dict(item) for item in _dict_list(data.get("outcome_labels"))]
        return cls(target, cards=cards, outcomes=outcomes)

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return self.path

    def to_dict(self) -> dict[str, Any]:
        return {
            "thesis_cards": [card.to_dict() for card in self.cards],
            "outcome_labels": [outcome.to_dict() for outcome in self.outcomes],
        }

    def upsert_card(self, card: ThesisCard) -> str:
        card.validate()
        for index, existing in enumerate(self.cards):
            if existing.thesis_id == card.thesis_id:
                self.cards[index] = card
                return "updated"
        self.cards.insert(0, card)
        return "created"

    def add_outcome(self, outcome: OutcomeLabel) -> None:
        outcome.validate()
        self.outcomes.insert(0, outcome)
        card = self.get(outcome.thesis_id)
        if card is None:
            return
        card.outcome_labels = _unique([*card.outcome_labels, *outcome.labels])
        if outcome.next_check_date:
            card.next_check_date = outcome.next_check_date

    def get(self, thesis_id: str) -> ThesisCard | None:
        for card in self.cards:
            if card.thesis_id == thesis_id:
                return card
        return None

    def search(self, query: str, *, limit: int = 10) -> list[ThesisCard]:
        terms = [term for term in query.lower().split() if term]
        if not terms:
            return self.cards[:limit]
        scored: list[tuple[int, ThesisCard]] = []
        for card in self.cards:
            haystack = card.search_text()
            score = sum(1 for term in terms if term in haystack)
            if card.project.lower() == query.lower() or card.thesis_id.lower() == query.lower():
                score += 5
            if score:
                scored.append((score, card))
        scored.sort(key=lambda item: (item[0], item[1].signal_date), reverse=True)
        return [card for _, card in scored[:limit]]

    def review(self, query: str) -> tuple[ThesisCard, list[OutcomeLabel]] | None:
        card = self.get(query)
        if card is None:
            matches = self.search(query, limit=1)
            card = matches[0] if matches else None
        if card is None:
            return None
        return card, self.outcomes_for(card.thesis_id)

    def outcomes_for(self, thesis_id: str) -> list[OutcomeLabel]:
        return [outcome for outcome in self.outcomes if outcome.thesis_id == thesis_id]

    def due_cards(self, *, today: str | date | None = None) -> list[ThesisCard]:
        target = _coerce_date(today) if today is not None else date.today()
        due: list[ThesisCard] = []
        for card in self.cards:
            next_check = _parse_date(card.next_check_date)
            if next_check is not None and next_check <= target:
                due.append(card)
        return due


def card_summary_line(card: ThesisCard) -> str:
    return (
        f"{card.thesis_id} | {card.project} | {card.stance} | "
        f"identity={card.identity_status} | token={card.token_status} | "
        f"evidence={card.evidence_strength:.2f} | next={card.next_check_date or 'none'}"
    )


def render_card_review(card: ThesisCard, outcomes: list[OutcomeLabel]) -> str:
    lines = [
        card_summary_line(card),
        f"narrative: {card.primary_narrative or 'unknown'}",
        f"catalyst: {card.claimed_catalyst or 'unknown'}",
        "what_must_be_true:",
        *_bullet_lines(card.what_must_be_true),
        "counter_thesis:",
        *_bullet_lines(card.counter_thesis),
        "source_ids: " + ", ".join(card.source_ids),
    ]
    if outcomes:
        lines.append("outcomes:")
        for outcome in outcomes:
            lines.append(
                f"- {outcome.review_date} ({outcome.review_window_days}d): "
                f"{', '.join(outcome.labels)} | next={outcome.next_check_date or 'none'}"
            )
    else:
        lines.append("outcomes: none")
    return "\n".join(lines)


def _bullet_lines(items: list[str]) -> list[str]:
    if not items:
        return ["- unknown"]
    return [f"- {item}" for item in items]


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str):
        return [value] if value else []
    return [str(value)] if str(value) else []


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _coerce_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    parsed = _parse_date(value)
    if parsed is None:
        raise ValueError(f"Invalid date: {value}")
    return parsed
