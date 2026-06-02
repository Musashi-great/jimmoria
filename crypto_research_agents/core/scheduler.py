from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crypto_research_agents.core.agent_spec import _load_yaml_like
from crypto_research_agents.storage.paths import resolve_project_path


@dataclass(slots=True)
class CronJobSpec:
    job_id: str
    schedule: str
    workflow_id: str
    output: str = "local"
    profile: str = "researcher"
    enabled: bool = True
    silent_no_signal: bool = True
    description: str = ""

    @classmethod
    def from_dict(cls, job_id: str, data: dict[str, Any]) -> "CronJobSpec":
        return cls(
            job_id=job_id,
            schedule=str(data.get("schedule", "")),
            workflow_id=str(data.get("workflow", data.get("workflow_id", ""))),
            output=str(data.get("output", "local")),
            profile=str(data.get("profile", "researcher")),
            enabled=bool(data.get("enabled", True)),
            silent_no_signal=bool(data.get("silent_no_signal", True)),
            description=str(data.get("description", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "schedule": self.schedule,
            "workflow": self.workflow_id,
            "output": self.output,
            "profile": self.profile,
            "enabled": self.enabled,
            "silent_no_signal": self.silent_no_signal,
            "description": self.description,
        }


@dataclass(slots=True)
class CronRunResult:
    job_id: str
    status: str
    should_notify: bool
    output: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "should_notify": self.should_notify,
            "output": self.output,
            "detail": self.detail,
        }


class CronRegistry:
    def __init__(self, jobs: dict[str, CronJobSpec] | None = None) -> None:
        self.jobs = jobs or {}

    @classmethod
    def load(cls, path: str | Path = "config/jobs.yaml") -> "CronRegistry":
        target = resolve_project_path(path)
        jobs: dict[str, CronJobSpec] = {}
        if target.exists():
            data = _load_yaml_like(target)
            for job_id, raw in dict(data.get("jobs", {})).items():
                jobs[str(job_id)] = CronJobSpec.from_dict(str(job_id), dict(raw or {}))
        local_path = resolve_project_path("data/jobs.local.json")
        if local_path.exists():
            try:
                local_data = json.loads(local_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                local_data = {}
            for job_id, raw in dict(local_data.get("jobs", {})).items():
                jobs[str(job_id)] = CronJobSpec.from_dict(str(job_id), dict(raw or {}))
        return cls(jobs)

    def get(self, job_id: str) -> CronJobSpec | None:
        return self.jobs.get(job_id)

    def list_jobs(self) -> list[CronJobSpec]:
        return sorted(self.jobs.values(), key=lambda item: item.job_id)

    def run_job(self, job_id: str, *, signal: dict[str, Any] | None = None) -> CronRunResult:
        job = self.jobs.get(job_id)
        if job is None:
            return CronRunResult(job_id, "missing", False, detail="job is not configured")
        if not job.enabled:
            return CronRunResult(job_id, "disabled", False, detail="job is disabled")
        if not signal:
            return CronRunResult(
                job_id,
                "no_signal",
                False,
                output="" if job.silent_no_signal else "No signal.",
                detail="no candidate signal detected",
            )
        return CronRunResult(
            job_id,
            "ready",
            True,
            output=json.dumps(
                {
                    "workflow": job.workflow_id,
                    "signal": signal,
                    "profile": job.profile,
                },
                ensure_ascii=False,
            ),
            detail="signal accepted for workflow dispatch",
        )


def create_local_job(
    *,
    job_id: str,
    schedule: str,
    workflow_id: str,
    output: str = "local",
    profile: str = "researcher",
    path: str | Path = "data/jobs.local.json",
) -> Path:
    target = resolve_project_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    else:
        data = {}
    jobs = dict(data.get("jobs", {}))
    jobs[job_id] = {
        "schedule": schedule,
        "workflow": workflow_id,
        "output": output,
        "profile": profile,
        "enabled": True,
        "silent_no_signal": True,
        "description": "User-created scheduled JIMMORIA research job.",
    }
    target.write_text(json.dumps({"jobs": jobs}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target
