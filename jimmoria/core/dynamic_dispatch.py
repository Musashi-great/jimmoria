from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4


@dataclass(slots=True)
class CandidateTask:
    candidate: dict[str, Any]
    index: int
    task_id: str = field(default_factory=lambda: f"candidate_task_{uuid4().hex[:10]}")

    @property
    def display_name(self) -> str:
        return str(self.candidate.get("project") or self.candidate.get("name") or f"candidate_{self.index}")


@dataclass(slots=True)
class CandidateResult:
    task_id: str
    candidate: dict[str, Any]
    status: str
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    risk_finding: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "candidate": self.candidate,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "risk_finding": self.risk_finding,
        }


class DynamicCandidateDispatcher:
    def __init__(self, *, max_parallel: int = 5) -> None:
        self.max_parallel = max(1, max_parallel)

    def create_tasks(self, candidates: list[dict[str, Any]]) -> list[CandidateTask]:
        return [CandidateTask(candidate=candidate, index=index) for index, candidate in enumerate(candidates, start=1)]

    def dispatch(
        self,
        candidates: list[dict[str, Any]],
        handler: Callable[[CandidateTask], dict[str, Any]] | None = None,
    ) -> list[CandidateResult]:
        results: list[CandidateResult] = []
        for task in self.create_tasks(candidates):
            try:
                result = handler(task) if handler else {"candidate": task.candidate, "status": "planned"}
            except Exception as exc:
                results.append(
                    CandidateResult(
                        task_id=task.task_id,
                        candidate=task.candidate,
                        status="failed",
                        error=str(exc),
                        risk_finding={
                            "type": "candidate_task_failure",
                            "severity": "medium",
                            "message": f"Candidate diligence failed for {task.display_name}: {exc}",
                        },
                    )
                )
                continue
            results.append(
                CandidateResult(
                    task_id=task.task_id,
                    candidate=task.candidate,
                    status=str(result.get("status", "completed")),
                    result=result,
                )
            )
        return results


def candidates_from_context(context: dict[str, Any], input_key: str = "candidates") -> list[dict[str, Any]]:
    raw = context.get(input_key, [])
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]
