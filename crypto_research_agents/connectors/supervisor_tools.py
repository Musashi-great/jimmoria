from __future__ import annotations

from typing import Any

from crypto_research_agents.core.time import utc_now


def create_research_room(
    *,
    research_room_id: str,
    topic: str,
    goals: list[str] | None = None,
    process_id: str | None = None,
    supervisor_mode: str | None = None,
    agent_count: int | None = None,
) -> dict[str, Any]:
    return {
        "status": "success",
        "message": f"Supervisor office opened {research_room_id}.",
        "data": {
            "room_id": research_room_id,
            "topic": topic,
            "goals": goals or [],
            "process_id": process_id,
            "supervisor_mode": supervisor_mode,
            "agent_count": agent_count,
            "opened_at": utc_now(),
        },
    }


def create_task(
    *,
    research_room_id: str,
    task_id: str,
    assigned_agent_id: str,
    description: str = "",
    expected_output: str = "",
    phase: str = "",
    requires: list[str] | None = None,
    priority: str = "normal",
) -> dict[str, Any]:
    task = {
        "room_id": research_room_id,
        "task_id": task_id,
        "agent_id": assigned_agent_id,
        "description": description,
        "expected_output": expected_output,
        "phase": phase,
        "requires": requires or [],
        "priority": priority,
        "status": "created",
        "created_at": utc_now(),
    }
    return {
        "status": "success",
        "message": f"Task {task_id} created for {assigned_agent_id}.",
        "data": task,
    }


def assign_task(
    *,
    research_room_id: str,
    task_id: str,
    assigned_agent_id: str,
    objective: str = "",
    expected_output: str = "",
    priority: str = "normal",
) -> dict[str, Any]:
    assignment = {
        "room_id": research_room_id,
        "task_id": task_id,
        "agent_id": assigned_agent_id,
        "objective": objective,
        "expected_output": expected_output,
        "priority": priority,
        "status": "assigned",
        "assigned_at": utc_now(),
    }
    return {
        "status": "success",
        "message": f"Task {task_id} assigned to {assigned_agent_id}.",
        "data": assignment,
    }


def update_task_status(
    *,
    research_room_id: str,
    task_id: str,
    status: str,
    assigned_agent_id: str | None = None,
    summary: str = "",
) -> dict[str, Any]:
    update = {
        "room_id": research_room_id,
        "task_id": task_id,
        "agent_id": assigned_agent_id,
        "status": status,
        "summary": summary,
        "updated_at": utc_now(),
    }
    return {
        "status": "success",
        "message": f"Task {task_id} status updated to {status}.",
        "data": update,
    }


def agent_handoff(
    *,
    research_room_id: str,
    from_agent: str,
    to_agent: str,
    task_id: str = "",
    context_summary: str = "",
    artifacts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    handoff = {
        "room_id": research_room_id,
        "from_agent": from_agent,
        "to_agent": to_agent,
        "task_id": task_id,
        "context_summary": context_summary,
        "artifacts": artifacts or {},
        "status": "handoff_created",
        "created_at": utc_now(),
    }
    return {
        "status": "success",
        "message": f"Handoff from {from_agent} to {to_agent} recorded.",
        "data": handoff,
    }


def read_agent_status(
    *,
    research_room_id: str,
    agents: list[str] | None = None,
    completed_agents: list[str] | None = None,
) -> dict[str, Any]:
    completed = set(completed_agents or [])
    statuses = [
        {
            "agent_id": agent_id,
            "status": "completed" if agent_id in completed else "pending",
        }
        for agent_id in agents or []
    ]
    return {
        "status": "success",
        "message": f"Read {len(statuses)} agent statuses.",
        "data": {
            "room_id": research_room_id,
            "agents": statuses,
            "read_at": utc_now(),
        },
    }


def task_retry(
    *,
    research_room_id: str,
    task_id: str,
    reason: str = "",
) -> dict[str, Any]:
    return {
        "status": "success",
        "message": f"Retry queued for {task_id}.",
        "data": {
            "room_id": research_room_id,
            "task_id": task_id,
            "reason": reason,
            "status": "retry_queued",
            "queued_at": utc_now(),
        },
    }


def task_cancel(
    *,
    research_room_id: str,
    task_id: str,
    reason: str = "",
) -> dict[str, Any]:
    return {
        "status": "success",
        "message": f"Task {task_id} cancelled.",
        "data": {
            "room_id": research_room_id,
            "task_id": task_id,
            "reason": reason,
            "status": "cancelled",
            "cancelled_at": utc_now(),
        },
    }
