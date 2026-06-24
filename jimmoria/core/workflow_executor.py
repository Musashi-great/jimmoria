from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4

from jimmoria.core.dynamic_dispatch import (
    CandidateResult,
    CandidateTask,
    DynamicCandidateDispatcher,
    candidates_from_context,
)
from jimmoria.core.edge_conditions import evaluate_edge_condition
from jimmoria.core.workflow import WorkflowEdge, WorkflowSpec


@dataclass(slots=True)
class WorkflowExecutionResult:
    run_id: str
    workflow_id: str
    status: str
    trace: list[dict[str, Any]] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    candidate_results: list[CandidateResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "status": self.status,
            "trace": self.trace,
            "data": self.data,
            "candidate_results": [result.to_dict() for result in self.candidate_results],
        }


class WorkflowExecutor:
    """Lightweight workflow graph runner for trace, conditions, and dynamic maps.

    The existing ResearchRuntime still owns real agent execution. This executor
    makes the specialist workflow explicit and replayable without replacing the
    current room runtime.
    """

    def execute(
        self,
        workflow: WorkflowSpec,
        context: dict[str, Any] | None = None,
        *,
        candidate_handler: Callable[[CandidateTask], dict[str, Any]] | None = None,
    ) -> WorkflowExecutionResult:
        workflow.validate()
        data = dict(context or {})
        run_id = str(data.get("run_id") or f"workflow_{uuid4().hex[:10]}")
        loop_counters = workflow.loop_counters
        trace: list[dict[str, Any]] = []
        candidate_results: list[CandidateResult] = []

        for node in workflow.nodes:
            if node.node_type == "loop_counter":
                counter = loop_counters[node.node_id]
                ticked = counter.tick()
                trace.append(
                    {
                        "type": "loop_counter",
                        "node_id": node.node_id,
                        "status": "continued" if ticked else "max_reached",
                        "counter": counter.to_dict(),
                    }
                )
                if not ticked:
                    data.setdefault("risk_flags", []).append(
                        {
                            "type": "loop_max_reached",
                            "severity": "low",
                            "message": f"{node.node_id} reached max iterations; unresolved issues should be marked as Thin Signal.",
                        }
                    )
                continue

            trace.append(
                {
                    "type": "node",
                    "node_id": node.node_id,
                    "node_type": node.node_type,
                    "agent_ref": node.agent_ref,
                    "workflow_ref": node.workflow_ref,
                    "status": "planned",
                }
            )

            for edge in outgoing_edges(workflow, node.node_id):
                condition_passed = evaluate_edge_condition(edge.condition, data, loop_counters=loop_counters)
                trace.append(edge_trace(edge, condition_passed))
                if not condition_passed or not edge.trigger:
                    continue
                if edge.dynamic.get("type") == "map":
                    input_key = str(edge.dynamic.get("input_key") or "candidates")
                    dispatcher = DynamicCandidateDispatcher(max_parallel=int(edge.dynamic.get("max_parallel", 5) or 5))
                    results = dispatcher.dispatch(candidates_from_context(data, input_key), handler=candidate_handler)
                    candidate_results.extend(results)
                    data["candidate_results"] = [result.to_dict() for result in candidate_results]
                    failed = [result.risk_finding for result in results if result.risk_finding]
                    if failed:
                        data.setdefault("risk_flags", []).extend(failed)

        return WorkflowExecutionResult(
            run_id=run_id,
            workflow_id=workflow.workflow_id,
            status="completed",
            trace=trace,
            data=data,
            candidate_results=candidate_results,
        )


def outgoing_edges(workflow: WorkflowSpec, node_id: str) -> list[WorkflowEdge]:
    return [edge for edge in workflow.edges if edge.from_node == node_id]


def edge_trace(edge: WorkflowEdge, condition_passed: bool) -> dict[str, Any]:
    return {
        "type": "edge",
        "from": edge.from_node,
        "to": edge.to_node,
        "trigger": edge.trigger,
        "condition": edge.condition,
        "condition_passed": condition_passed,
        "dynamic": edge.dynamic,
    }
