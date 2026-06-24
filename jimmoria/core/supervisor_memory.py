from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from jimmoria.core.personal_agent_settings import PersonalAgentSettings
from jimmoria.core.time import utc_now


def supervisor_memory_path_for(memory_path: str | Path | None = None) -> Path:
    env_path = os.getenv("JIMMORIA_SUPERVISOR_MEMORY_PATH")
    if env_path:
        return Path(env_path)
    if memory_path is not None:
        return Path(memory_path).parent / "supervisor_memory.json"
    return Path("data/supervisor_memory.json")


@dataclass(slots=True)
class SupervisorMemoryItem:
    key: str
    value: str
    category: str = "preference"
    source: str = "user"
    confidence: float = 0.7
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SupervisorMemoryItem":
        known = {field_name for field_name in cls.__dataclass_fields__}
        filtered = {key: value for key, value in data.items() if key in known}
        return cls(**filtered)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SupervisorMemoryStore:
    """Persistent Hermes-style memory; class name stays for compatibility."""

    def __init__(self, path: str | Path | None = None, *, items: dict[str, SupervisorMemoryItem] | None = None) -> None:
        self.path = Path(path) if path is not None else supervisor_memory_path_for()
        self.items = items or {}

    @classmethod
    def load(cls, path: str | Path | None = None) -> "SupervisorMemoryStore":
        memory_path = Path(path) if path is not None else supervisor_memory_path_for()
        if not memory_path.exists():
            return cls(memory_path)
        try:
            data = json.loads(memory_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls(memory_path)
        raw_items = data.get("items", {}) if isinstance(data, dict) else {}
        items: dict[str, SupervisorMemoryItem] = {}
        if isinstance(raw_items, dict):
            for key, value in raw_items.items():
                if isinstance(value, dict):
                    item = SupervisorMemoryItem.from_dict(value)
                    items[str(key)] = item
        return cls(memory_path, items=items)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "jimmoria.supervisor_memory.v1",
            "updated_at": utc_now(),
            "items": {key: item.to_dict() for key, item in sorted(self.items.items())},
        }

    def remember(
        self,
        *,
        key: str,
        value: str,
        category: str = "preference",
        source: str = "user",
        confidence: float = 0.7,
        metadata: dict[str, Any] | None = None,
    ) -> SupervisorMemoryItem:
        normalized_key = normalize_memory_key(key)
        now = utc_now()
        existing = self.items.get(normalized_key)
        if existing is None:
            item = SupervisorMemoryItem(
                key=normalized_key,
                value=value.strip(),
                category=category,
                source=source,
                confidence=confidence,
                created_at=now,
                updated_at=now,
                metadata=metadata or {},
            )
            self.items[normalized_key] = item
            return item
        existing.value = value.strip()
        existing.category = category or existing.category
        existing.source = source or existing.source
        existing.confidence = max(existing.confidence, confidence)
        existing.updated_at = now
        if metadata:
            existing.metadata.update(metadata)
        return existing

    def observe_user_message(self, line: str, settings: PersonalAgentSettings) -> list[SupervisorMemoryItem]:
        """Extract durable Hermes Agent preferences from a user message."""

        text = line.strip()
        lowered = text.lower()
        captured: list[SupervisorMemoryItem] = []

        if not text:
            return captured

        if "hermes" in lowered or "슈퍼바이저" in text or "supervisor" in lowered:
            captured.append(
                self.remember(
                    key="supervisor_operating_model",
                    value=(
                        "Hermes Agent should behave like a Hermes-style personal-agent harness: "
                        "answer normal chat directly, remember preferences, and delegate real work to specialist subroutines."
                    ),
                    category="operating_principle",
                    source="user",
                    confidence=0.9,
                    metadata={"trigger": text[:240]},
                )
            )

        if "메모리" in text or "memory" in lowered or "기억" in text:
            captured.append(
                self.remember(
                    key="supervisor_memory_expected",
                    value="Hermes Agent is expected to keep persistent memory across CLI sessions.",
                    category="preference",
                    source="user",
                    confidence=0.9,
                    metadata={"trigger": text[:240]},
                )
            )

        if "하위 에이전트" in text or "sub-agent" in lowered or "delegate" in lowered:
            captured.append(
                self.remember(
                    key="delegate_work_to_specialists",
                    value="When the user gives executable work, Hermes Agent should plan and assign specialist agents instead of only chatting.",
                    category="operating_principle",
                    source="user",
                    confidence=0.9,
                    metadata={"trigger": text[:240]},
                )
            )

        if "보고서" in text or "report" in lowered:
            captured.append(
                self.remember(
                    key="report_language_preference",
                    value=f"Client-facing reports should default to {settings.report_language}; English technical terms allowed={settings.allow_english_terms}.",
                    category="report_preference",
                    source="settings",
                    confidence=0.8,
                )
            )

        if "ui" in lowered or "테두리" in text or "대시" in text or "dashboard" in lowered:
            captured.append(
                self.remember(
                    key="runtime_ui_preference",
                    value="Runtime UI should show stable agent dashboards with concise visible status, while raw logs stay in background artifacts.",
                    category="ui_preference",
                    source="user",
                    confidence=0.8,
                    metadata={"trigger": text[:240]},
                )
            )

        return captured

    def search(self, query: str, *, limit: int = 8) -> list[SupervisorMemoryItem]:
        tokens = _tokens(query)
        scored: list[tuple[int, str, SupervisorMemoryItem]] = []
        for key, item in self.items.items():
            haystack = f"{item.key} {item.value} {item.category}".lower()
            score = sum(1 for token in tokens if token in haystack)
            if score:
                scored.append((score, item.updated_at, item))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        if scored:
            return [item for _, _, item in scored[:limit]]
        return self.recent(limit=limit)

    def recent(self, *, limit: int = 8) -> list[SupervisorMemoryItem]:
        items = sorted(self.items.values(), key=lambda item: item.updated_at, reverse=True)
        return items[:limit]

    def prompt_lines(self, query: str = "", *, limit: int = 8) -> list[str]:
        items = self.search(query, limit=limit) if query else self.recent(limit=limit)
        return [f"[{item.category}] {item.key}: {item.value}" for item in items]


def normalize_memory_key(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    return "".join(char for char in normalized if char.isalnum() or char == "_") or "memory"


def _tokens(text: str) -> set[str]:
    return {part.strip(".,:;!?()[]{}").lower() for part in text.split() if len(part.strip()) > 1}
