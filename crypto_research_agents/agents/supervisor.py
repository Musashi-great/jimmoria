from __future__ import annotations

from dataclasses import asdict
from typing import Any

from crypto_research_agents.agents.base import AgentResult, BaseAgent
from crypto_research_agents.core.bus import CollaborationBus
from crypto_research_agents.core.memory import SharedMemory
from crypto_research_agents.core.room import ResearchRoom


class SupervisorAgent(BaseAgent):
    agent_id = "supervisor_agent"
    name = "Supervisor Agent"
    task_type = "supervision"

    def run(self, room: ResearchRoom, memory: SharedMemory, bus: CollaborationBus, **kwargs: Any) -> AgentResult:
        decision = self.model_gateway.select(agent_id=self.agent_id, task_type=self.task_type)
        goals = kwargs.get("goals") or room.goals
        company_settings = kwargs.get("company_settings") if isinstance(kwargs.get("company_settings"), dict) else {}
        intake_decision = kwargs.get("intake_decision") if isinstance(kwargs.get("intake_decision"), dict) else {}
        job_contract = kwargs.get("job_contract") if isinstance(kwargs.get("job_contract"), dict) else {}
        process = kwargs.get("process") if isinstance(kwargs.get("process"), dict) else {}
        supervisor_mode = company_settings.get("supervisor_mode", "research_director")
        summary = (
            "Research room initialized by CEO-style supervisor orchestration."
            if supervisor_mode == "company_ceo"
            else "Research room initialized with controlled P2P orchestration."
        )
        delegation = self._delegate_room_tasks(
            room=room,
            bus=bus,
            process=process,
            supervisor_mode=supervisor_mode,
            job_contract=job_contract,
        )
        orchestration_plan = _build_orchestration_plan(
            room=room,
            goals=goals,
            process=process,
            delegation=delegation,
            supervisor_mode=supervisor_mode,
            job_contract=job_contract,
        )
        llm_analysis = self.llm_analysis_pass(
            room=room,
            objective="Create an evidence-bound orchestration plan for the specialist agents.",
            evidence={
                "topic": room.topic,
                "goals": goals,
                "agents": room.agents,
                "process": process,
                "delegation": delegation,
                "orchestration_plan": orchestration_plan,
                "company_settings": company_settings,
                "intake_decision": intake_decision,
                "job_contract": job_contract,
                "model_decision": asdict(decision),
            },
            fallback_summary=summary,
        )
        finding = self.write_finding(
            room=room,
            memory=memory,
            finding_type="supervision_plan",
            summary=summary,
            data={
                "topic": room.topic,
                "goals": goals,
                "agents": room.agents,
                "model_decision": asdict(decision),
                "company_settings": company_settings,
                "intake_decision": intake_decision,
                "job_contract": job_contract,
                "llm_analysis": llm_analysis,
                "delegation": delegation,
                "orchestration_plan": orchestration_plan,
            },
            confidence=0.9,
        )
        bus.update(
            room_id=room.room_id,
            from_agent=self.agent_id,
            summary="Supervisor created the orchestration plan, delegated specialist tasks, and set council/final-review checkpoints.",
            payload={
                "finding_id": finding.finding_id,
                "goals": goals,
                "intake_decision": intake_decision,
                "job_contract": job_contract,
                "llm_analysis": llm_analysis,
                "delegated_tasks": delegation.get("delegated_tasks", []),
                "orchestration_plan": orchestration_plan,
            },
        )
        return AgentResult(
            self.agent_id,
            f"{summary} Delegated {delegation.get('delegated_count', 0)} specialist tasks and set coordination checkpoints.",
            {
                "finding_id": finding.finding_id,
                "delegated_count": delegation.get("delegated_count", 0),
                "orchestration_plan": orchestration_plan,
            },
            confidence=0.9,
        )

    def _delegate_room_tasks(
        self,
        *,
        room: ResearchRoom,
        bus: CollaborationBus,
        process: dict[str, Any],
        supervisor_mode: str,
        job_contract: dict[str, Any],
    ) -> dict[str, Any]:
        process_id = str(process.get("process_id") or "")
        contract_context = _delegation_contract_context(job_contract)
        tasks = _task_payloads(process, room)
        office_results: list[dict[str, Any]] = []
        delegated_tasks: list[dict[str, Any]] = []
        room_result = self._call_office_tool(
            "create_research_room",
            room_id=room.room_id,
            topic=room.topic,
            goals=room.goals,
            process_id=process_id,
            supervisor_mode=supervisor_mode,
            agent_count=len(room.agents),
        )
        office_results.append({"tool": "create_research_room", "result": room_result})

        for task in tasks:
            task_id = task["task_id"]
            agent_id = task["agent_id"]
            create_result = self._call_office_tool(
                "create_task",
                room_id=room.room_id,
                task_id=task_id,
                agent_id=agent_id,
                description=task["description"],
                expected_output=task["expected_output"],
                phase=task["phase"],
                requires=task["requires"],
                priority=_priority_for_task(task),
            )
            office_results.append({"tool": "create_task", "task_id": task_id, "result": create_result})
            if agent_id == self.agent_id:
                status_result = self._call_office_tool(
                    "update_task_status",
                    room_id=room.room_id,
                    task_id=task_id,
                    agent_id=agent_id,
                    status="done",
                    summary="Supervisor planning task completed and downstream tasks prepared.",
                )
                office_results.append({"tool": "update_task_status", "task_id": task_id, "result": status_result})
                continue

            assign_result = self._call_office_tool(
                "assign_task",
                room_id=room.room_id,
                task_id=task_id,
                agent_id=agent_id,
                objective=task["description"],
                expected_output=task["expected_output"],
                priority=_priority_for_task(task),
            )
            office_results.append({"tool": "assign_task", "task_id": task_id, "result": assign_result})
            delegated_task = {
                **task,
                "priority": _priority_for_task(task),
                "assignment_status": assign_result.get("status", "unknown"),
            }
            delegated_tasks.append(delegated_task)
            bus.request(
                room_id=room.room_id,
                from_agent=self.agent_id,
                to_agent=agent_id,
                objective=task["description"] or f"Complete {task_id}.",
                required_output=[task["expected_output"]] if task["expected_output"] else [],
                context={
                    "task_id": task_id,
                    "phase": task["phase"],
                    "requires": task["requires"],
                    "process_id": process_id,
                    "supervisor_mode": supervisor_mode,
                    **contract_context,
                },
                priority=_priority_for_task(task),
            )

        if delegated_tasks:
            first_task = delegated_tasks[0]
            handoff_result = self._call_office_tool(
                "agent_handoff",
                room_id=room.room_id,
                from_agent=self.agent_id,
                to_agent=first_task["agent_id"],
                task_id=first_task["task_id"],
                context_summary="Supervisor finished room planning and opened the first specialist assignment.",
                artifacts={
                    "delegated_task_count": len(delegated_tasks),
                    "process_id": process_id,
                    "delegation_policy": contract_context.get("delegation_policy", {}),
                },
            )
            office_results.append({"tool": "agent_handoff", "task_id": first_task["task_id"], "result": handoff_result})
            bus.handoff(
                room_id=room.room_id,
                from_agent=self.agent_id,
                to_agent=first_task["agent_id"],
                summary="Supervisor completed office planning; start the first assigned task.",
                payload={
                    "first_task": first_task,
                    "delegated_task_count": len(delegated_tasks),
                    **contract_context,
                },
            )

        return {
            "process_id": process_id,
            "delegated_count": len(delegated_tasks),
            "delegated_tasks": delegated_tasks,
            "office_tool_results": office_results,
        }

    def _call_office_tool(self, tool_name: str, **kwargs: Any) -> dict[str, Any]:
        tool_kwargs = dict(kwargs)
        event_room_id = str(tool_kwargs.pop("room_id", "") or "")
        if event_room_id:
            tool_kwargs.setdefault("research_room_id", event_room_id)
        if tool_name in {"create_task", "assign_task", "update_task_status"} and "agent_id" in tool_kwargs:
            tool_kwargs["assigned_agent_id"] = tool_kwargs.pop("agent_id")
        try:
            return self.tool_gateway.call(self.agent_id, tool_name, room_id=event_room_id, **tool_kwargs)
        except Exception as exc:  # pragma: no cover - defensive around optional tool policy
            return {
                "status": "failed",
                "tool": tool_name,
                "message": str(exc),
                "data": None,
            }


