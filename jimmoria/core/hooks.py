from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

from jimmoria.core.hook_registry import HookRegistry


HookCallable = Callable[..., None]


class HookEngine:
    def __init__(self, registry: HookRegistry | None = None) -> None:
        self._hooks: dict[str, list[HookCallable]] = defaultdict(list)
        self.registry = registry or HookRegistry()
        self.events: list[dict[str, Any]] = []

    def register(self, hook_name: str, func: HookCallable) -> None:
        self._hooks[hook_name].append(func)

    def run(self, hook_name: str, **payload: Any) -> None:
        self.events.append({"hook": hook_name, "payload": payload})
        for func in self._hooks.get(hook_name, []):
            func(**payload)

    def emit(self, event_type: str, **payload: Any) -> None:
        self.events.append({"event": event_type, "payload": payload})
        for manifest in self.registry.hooks_for_event(event_type):
            self.run(
                manifest.hook_id,
                hook_event=event_type,
                hook_manifest=manifest.to_dict(),
                **payload,
            )
