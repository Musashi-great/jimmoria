from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jimmoria.core.agent_spec import _load_yaml_like
from jimmoria.core.workflow import WorkflowSpec
from jimmoria.storage.paths import resolve_project_path


class WorkflowSpecRegistry:
    def __init__(self, specs: dict[str, WorkflowSpec] | None = None) -> None:
        self.specs = specs or {}

    def get(self, workflow_id: str) -> WorkflowSpec | None:
        return self.specs.get(workflow_id)

    @classmethod
    def load_dir(cls, directory: str | Path = "config/workflows") -> "WorkflowSpecRegistry":
        root = resolve_project_path(directory)
        specs: dict[str, WorkflowSpec] = {}
        if not root.exists():
            return cls(specs)
        for path in sorted(root.glob("*.yaml")):
            spec = load_workflow_file(path)
            specs[spec.workflow_id] = spec
        return cls(specs)


def load_workflow_file(path: str | Path) -> WorkflowSpec:
    data = _load_yaml_like(Path(path))
    spec = WorkflowSpec.from_dict(data)
    spec.validate()
    return spec


def load_workflow_spec(workflow_id: str, directory: str | Path = "config/workflows") -> WorkflowSpec:
    registry = WorkflowSpecRegistry.load_dir(directory)
    spec = registry.get(workflow_id)
    if spec is None:
        available = ", ".join(sorted(registry.specs)) or "none"
        raise RuntimeError(f"Workflow spec not found: {workflow_id}. Available: {available}")
    return spec


def workflow_spec_to_json(spec: WorkflowSpec) -> str:
    return json.dumps(spec.to_dict(), ensure_ascii=False, indent=2)


def workflow_summary(spec: WorkflowSpec) -> dict[str, Any]:
    return {
        "workflow_id": spec.workflow_id,
        "description": spec.description,
        "nodes": len(spec.nodes),
        "edges": len(spec.edges),
        "dynamic_edges": len([edge for edge in spec.edges if edge.dynamic]),
        "loop_counters": len([node for node in spec.nodes if node.node_type == "loop_counter"]),
    }