def _task_payloads(process: dict[str, Any], room: ResearchRoom) -> list[dict[str, Any]]:
    process_tasks = process.get("tasks")
    if isinstance(process_tasks, list) and process_tasks:
        return [
            {
                "task_id": str(item.get("task_id") or item.get("agent_id") or "task"),
                "agent_id": str(item.get("agent_id") or ""),
                "phase": str(item.get("phase") or ""),
                "description": str(item.get("description") or ""),
                "expected_output": str(item.get("expected_output") or ""),
                "requires": [str(value) for value in item.get("requires", [])],
            }
            for item in process_tasks
            if isinstance(item, dict) and item.get("agent_id")
        ]
    return [
        {
            "task_id": f"{agent_id}_task",
            "agent_id": agent_id,
            "phase": "specialist_work" if agent_id != SupervisorAgent.agent_id else "management",
            "description": f"Run the assigned room work for {agent_id}.",
            "expected_output": "Finding or artifact appropriate to this agent.",
            "requires": [],
        }
        for agent_id in room.agents
    ]


def _priority_for_task(task: dict[str, Any]) -> str:
    phase = str(task.get("phase") or "")
    if phase in {"management", "source_memory", "candidate_discovery", "synthesis"}:
        return "high"
    if phase in {"knowledge_ops"}:
        return "low"
    return "normal"


