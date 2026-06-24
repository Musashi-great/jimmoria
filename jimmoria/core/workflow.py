from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class WorkflowNode:
    node_id: str
    node_type: str
    agent_ref: str = ""
    workflow_ref: str = ""
    context_window: int = 0
    config: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowNode":
        return cls(
            node_id=str(data["id"]),
            node_type=str(data.get("type", "agent")),
            agent_ref=str(data.get("agent_ref", "")),
            workflow_ref=str(data.get("workflow_ref", "")),
            context_window=int(data.get("context_window", 0) or 0),
            config=dict(data.get("config", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.node_id,
            "type": self.node_type,
        }
        if self.agent_ref:
            data["agent_ref"] = self.agent_ref
        if self.workflow_ref:
            data["workflow_ref"] = self.workflow_ref
        if self.context_window:
            data["context_window"] = self.context_window
        if self.config:
            data["config"] = self.config
        return data


@dataclass(slots=True)
class WorkflowEdge:
    from_node: str
    to_node: str
    trigger: bool = True
    condition: bool | str | dict[str, Any] = True
    dynamic: dict[str, Any] = field(default_factory=dict)
    carry_data: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowEdge":
        return cls(
            from_node=str(data["from"]),
            to_node=str(data["to"]),
            trigger=bool(data.get("trigger", True)),
            condition=data.get("condition", True),
            dynamic=dict(data.get("dynamic", {})),
            carry_data=bool(data.get("carry_data", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "from": self.from_node,
            "to": self.to_node,
            "trigger": self.trigger,
            "condition": self.condition,
            "carry_data": self.carry_data,
        }
        if self.dynamic:
            data["dynamic"] = self.dynamic
        return data


@dataclass(slots=True)
class LoopCounter:
    counter_id: str
    max_iterations: int
    reset_on_emit: bool = False
    current_iteration: int = 0

    @classmethod
    def from_node(cls, node: WorkflowNode) -> "LoopCounter":
        return cls(
            counter_id=node.node_id,
            max_iterations=int(node.config.get("max_iterations", 1) or 1),
            reset_on_emit=bool(node.config.get("reset_on_emit", False)),
        )

    def can_continue(self) -> bool:
        return self.current_iteration < self.max_iterations

    def tick(self) -> bool:
        if not self.can_continue():
            return False
        self.current_iteration += 1
        return True

    def reset(self) -> None:
        self.current_iteration = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.counter_id,
            "max_iterations": self.max_iterations,
            "reset_on_emit": self.reset_on_emit,
            "current_iteration": self.current_iteration,
        }


@dataclass(slots=True)
class WorkflowSpec:
    workflow_id: str
    description: str = ""
    nodes: list[WorkflowNode] = field(default_factory=list)
    edges: list[WorkflowEdge] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowSpec":
        body = data.get("workflow", data)
        if not isinstance(body, dict):
            raise ValueError("Workflow file must contain a mapping.")
        return cls(
            workflow_id=str(body["id"]),
            description=str(body.get("description", "")),
            nodes=[WorkflowNode.from_dict(item) for item in body.get("nodes", [])],
            edges=[WorkflowEdge.from_dict(item) for item in body.get("edges", [])],
            metadata=dict(body.get("metadata", {})),
        )

    @property
    def node_ids(self) -> set[str]:
        return {node.node_id for node in self.nodes}

    @property
    def loop_counters(self) -> dict[str, LoopCounter]:
        return {
            node.node_id: LoopCounter.from_node(node)
            for node in self.nodes
            if node.node_type == "loop_counter"
        }

    def validate(self) -> None:
        if not self.nodes:
            raise ValueError(f"Workflow {self.workflow_id} has no nodes.")
        node_ids = self.node_ids
        for edge in self.edges:
            if edge.from_node not in node_ids and edge.from_node not in {"START", "END"}:
                raise ValueError(f"Workflow {self.workflow_id} edge references unknown source node: {edge.from_node}")
            if edge.to_node not in node_ids and edge.to_node not in {"START", "END"}:
                raise ValueError(f"Workflow {self.workflow_id} edge references unknown target node: {edge.to_node}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow": {
                "id": self.workflow_id,
                "description": self.description,
                "metadata": self.metadata,
                "nodes": [node.to_dict() for node in self.nodes],
                "edges": [edge.to_dict() for edge in self.edges],
            }
        }
