from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jimmoria.storage.paths import resolve_project_path


@dataclass(slots=True)
class HookManifest:
    hook_id: str
    description: str = ""
    events: list[str] = field(default_factory=list)
    blocking: bool = False
    priority: int = 100
    handler: str = ""
    source_path: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, source_path: str = "") -> "HookManifest":
        hook_id = str(data.get("hook_id") or data.get("name") or data.get("id") or "").strip()
        if not hook_id:
            raise ValueError(f"Hook manifest missing hook_id/name: {source_path}")
        return cls(
            hook_id=hook_id,
            description=str(data.get("description") or ""),
            events=_string_list(data.get("events")),
            blocking=bool(data.get("blocking", False)),
            priority=int(data.get("priority", 100)),
            handler=str(data.get("handler") or ""),
            source_path=source_path,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "hook_id": self.hook_id,
            "description": self.description,
            "events": self.events,
            "blocking": self.blocking,
            "priority": self.priority,
            "handler": self.handler,
            "source_path": self.source_path,
        }


class HookRegistry:
    def __init__(self, manifests: dict[str, HookManifest] | None = None) -> None:
        self.manifests = manifests or {}

    def get(self, hook_id: str) -> HookManifest | None:
        return self.manifests.get(_normalize_hook_id(hook_id))

    def hooks_for_event(self, event: str) -> list[HookManifest]:
        selected = [
            manifest
            for manifest in self.manifests.values()
            if event in manifest.events
        ]
        return sorted(selected, key=lambda item: (item.priority, item.hook_id))

    def hooks_for_phase(self, phase: str) -> list[HookManifest]:
        aliases = _phase_events(phase)
        selected: dict[str, HookManifest] = {}
        for event in aliases:
            for manifest in self.hooks_for_event(event):
                selected[manifest.hook_id] = manifest
        return sorted(selected.values(), key=lambda item: (item.priority, item.hook_id))

    def to_dict(self) -> dict[str, Any]:
        return {"hooks": {hook_id: hook.to_dict() for hook_id, hook in self.manifests.items()}}

    @classmethod
    def load_dir(cls, directory: str | Path = "config/hooks") -> "HookRegistry":
        root = resolve_project_path(directory)
        manifests: dict[str, HookManifest] = {}
        if not root.exists():
            return cls(manifests)
        for path in sorted(root.glob("*.yaml")):
            data = _load_yaml_like(path)
            for manifest in _manifests_from_file(data, path):
                manifests[_normalize_hook_id(manifest.hook_id)] = manifest
        for manifest_path in sorted(root.glob("*/HOOK.yaml")):
            data = _load_yaml_like(manifest_path)
            data.setdefault("handler", "handler.py")
            manifest = HookManifest.from_dict(data, source_path=str(manifest_path))
            manifests[_normalize_hook_id(manifest.hook_id)] = manifest
        return cls(manifests)


def runtime_event_to_hook_event(event_type: str) -> str:
    return {
        "room_created": "room:created",
        "parallel_group_start": "parallel:group_start",
        "parallel_group_done": "parallel:group_done",
        "parallel_group_failed": "parallel:group_failed",
        "agent_start": "agent:start",
        "agent_done": "agent:end",
        "agent_failed": "agent:failed",
        "tool_start": "tool:start",
        "tool_done": "tool:done",
        "tool_failed": "tool:failed",
        "tool_denied": "tool:failed",
        "tool_unconfigured": "tool:failed",
        "finding_saved": "finding:written",
        "source_saved": "source:written",
        "deliberation_start": "council:start",
        "deliberation_done": "council:end",
        "report_written": "report:after_render",
        "final_review_start": "supervisor:final_review",
        "note_written": "obsidian:after_sync",
        "room_completed": "room:completed",
    }.get(event_type, event_type.replace("_", ":"))


def _manifests_from_file(data: dict[str, Any], path: Path) -> list[HookManifest]:
    if isinstance(data.get("common_hooks"), dict):
        manifests: dict[str, HookManifest] = {}
        for phase, hook_ids in data["common_hooks"].items():
            for hook_id in _string_list(hook_ids):
                key = _normalize_hook_id(hook_id)
                manifest = manifests.get(key)
                if manifest is None:
                    manifest = HookManifest(
                        hook_id=hook_id,
                        description=f"Common {phase} hook.",
                        events=[],
                        priority=_phase_priority(phase),
                        source_path=str(path),
                    )
                    manifests[key] = manifest
                for event in _phase_events(phase):
                    if event not in manifest.events:
                        manifest.events.append(event)
        return list(manifests.values())
    if isinstance(data.get("hooks"), dict):
        manifests = []
        for hook_id, raw in data["hooks"].items():
            entry = dict(raw) if isinstance(raw, dict) else {"events": _string_list(raw)}
            entry.setdefault("hook_id", str(hook_id))
            manifests.append(HookManifest.from_dict(entry, source_path=str(path)))
        return manifests
    return [HookManifest.from_dict(data, source_path=str(path))]


def _phase_events(phase: str) -> list[str]:
    return {
        "before_run": ["before_run", "agent:start"],
        "before_tool_call": ["before_tool_call", "tool:start"],
        "after_tool_call": ["after_tool_call", "tool:done", "tool:failed"],
        "after_run": ["after_run", "agent:end"],
        "before_report": ["before_report", "report:before_render"],
        "after_report": ["after_report", "report:after_render"],
        "quality_gate": ["quality_gate"],
    }.get(phase, [phase])


def _phase_priority(phase: str) -> int:
    return {
        "before_run": 10,
        "before_tool_call": 20,
        "after_tool_call": 30,
        "quality_gate": 40,
        "before_report": 50,
        "after_report": 60,
        "after_run": 70,
    }.get(phase, 100)


def _normalize_hook_id(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, tuple):
        return [str(item) for item in value]
    if isinstance(value, str):
        return [value]
    return []


def _load_yaml_like(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            f"{path} is not JSON-compatible YAML and PyYAML is not installed."
        ) from exc
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise RuntimeError(f"{path} must contain a YAML mapping.")
    return loaded