def _build_orchestration_plan(
    *,
    room: ResearchRoom,
    goals: Any,
    process: dict[str, Any],
    delegation: dict[str, Any],
    supervisor_mode: str,
    job_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    delegated_tasks = delegation.get("delegated_tasks", [])
    if not isinstance(delegated_tasks, list):
        delegated_tasks = []
    contract = job_contract if isinstance(job_contract, dict) else {}
    coordination_checkpoints = [
        {
            "checkpoint": "intake_and_scope",
            "owner": "supervisor_agent",
            "purpose": "Confirm the client request, output mode, quality bar, and whether a Research Room is needed.",
        },
        {
            "checkpoint": "task_delegation",
            "owner": "supervisor_agent",
            "purpose": "Create specialist tasks, assign owners, and record handoffs through Supervisor Office tools.",
        },
        {
            "checkpoint": "specialist_execution",
            "owner": "specialist_agents",
            "purpose": "Let each specialist gather evidence and report gaps inside its scope.",
        },
        {
            "checkpoint": "agent_council",
            "owner": "agent_council",
            "purpose": "Compare specialist findings, surface conflicts, and choose dossier versus diagnostic memo.",
        },
        {
            "checkpoint": "final_review",
            "owner": "supervisor_agent",
            "purpose": "Review quality gate, delivery mode, unresolved risks, and client-facing final output.",
        },
    ]
    return {
        "mode": "supervisor_orchestrator",
        "supervisor_mode": supervisor_mode,
        "room_id": room.room_id,
        "topic": room.topic,
        "goals": goals,
        "process_id": process.get("process_id"),
        "loop_mode": contract.get("loop_mode", "closed_fleet" if delegated_tasks else "single_agent_chat"),
        "job_contract": contract,
        "delegated_count": len(delegated_tasks),
        "agent_order": [task.get("agent_id") for task in delegated_tasks if isinstance(task, dict)],
        "coordination_checkpoints": coordination_checkpoints,
        "verification_gates": contract.get("verification_gates", []),
        "iteration_policy": contract.get("iteration_policy", {}),
        "extension_policy": contract.get("extension_policy", {}),
        "context_policy": contract.get("context_policy", {}),
        "memory_policy": contract.get("memory_policy", {}),
        "delegation_policy": contract.get("delegation_policy", {}),
        "supervisor_decision_rights": [
            "decide whether to open a Research Room",
            "set room goals and priority",
            "delegate specialist tasks",
            "coordinate Agent Council",
            "approve final delivery mode",
        ],
    }


def _delegation_contract_context(job_contract: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(job_contract, dict) or not job_contract:
        return {}
    return {
        "job_contract": {
            "loop_mode": job_contract.get("loop_mode"),
            "output_mode": job_contract.get("output_mode"),
            "goal": job_contract.get("goal"),
            "topic": job_contract.get("topic"),
            "source_requirements": job_contract.get("source_requirements", []),
            "verification_gates": job_contract.get("verification_gates", []),
            "completion_criteria": job_contract.get("completion_criteria", []),
        },
        "source_requirements": job_contract.get("source_requirements", []),
        "verification_gates": job_contract.get("verification_gates", []),
        "completion_criteria": job_contract.get("completion_criteria", []),
        "context_policy": job_contract.get("context_policy", {}),
        "delegation_policy": job_contract.get("delegation_policy", {}),
    }
