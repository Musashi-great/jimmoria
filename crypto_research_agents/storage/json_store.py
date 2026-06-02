from __future__ import annotations

import json
from pathlib import Path

from crypto_research_agents.core.memory import SharedMemory


def load_memory(path: str | Path) -> SharedMemory:
    memory_path = Path(path)
    if not memory_path.exists():
        return SharedMemory()
    return SharedMemory.from_dict(json.loads(memory_path.read_text(encoding="utf-8")))


def save_memory(memory: SharedMemory, path: str | Path) -> None:
    memory_path = Path(path)
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text(
        json.dumps(memory.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
