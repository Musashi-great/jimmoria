from enum import Enum


class RuntimeState(str, Enum):
    CREATED = "created"
    ASSIGNED = "assigned"
    RUNNING = "running"
    WAITING_FOR_TOOL = "waiting_for_tool"
    WAITING_FOR_AGENT = "waiting_for_agent"
    READY_FOR_REPORT = "ready_for_report"
    WRITING_REPORT = "writing_report"
    OBSIDIAN_SYNCING = "obsidian_syncing"
    COMPLETED = "completed"
    FAILED = "failed"
