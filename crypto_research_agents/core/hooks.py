from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any


HookCallable = Callable[..., None]


class HookEngine:
    def __init__(self) -> None:
        self._hooks: dict[str, list[HookCallable]] = defaultdict(list)
        self.events: list[dict[str, Any]] = []

    def register(self, hook_name: str, func: HookCallable) -> None:
        self._hooks[hook_name].append(func)

    def run(self, hook_name: str, **payload: Any) -> None:
        self.events.append({"hook": hook_name, "payload": payload})
        for func in self._hooks.get(hook_name, []):
            func(**payload)
