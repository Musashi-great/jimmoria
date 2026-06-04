from __future__ import annotations

import os
import shutil
import sys
import textwrap
from pathlib import Path
from typing import Any

from crypto_research_agents import APP_NAME, __version__
from crypto_research_agents.core.agent_spec import AgentSpecRegistry
from crypto_research_agents.runtime import DEFAULT_AGENTS
from crypto_research_agents.storage.json_store import load_memory
from crypto_research_agents.storage.paths import resolve_project_path
from crypto_research_agents.storage.run_store import list_run_summaries, load_run_file

try:
    from rich import box
    from rich.console import Console as RichConsole
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError:  # pragma: no cover - fallback for non-installed editable checkouts
    box = None
    RichConsole = None
    Panel = None
    Table = None
    Text = None


AGENT_ACTIVITY = {
    "supervisor_agent": "Planning direction",
    "ingestion_agent": "Extracting source metadata",
    "social_kol_agent": "Collecting X/KOL market signals",
    "narrative_agent": "Mapping narratives",
    "discovery_agent": "Resolving candidates",
    "contract_onchain_agent": "Checking token identity",
    "product_tech_agent": "Checking docs/GitHub",
    "funding_token_agent": "Checking funding/token hints",
    "report_agent": "Writing dossier",
    "obsidian_curator_agent": "Syncing vault notes",
}


class JimmoriaConsole:
    """Chat-like CLI surface for the multi-agent research company."""

    def __init__(
        self,
        *,
        agent_spec_dir: str | Path = "config/agents",
        memory_path: str | Path = "data/memory.json",
        runs_dir: str | Path = "data/runs",
    ) -> None:
        self.registry = AgentSpecRegistry.load_dir(agent_spec_dir)
        self.memory_path = resolve_project_path(memory_path)
        self.runs_dir = resolve_project_path(runs_dir)
        self.agent_state: dict[str, str] = {}
        self.agent_activity: dict[str, str] = {}
        self.last_room_id = ""
        self.width = min(shutil.get_terminal_size((100, 30)).columns, 110)
        self.use_rich = RichConsole is not None and not os.getenv("JIMMORIA_PLAIN_LOGS")
        self.event_style = os.getenv("JIMMORIA_EVENT_STYLE", "stream").strip().lower() or "stream"
        self.runtime_room_running = False
        self.runtime_dock_lines = 0
        self.runtime_dock_frame = 0
        self.last_runtime_metrics: dict[str, object] = {}

    def print_intro(self) -> None:
        print_jimmoria_logo(self.width)

    def print_help(self) -> None:
        lines = [
            "Type a message. The Supervisor decides whether it is research, settings, status, or source ingest.",
            "",
            "Commands:",
            "  /add <text-or-url>       Ingest source only",
            "  /models                  Configure LLM provider/models",
            "  /doctor                  Show configured vs placeholder capabilities",
            "  /company                 Show active and planned agents",
            "  /settings                Show company operating settings",
            "  /board                   Show current live agent board",
            "  /context                 Show shared memory and latest run context",
            "  /rooms                   Show multi-room workload board",
            "  /runs                    Show previous runs",
            "  /status [room_id]        Show latest or selected room status",
            "  /messages [room_id]      Show collaboration history",
            "  /events [room_id]        Show saved UI/replay events",
            "  /report [room_id]        Print saved report",
            "  /last                    Show the latest run card",
            "  /help                    Show this help",
            "  /quit                    Exit",
        ]
        self.block("JIMMORIA commands", lines)

    def print_company(self, *, active_only: bool = False) -> None:
        agent_ids = DEFAULT_AGENTS if active_only else sorted(self.registry.specs)
        rows = []
        for agent_id in agent_ids:
            spec = self.registry.get(agent_id)
            persona = spec.persona_name if spec else "-"
            status = "enabled" if agent_id in DEFAULT_AGENTS else "planned"
            one_liner = ""
            if spec:
                one_liner = spec.identity.one_liner or spec.role.description
            rows.append(f"{agent_id:<28} {persona:<34} {status:<8} {one_liner}")
        self.block("Company roster", rows)

    def print_company_settings(self, settings: Any, path: str | Path) -> None:
        principles = list(getattr(settings, "operating_principles", []) or [])
        lines = [
            f"Settings file: {path}",
            f"Report language: {getattr(settings, 'report_language', 'en')}",
            f"English technical terms: {'allowed' if getattr(settings, 'allow_english_terms', True) else 'restricted'}",
            f"Supervisor mode: {getattr(settings, 'supervisor_mode', 'research_director')}",
            f"Client relationship: {getattr(settings, 'client_relationship', 'user')}",
            f"Auto-apply company instructions: {getattr(settings, 'auto_apply_company_instructions', True)}",
        ]
        if principles:
            lines.extend(["", "Operating principles:"])
            lines.extend(f"- {item}" for item in principles[-8:])
        authority = list(getattr(settings, "supervisor_authority", []) or [])
        if authority:
            lines.extend(["", "Supervisor authority:"])
            lines.extend(f"- {item}" for item in authority[-10:])
        self.block("Company settings", lines)

    def print_supervisor_intake(self, decision: Any) -> None:
        lines = [
            f"Intent: {getattr(decision, 'intent_type', 'unknown')}",
            f"Action: {getattr(decision, 'action', 'unknown')}",
            f"Output mode: {getattr(decision, 'output_mode', 'unknown')}",
            f"Research Room: {'open' if getattr(decision, 'needs_research_room', False) else 'not needed'}",
            f"Confidence: {getattr(decision, 'confidence', 0):.2f}",
            f"Why: {getattr(decision, 'rationale', '')}",
            f"Next: {getattr(decision, 'next_step', '')}",
        ]
        self.block("Supervisor intake", lines)

    def print_supervisor_reply(self, lines: list[str]) -> None:
        self.block("Supervisor", lines)

    def confirm_dispatch(
        self,
        *,
        intent_type: str,
        title: str,
        agent_count: int,
    ) -> bool:
        action = {
            "research_request": "Open full Research Room",
            "source_ingestion": "Save source only",
        }.get(intent_type, intent_type)
        lines = [
            "제가 이렇게 이해했습니다.",
            f"Action: {action}",
            f"Topic: {title}",
            f"Agents: {agent_count}",
            "",
            "진행하려면 Enter 또는 y, 취소하려면 n을 입력하세요.",
        ]
        self.block("Supervisor check", lines)
        if not sys.stdin.isatty():
            return True
        answer = input("Proceed [Enter/Y/n]: ").strip().lower()
        return answer in {"", "y", "yes", "ye", "go", "proceed", "ㅇ", "예", "네", "응"}

    def print_supervisor_working(self, activity: str = "Reading your request and choosing the next move.") -> None:
        self.print_log_line("Supervisor", activity, muted=True)

    def print_company_settings_updated(self, settings: Any, applied: list[str], path: str | Path) -> None:
        lines = [
            "No Research Room opened.",
            "This was treated as a company operating instruction.",
            f"Settings file: {path}",
            "",
            "Applied:",
        ]
        lines.extend(f"- {item}" for item in applied)
        lines.extend(
            [
                "",
                f"Report language: {getattr(settings, 'report_language', 'en')}",
                f"Supervisor mode: {getattr(settings, 'supervisor_mode', 'research_director')}",
            ]
        )
        self.block("Company instruction applied", lines)

    def print_user_message(self, text: str) -> None:
        self.print_log_line("You", text)

    def print_log_line(self, label: str, text: str, *, muted: bool = False) -> None:
        wrapped = self.wrap(text)
        if not wrapped:
            return
        if supports_color():
            label_style = "\033[38;2;255;92;212m"
            body_style = "\033[38;2;160;132;188m" if muted else "\033[38;2;230;214;255m"
            reset = "\033[0m"
            prefix = f"{label_style}{label}{reset} {label_style}>{reset} "
            continuation = " " * (len(label) + 3)
            print("")
            for index, line in enumerate(wrapped):
                if index == 0:
                    print(f"{prefix}{body_style}{line}{reset}")
                else:
                    print(f"{continuation}{body_style}{line}{reset}")
            return

        prefix = f"{label} > "
        continuation = " " * len(prefix)
        print("")
        for index, line in enumerate(wrapped):
            if index == 0:
                print(f"{prefix}{line}")
            else:
                print(f"{continuation}{line}")

    def print_event_line(self, label: str, text: str, *, muted: bool = False) -> None:
        if self.use_runtime_dock():
            self.erase_runtime_dock()
        self.print_log_line(label, text, muted=muted)
        if self.use_runtime_dock() and self.runtime_room_running:
            self.print_runtime_dock()
        elif self.use_runtime_dock():
            self.show_cursor()

    def use_stream_events(self) -> bool:
        return self.event_style not in {"card", "cards", "panel", "panels"}

    def use_runtime_dock(self) -> bool:
        return self.use_stream_events() and supports_color() and not os.getenv("JIMMORIA_NO_RUNTIME_DOCK")

    def read_chat_input(self) -> str:
        if not sys.stdin.isatty():
            return input(f"\n{APP_NAME.lower()}> ")

        if supports_color():
            return self.read_ansi_boxed_input()
        return self.read_basic_boxed_input()

    def read_ansi_boxed_input(self) -> str:
        self.show_cursor()
        hint = "Type a request, URL, /command, or @path/to/file"
        border = self.input_border()
        print("")
        print(self.input_border_style(border))
        print(self.input_status_line_style(self.input_text_line(self.input_status_text())))
        print(self.input_border_style(self.input_hint_line(hint)))
        print(self.input_edit_line_style())
        print(self.input_border_style(border))
        try:
            return input(self.input_cursor_sequence())
        finally:
            self.clear_submitted_input_box()
            sys.stdout.flush()

    def clear_submitted_input_box(self) -> None:
        # Move below the drawn input dock, then delete its five prompt lines.
        sys.stdout.write("\033[1B\r\033[5A\033[5M\r")

    def erase_runtime_dock(self) -> None:
        if not self.runtime_dock_lines:
            return
        sys.stdout.write(f"\r\033[{self.runtime_dock_lines}A\033[{self.runtime_dock_lines}M\r")
        self.runtime_dock_lines = 0

    def print_runtime_dock(self) -> None:
        self.hide_cursor()
        self.runtime_dock_frame += 1
        border = self.input_border()
        lines = [
            self.input_border_style(border),
            self.input_status_line_style(self.input_text_line(self.input_status_text())),
            self.input_border_style(self.input_hint_line("Room running. Input returns when Supervisor finishes this room.")),
            self.input_divider_line_style(),
            self.input_board_title_line_style(),
            self.input_board_header_line_style(),
        ]
        lines.extend(self.runtime_agent_board_lines())
        lines.extend(
            [
                self.input_divider_line_style(),
                self.input_locked_line_style(),
                self.input_border_style(border),
            ]
        )
        for line in lines:
            print(line)
        self.runtime_dock_lines = len(lines)

    def hide_cursor(self) -> None:
        if supports_color():
            sys.stdout.write("\033[?25l")

    def show_cursor(self) -> None:
        if supports_color():
            sys.stdout.write("\033[?25h")

    def read_basic_boxed_input(self) -> str:
        hint = "Type a request, URL, /command, or @path/to/file"
        border = self.input_border()
        print("")
        print(border)
        print(self.input_text_line(self.input_status_text()))
        print(self.input_hint_line(hint))
        try:
            return input("| > ")
        finally:
            print(self.input_border_style(border))

    def input_box_width(self) -> int:
        available_width = max(32, self.width - 4)
        return max(32, min(available_width, 100))

    def input_border(self) -> str:
        return "+" + "-" * (self.input_box_width() - 2) + "+"

    def input_text_line(self, text: str) -> str:
        inner_width = self.input_box_width() - 4
        clipped = text[:inner_width]
        return "| " + clipped.ljust(inner_width) + " |"

    def input_hint_line(self, text: str) -> str:
        return self.input_text_line(text)

    def input_divider_line(self) -> str:
        return "|" + "-" * (self.input_box_width() - 2) + "|"

    def input_edit_line(self) -> str:
        inner_width = self.input_box_width() - 4
        return "| " + "> ".ljust(inner_width) + " |"

    def input_status_text(self) -> str:
        provider = os.getenv("LLM_PROVIDER") or "offline"
        room = self.short_room_label()
        agents = self.agent_state_label()
        return f"JIMMORIA HQ | Supervisor channel | provider: {provider} | room: {room} | agents: {agents}"

    def short_room_label(self) -> str:
        if not self.last_room_id:
            return "none"
        if len(self.last_room_id) <= 18:
            return self.last_room_id
        return self.last_room_id[:10] + "..." + self.last_room_id[-5:]

    def agent_state_label(self) -> str:
        if not self.agent_state:
            return "idle"
        running = sum(1 for state in self.agent_state.values() if state == "running")
        queued = sum(1 for state in self.agent_state.values() if state == "queued")
        done = sum(1 for state in self.agent_state.values() if state == "done")
        failed = sum(1 for state in self.agent_state.values() if state == "failed")
        if failed:
            return f"{failed} fail/{running} run/{queued} wait"
        if running:
            return f"{running} run/{queued} wait/{done} done"
        if queued:
            return f"{queued} wait/{done} done"
        return f"{done} done"

    def input_border_style(self, text: str) -> str:
        if not supports_color():
            return text
        return f"\033[38;2;211;95;255m{text}\033[0m"

    def input_divider_line_style(self) -> str:
        return self.input_border_style(self.input_divider_line())

    def input_status_line_style(self, text: str) -> str:
        if not supports_color():
            return text
        violet = "\033[38;2;211;95;255m"
        dim = "\033[38;2;126;96;154m"
        pink = "\033[38;2;255;92;212m"
        reset = "\033[0m"
        return text.replace("|", f"{violet}|{reset}", 2).replace("JIMMORIA HQ", f"{pink}JIMMORIA HQ{reset}", 1).replace(
            "Supervisor channel",
            f"{dim}Supervisor channel{reset}",
            1,
        )

    def input_edit_line_style(self) -> str:
        violet = "\033[38;2;211;95;255m"
        pink = "\033[38;2;255;92;212m"
        reset = "\033[0m"
        inner_width = self.input_box_width() - 4
        padding = " " * max(inner_width - 2, 0)
        return f"{violet}|{reset} {pink}>{reset} {padding}{violet}|{reset}"

    def input_locked_line_style(self) -> str:
        violet = "\033[38;2;211;95;255m"
        muted = "\033[38;2;126;96;154m"
        pink = "\033[38;2;255;92;212m"
        blink = "\033[5m"
        reset = "\033[0m"
        inner_width = self.input_box_width() - 4
        text = "> working..."
        visible_prefix = "> working"
        visible_dots = "..."
        padding = " " * max(inner_width - len(text), 0)
        return (
            f"{violet}|{reset} {muted}{visible_prefix}{reset}"
            f"{blink}{pink}{visible_dots}{reset}{padding}{violet}|{reset}"
        )

    def input_board_title_line_style(self) -> str:
        line = self.input_text_line("Live agent board - current work")
        if not supports_color():
            return line
        pink = "\033[38;2;255;92;212m"
        violet = "\033[38;2;211;95;255m"
        reset = "\033[0m"
        return line.replace("|", f"{violet}|{reset}", 2).replace(
            "Live agent board",
            f"{pink}Live agent board{reset}",
            1,
        )

    def input_board_header_line_style(self) -> str:
        line = self.input_text_line(f"{'STATE':<6} {'AGENT':<28} CURRENT WORK")
        if not supports_color():
            return line
        violet = "\033[38;2;211;95;255m"
        muted = "\033[38;2;160;132;188m"
        reset = "\033[0m"
        return line.replace("|", f"{violet}|{reset}", 2).replace(
            "STATE",
            f"{muted}STATE{reset}",
            1,
        ).replace(
            "AGENT",
            f"{muted}AGENT{reset}",
            1,
        ).replace(
            "CURRENT WORK",
            f"{muted}CURRENT WORK{reset}",
            1,
        )

    def runtime_agent_board_lines(self) -> list[str]:
        if not self.agent_state:
            return [self.input_text_line("IDLE   no active room              Waiting for your next request")]

        lines: list[str] = []
        for agent_id in DEFAULT_AGENTS:
            if agent_id not in self.agent_state:
                continue
            state = self.agent_state[agent_id]
            activity = self.agent_activity.get(agent_id) or AGENT_ACTIVITY.get(agent_id, "")
            label = self.state_label(state)
            row = f"{label:<6} {agent_id:<28} {self.activity_label(state, activity)}"
            lines.append(self.runtime_agent_board_line_style(self.input_text_line(row), state))
        return lines

    def runtime_agent_board_line_style(self, line: str, state: str) -> str:
        if not supports_color():
            return line
        violet = "\033[38;2;211;95;255m"
        running = "\033[38;2;255;210;245m"
        done = "\033[38;2;120;255;190m"
        failed = "\033[38;2;255;92;120m"
        waiting = "\033[38;2;160;132;188m"
        reset = "\033[0m"
        state_style = {
            "running": running,
            "done": done,
            "failed": failed,
            "queued": waiting,
        }.get(state, "\033[38;2;230;214;255m")
        styled = line.replace("|", f"{violet}|{reset}", 2)
        label = self.state_label(state)
        return styled.replace(label, f"{state_style}{label}{reset}", 1)

    def input_cursor_sequence(self) -> str:
        return "\033[2A\033[4C"

    def make_event_handler(self) -> Any:
        def handle(event: dict[str, object]) -> None:
            self.handle_event(event)

        return handle

    def handle_event(self, event: dict[str, object]) -> None:
        event_type = str(event.get("type", ""))
        if event_type == "room_created":
            self.runtime_room_running = True
            self.last_room_id = str(event.get("room_id", ""))
            self.agent_state = {
                str(agent_id): "queued"
                for agent_id in event.get("agents", [])
            }
            self.agent_activity = {
                str(agent_id): AGENT_ACTIVITY.get(str(agent_id), "Waiting for assignment")
                for agent_id in event.get("agents", [])
            }
            if self.use_stream_events():
                topic = self.compact_text(str(event.get("topic", "")), 72)
                agent_count = len(event.get("agents", []))
                process = event.get("process") if isinstance(event.get("process"), dict) else {}
                process_id = process.get("process_id") if isinstance(process, dict) else ""
                process_text = f" | process {process_id}" if process_id else ""
                self.print_event_line("Room", f"OPEN {event.get('room_id')} | agents {agent_count}{process_text} | {topic}")
                self.print_event_line("Board", self.agent_state_label(), muted=True)
                return
            lines = [
                f"Room: {event.get('room_id')}",
                f"Topic: {event.get('topic')}",
                "",
                "Goals:",
            ]
            lines.extend(f"- {goal}" for goal in event.get("goals", []))
            lines.extend(["", f"Dispatching {len(event.get('agents', []))} agents."])
            self.block("JIMMORIA opens a Research Room", lines)
            self.print_agent_state()
            return

        if event_type == "agent_start":
            agent_id = str(event.get("agent_id", ""))
            self.agent_state[agent_id] = "running"
            self.agent_activity[agent_id] = AGENT_ACTIVITY.get(agent_id, f"Running {event.get('task_type')}")
            if self.use_stream_events():
                self.print_event_line("Agent", f"RUN {agent_id} | {self.agent_activity[agent_id]}")
                return
            label = self.agent_label(agent_id)
            self.block(
                f"{label} started",
                [
                    f"State: {self.state_label('running')}",
                    f"Task type: {event.get('task_type')}",
                    f"Now: {self.agent_activity[agent_id]}",
                ],
            )
            return

        if event_type == "agent_done":
            agent_id = str(event.get("agent_id", ""))
            self.agent_state[agent_id] = "done"
            self.agent_activity[agent_id] = f"Done: {event.get('summary')}"
            if self.use_stream_events():
                summary = self.compact_text(str(event.get("summary", "")), 76)
                self.print_event_line(
                    "Agent",
                    f"DONE {agent_id} | {summary} | msg {event.get('messages')} / findings {event.get('findings')}{self.event_metrics_suffix(event)}",
                )
                return
            label = self.agent_label(agent_id)
            summary = str(event.get("summary", ""))
            self.block(
                f"{label} finished",
                [
                    f"State: {self.state_label('done')}",
                    f"Finished: {summary}",
                    f"Messages: {event.get('messages')}",
                    f"Findings: {event.get('findings')}",
                    *self.event_metric_lines(event),
                ],
            )
            return

        if event_type == "agent_failed":
            agent_id = str(event.get("agent_id", ""))
            self.agent_state[agent_id] = "failed"
            self.agent_activity[agent_id] = f"Failed: {event.get('error')}"
            if self.use_stream_events():
                error = self.compact_text(str(event.get("error", "")), 82)
                self.print_event_line("Agent", f"FAIL {agent_id} | {error}{self.event_metrics_suffix(event)}")
                return
            label = self.agent_label(agent_id)
            self.block(
                f"{label} failed",
                [
                    f"State: {self.state_label('failed')}",
                    f"Task type: {event.get('task_type')}",
                    f"Stopped: {event.get('error')}",
                    *self.event_metric_lines(event),
                ],
            )
            self.print_agent_state()
            return

        if event_type in {"tool_start", "tool_done", "tool_failed", "tool_denied", "tool_unconfigured"}:
            self.update_tool_activity(event_type, event)
            self.print_tool_event(event_type, event)
            return

        if event_type in {"finding_saved", "source_saved", "report_written", "note_written"}:
            self.print_output_event(event_type, event)
            return

        if event_type == "orchestration_plan":
            delegated_count = event.get("delegated_count", 0)
            checkpoints = event.get("checkpoints")
            checkpoint_count = len(checkpoints) if isinstance(checkpoints, list) else 0
            summary = self.compact_text(str(event.get("summary") or "Supervisor set the orchestration plan."), 78)
            if self.use_stream_events():
                self.print_event_line(
                    "Plan",
                    f"ORCHESTRATE {delegated_count} tasks | checkpoints {checkpoint_count} | {summary}",
                )
                return
            self.block(
                "Supervisor orchestration plan",
                [
                    f"Delegated tasks: {delegated_count}",
                    f"Checkpoints: {checkpoint_count}",
                    summary,
                ],
            )
            return

        if event_type == "deliberation_start":
            participants = event.get("participants", [])
            count = len(participants) if isinstance(participants, list) else 0
            if self.use_stream_events():
                self.print_event_line("Council", f"START specialist roundtable | agents {count}")
                return
            self.block(
                "Agent council started",
                [
                    f"Participants: {count}",
                    str(event.get("summary") or "Specialists compare findings."),
                ],
            )
            return

        if event_type == "deliberation_done":
            decision = str(event.get("decision") or "")
            summary = self.compact_text(str(event.get("summary") or ""), 84)
            if self.use_stream_events():
                self.print_event_line(
                    "Council",
                    f"DONE {decision} | {summary} | msg {event.get('messages')} / findings {event.get('findings')}",
                )
                return
            self.block(
                "Agent council consensus",
                [
                    f"Decision: {decision}",
                    f"Summary: {event.get('summary')}",
                    f"Messages: {event.get('messages')}",
                    f"Findings: {event.get('findings')}",
                ],
            )
            return

        if event_type == "final_review_start":
            summary = self.compact_text(str(event.get("summary") or ""), 84)
            if self.use_stream_events():
                self.print_event_line("Supervisor", f"REVIEW report | {summary}")
                return
            self.block("Supervisor final review started", [str(event.get("summary") or "")])
            return

        if event_type == "final_review_done":
            delivery_mode = str(event.get("delivery_mode") or "")
            summary = self.compact_text(str(event.get("summary") or ""), 84)
            if self.use_stream_events():
                self.print_event_line(
                    "Supervisor",
                    f"FINAL {delivery_mode} | {summary} | msg {event.get('messages')} / findings {event.get('findings')}",
                )
                return
            self.block(
                "Supervisor final review",
                [
                    f"Delivery mode: {delivery_mode}",
                    f"Approved: {event.get('approved')}",
                    f"Summary: {event.get('summary')}",
                ],
            )
            return

        if event_type == "room_completed":
            quality_status = str(event.get("research_quality_status") or "")
            quality_suffix = f" | quality {quality_status}" if quality_status else ""
            self.last_runtime_metrics = {
                "duration_ms": event.get("duration_ms"),
                "llm_usage": event.get("llm_usage"),
            }
            if self.use_stream_events():
                self.runtime_room_running = False
                self.print_event_line(
                    "Room",
                    f"DONE {event.get('room_id')} | status {event.get('status')}{quality_suffix} | msg {event.get('messages')} / findings {event.get('findings')}{self.event_metrics_suffix(event)}",
                )
                return
            lines = [
                f"Room: {event.get('room_id')}",
                f"Status: {event.get('status')}",
                f"Messages: {event.get('messages')}",
                f"Findings: {event.get('findings')}",
                *self.event_metric_lines(event),
            ]
            if quality_status:
                lines.append(f"Research quality: {quality_status}")
            self.block("JIMMORIA finalizes the room", lines)
            self.print_agent_state()
            return

        if event_type == "room_failed":
            self.last_runtime_metrics = {
                "duration_ms": event.get("duration_ms"),
                "llm_usage": event.get("llm_usage"),
            }
            if self.use_stream_events():
                self.runtime_room_running = False
                reason = self.compact_text(str(event.get("summary", "")), 86)
                self.print_event_line("Room", f"FAIL {event.get('room_id')} | {reason}{self.event_metrics_suffix(event)}")
                return
            self.block(
                "JIMMORIA room failed",
                [
                    f"Room: {event.get('room_id')}",
                    f"Status: {event.get('status')}",
                    f"Reason: {event.get('summary')}",
                    *self.event_metric_lines(event),
                ],
            )
            self.print_agent_state()

    def print_run_summary(self, result: object) -> None:
        room = result.room
        memory = result.memory
        bus = result.bus
        report_path = room.output_paths.get("report", "")
        evidence_packet_path = room.output_paths.get("evidence_packet", "")
        vault_path = room.output_paths.get("obsidian_vault", "")
        lines = [
            f"Room: {room.room_id}",
            f"Status: {room.status}",
            f"Messages: {len(bus.messages)}",
            f"Findings: {len(memory.get_room_findings(room.room_id))}",
        ]
        runtime_metrics = room.project_card.get("runtime_metrics") if isinstance(room.project_card, dict) else {}
        if isinstance(runtime_metrics, dict):
            lines.extend(self.runtime_metric_lines(runtime_metrics))
        quality = room.project_card.get("research_quality") if isinstance(room.project_card, dict) else {}
        quality_status = ""
        if isinstance(quality, dict) and quality.get("status"):
            quality_status = str(quality.get("status"))
            lines.append(f"Research quality: {quality.get('status')}")
            if quality.get("reasons"):
                lines.append(f"Quality reasons: {'; '.join(str(item) for item in quality.get('reasons', []))}")
        if report_path:
            lines.append(f"Report: {report_path}")
            lines.append(f"Full report command: /report {room.room_id}")
        if evidence_packet_path:
            lines.append(f"Evidence packet: {evidence_packet_path}")
        if vault_path:
            lines.append(f"Vault: {vault_path}")
        lines.append(f"Replay events: {self.runs_dir / room.room_id / 'events.json'}")
        summary_title = "JIMMORIA diagnostic" if quality_status == "insufficient_evidence" else "JIMMORIA response"
        preview_title = "Diagnostic preview" if quality_status == "insufficient_evidence" else "Report preview"
        self.block(summary_title, lines)
        if report_path:
            if self.should_print_full_report(quality_status):
                self.print_report_full(report_path)
            else:
                self.print_report_preview(report_path, title=preview_title)

    def print_context(self) -> None:
        memory = load_memory(self.memory_path)
        runs = list_run_summaries(self.runs_dir)
        lines = [
            f"Memory file: {self.memory_path}",
            f"Sources: {len(memory.sources)}",
            f"Projects: {len(memory.projects)}",
            f"Findings: {len(memory.findings)}",
            f"Runs: {len(runs)}",
        ]
        if runs:
            latest = runs[0]
            lines.extend(
                [
                    "",
                    f"Latest room: {latest.get('room_id')}",
                    f"Latest topic: {latest.get('topic')}",
                    f"Latest status: {latest.get('status')}",
                ]
            )
        self.block("Shared context", lines)

    def print_latest_run_card(self, room_id: str | None = None) -> None:
        selected_room_id = room_id or self.last_room_id
        if not selected_room_id:
            runs = list_run_summaries(self.runs_dir)
            selected_room_id = str(runs[0]["room_id"]) if runs else ""
        if not selected_room_id:
            self.block("Latest run", ["No runs found."])
            return
        room = load_run_file(selected_room_id, "room.json", self.runs_dir)
        assert isinstance(room, dict)
        messages = load_run_file(selected_room_id, "messages.json", self.runs_dir)
        audit = load_run_file(selected_room_id, "tool_audit_log.json", self.runs_dir)
        events = load_run_file(selected_room_id, "events.json", self.runs_dir)
        assert isinstance(messages, list)
        assert isinstance(audit, list)
        assert isinstance(events, list)
        unconfigured = sum(1 for item in audit if item.get("status") == "unconfigured")
        output_paths = room.get("output_paths") if isinstance(room.get("output_paths"), dict) else {}
        assert isinstance(output_paths, dict)
        project_card = room.get("project_card") if isinstance(room.get("project_card"), dict) else {}
        runtime_metrics = project_card.get("runtime_metrics") if isinstance(project_card, dict) else {}
        metric_lines = self.runtime_metric_lines(runtime_metrics) if isinstance(runtime_metrics, dict) else []
        self.block(
            "Latest run",
            [
                f"Room: {room.get('room_id')}",
                f"Topic: {room.get('topic')}",
                f"Status: {room.get('status')}",
                f"Messages: {len(messages)}",
                f"Events: {len(events)}",
                *metric_lines,
                f"Unconfigured tool calls: {unconfigured}",
                f"Report: {output_paths.get('report', '')}",
                f"Evidence packet: {output_paths.get('evidence_packet', '')}",
                f"Vault: {output_paths.get('obsidian_vault', '')}",
            ],
        )

    def print_workboard(self, *, limit: int = 8) -> None:
        runs = list_run_summaries(self.runs_dir)[:limit]
        if not runs:
            self.block("Workload board", ["No saved rooms found."])
            return

        rows: list[dict[str, str]] = []
        for item in runs:
            room_id = str(item.get("room_id", ""))
            status = str(item.get("status", ""))
            topic = self.compact_text(str(item.get("topic", "")), 46)
            report = "report" if item.get("report") else "-"
            quality = ""
            progress = "-"
            latest = "-"
            try:
                room = load_run_file(room_id, "room.json", self.runs_dir)
                events = load_run_file(room_id, "events.json", self.runs_dir)
                assert isinstance(room, dict)
                assert isinstance(events, list)
                quality = self.room_quality_label(room)
                progress = self.progress_from_events(events)
                latest = self.latest_work_from_events(events)
            except (FileNotFoundError, AssertionError, OSError, ValueError):
                latest = "snapshot incomplete"
            rows.append(
                {
                    "state": self.room_state_label(status),
                    "room": self.short_id(room_id),
                    "topic": topic,
                    "progress": progress,
                    "quality": quality or "-",
                    "latest": self.compact_text(latest, 58),
                    "report": report,
                }
            )

        if self.use_rich:
            self.rich_workboard(rows)
            return

        lines: list[str] = []
        for row in rows:
            lines.append(
                f"{row['state']:<5} {row['room']:<18} {row['progress']:<22} "
                f"{row['quality']:<22} {row['report']}"
            )
            lines.append(f"      {row['topic']}")
            lines.append(f"      latest: {row['latest']}")
        self.block("Workload board", lines)

    def room_state_label(self, status: str) -> str:
        normalized = status.lower()
        if normalized in {"completed", "done"}:
            return "DONE"
        if normalized in {"failed", "error"}:
            return "FAIL"
        if normalized in {"running", "assigned", "waiting_for_tool", "ready_for_report", "writing_report", "obsidian_syncing"}:
            return "RUN"
        if normalized in {"created"}:
            return "NEW"
        return (status.upper() or "ROOM")[:5]

    def room_quality_label(self, room: dict[str, object]) -> str:
        project_card = room.get("project_card") if isinstance(room.get("project_card"), dict) else {}
        assert isinstance(project_card, dict)
        direct = project_card.get("research_quality_status")
        if direct:
            return str(direct)
        quality = project_card.get("research_quality") if isinstance(project_card.get("research_quality"), dict) else {}
        assert isinstance(quality, dict)
        return str(quality.get("status") or "")

    def progress_from_events(self, events: list[object]) -> str:
        states: dict[str, str] = {}
        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type", ""))
            if event_type == "room_created":
                for agent_id in event.get("agents", []) or []:
                    states[str(agent_id)] = "queued"
            elif event_type == "agent_start":
                states[str(event.get("agent_id", ""))] = "running"
            elif event_type == "agent_done":
                states[str(event.get("agent_id", ""))] = "done"
            elif event_type == "agent_failed":
                states[str(event.get("agent_id", ""))] = "failed"
        if not states:
            return "-"
        failed = sum(1 for value in states.values() if value == "failed")
        running = sum(1 for value in states.values() if value == "running")
        done = sum(1 for value in states.values() if value == "done")
        queued = sum(1 for value in states.values() if value == "queued")
        parts: list[str] = []
        if failed:
            parts.append(f"{failed} fail")
        if running:
            parts.append(f"{running} run")
        if queued:
            parts.append(f"{queued} wait")
        parts.append(f"{done} done")
        return "/".join(parts)

    def latest_work_from_events(self, events: list[object]) -> str:
        for event in reversed(events):
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type", ""))
            if event_type == "agent_start":
                agent_id = str(event.get("agent_id", ""))
                return f"{agent_id}: {AGENT_ACTIVITY.get(agent_id, 'running')}"
            if event_type == "agent_done":
                return f"{event.get('agent_id', '')}: {event.get('summary', 'done')}"
            if event_type in {"tool_start", "tool_done", "tool_failed", "tool_denied", "tool_unconfigured"}:
                return f"{event.get('agent_id', '')} -> {event.get('tool_name', '')}: {event.get('summary') or event_type}"
            if event_type == "orchestration_plan":
                return f"supervisor: {event.get('summary', 'orchestration plan')}"
            if event_type == "report_written":
                return f"report: {event.get('summary', 'written')}"
            if event_type == "room_completed":
                return f"room completed: {event.get('status', '')}"
            if event_type == "room_failed":
                return f"room failed: {event.get('summary', '')}"
        return "-"

    def short_id(self, value: str, *, head: int = 12, tail: int = 4) -> str:
        if len(value) <= head + tail + 3:
            return value
        return f"{value[:head]}...{value[-tail:]}"

    def print_agent_state(self) -> None:
        if not self.agent_state:
            return
        rows: list[tuple[str, str, str]] = []
        for agent_id in DEFAULT_AGENTS:
            if agent_id not in self.agent_state:
                continue
            state = self.agent_state[agent_id]
            activity = self.agent_activity.get(agent_id) or AGENT_ACTIVITY.get(agent_id, "")
            rows.append((state, agent_id, self.activity_label(state, activity)))
        if self.use_rich:
            self.rich_agent_board(rows)
            return
        self.block(
            "Live agent board",
            [f"{self.state_label(state):<8} {agent_id:<28} {activity}" for state, agent_id, activity in rows],
        )

    def print_tool_event(self, event_type: str, event: dict[str, object]) -> None:
        marker = {
            "tool_start": "RUN",
            "tool_done": "DONE",
            "tool_failed": "FAIL",
            "tool_denied": "DENY",
            "tool_unconfigured": "WAIT",
        }.get(event_type, "TOOL")
        if self.use_stream_events():
            summary = self.compact_text(str(event.get("summary") or event.get("input_preview") or ""), 82)
            latency = f" | {event.get('latency_ms')}ms" if event.get("latency_ms") is not None else ""
            suffix = f" | {summary}" if summary else ""
            self.print_event_line("Tool", f"{marker} {event.get('agent_id')} -> {event.get('tool_name')}{suffix}{latency}")
            return
        lines = [
            f"[TOOL] {event.get('agent_id')} -> {event.get('tool_name')} [{marker}]",
        ]
        if event.get("summary"):
            lines.append(f"Summary: {event.get('summary')}")
        if event.get("latency_ms") is not None:
            lines.append(f"Latency: {event.get('latency_ms')}ms")
        if event.get("input_preview") and event_type == "tool_start":
            lines.append(f"Input: {event.get('input_preview')}")
        self.block("Tool activity", lines)

    def update_tool_activity(self, event_type: str, event: dict[str, object]) -> None:
        agent_id = str(event.get("agent_id") or "")
        if not agent_id:
            return
        tool_name = str(event.get("tool_name") or "tool")
        marker = {
            "tool_start": "Tool running",
            "tool_done": "Tool done",
            "tool_failed": "Tool failed",
            "tool_denied": "Tool denied",
            "tool_unconfigured": "Tool waiting",
        }.get(event_type, "Tool")
        detail = str(event.get("summary") or event.get("input_preview") or tool_name)
        self.agent_activity[agent_id] = f"{marker}: {tool_name} - {detail}"
        if self.agent_state.get(agent_id) not in {"done", "failed"}:
            self.agent_state[agent_id] = "running"

    def print_output_event(self, event_type: str, event: dict[str, object]) -> None:
        labels = {
            "finding_saved": "Finding saved",
            "source_saved": "Source saved",
            "report_written": "Report written",
            "note_written": "Vault note written",
        }
        if event_type == "report_written" and event.get("quality_status") == "insufficient_evidence":
            labels = {**labels, "report_written": "Research gate"}
        summary = str(event.get("summary") or labels.get(event_type, event_type))
        if event.get("path"):
            summary = f"{summary}"
        if self.use_stream_events():
            self.print_event_line("Output", f"{labels.get(event_type, event_type)} | {self.compact_text(summary, 88)}")
            return
        self.block(
            labels.get(event_type, event_type),
            [
                f"Agent: {event.get('agent_id')}",
                summary,
            ],
        )

    def event_metrics_suffix(self, event: dict[str, object]) -> str:
        parts = self.event_metric_parts(event)
        return " | " + " | ".join(parts) if parts else ""

    def event_metric_lines(self, event: dict[str, object]) -> list[str]:
        parts = self.event_metric_parts(event)
        return [f"Metrics: {' | '.join(parts)}"] if parts else []

    def event_metric_parts(self, event: dict[str, object]) -> list[str]:
        parts: list[str] = []
        duration = format_duration_ms(event.get("duration_ms"))
        if duration:
            parts.append(f"time {duration}")
        usage = event.get("llm_usage")
        if isinstance(usage, dict):
            usage_text = format_llm_usage(usage)
            if usage_text:
                parts.append(usage_text)
        return parts

    def runtime_metric_lines(self, metrics: dict[str, object]) -> list[str]:
        lines: list[str] = []
        duration = format_duration_ms(metrics.get("duration_ms"))
        if duration:
            lines.append(f"Runtime: {duration}")
        usage = metrics.get("llm_usage")
        if isinstance(usage, dict):
            usage_text = format_llm_usage(usage)
            if usage_text:
                lines.append(f"LLM: {usage_text}")
        return lines

    def state_label(self, state: str) -> str:
        labels = {
            "queued": "WAIT",
            "running": "RUN",
            "done": "DONE",
            "failed": "FAIL",
        }
        return labels.get(state, state.upper()[:8])

    def activity_label(self, state: str, activity: str) -> str:
        prefix = {
            "queued": "Waiting",
            "running": "Now",
            "done": "Finished",
            "failed": "Stopped",
        }.get(state, "Status")
        compact = " ".join(str(activity).split())
        if len(compact) > 64:
            compact = compact[:61].rstrip() + "..."
        return f"{prefix}: {compact}"

    def print_report_preview(self, report_path: str | Path, *, max_lines: int = 12, title: str = "Report preview") -> None:
        path = Path(report_path)
        if not path.exists():
            return
        lines = path.read_text(encoding="utf-8").splitlines()
        preview = [line for line in lines if line.strip()][:max_lines]
        self.block(title, preview)

    def print_report_full(self, report_path: str | Path) -> None:
        path = Path(report_path)
        if not path.exists():
            return
        lines = path.read_text(encoding="utf-8").splitlines()
        self.block("Full report", lines)

    def should_print_full_report(self, quality_status: str) -> bool:
        mode = os.getenv("JIMMORIA_REPORT_DISPLAY", "").strip().lower()
        if mode in {"preview", "summary"}:
            return False
        if mode in {"full", "all"}:
            return True
        return quality_status != "insufficient_evidence"

    def agent_label(self, agent_id: str) -> str:
        spec = self.registry.get(agent_id)
        if not spec:
            return agent_id
        if spec.persona_name:
            return f"{spec.persona_name} ({agent_id})"
        return agent_id

    def block(self, title: str, lines: list[str]) -> None:
        if self.use_rich:
            self.rich_block(title, lines)
            return
        print("")
        print(f"[{title}]")
        for line in lines:
            if not line:
                print("")
                continue
            for wrapped in self.wrap(line):
                print(f"  {wrapped}")

    def wrap(self, text: str) -> list[str]:
        width = max(40, self.width - 4)
        return textwrap.wrap(text, width=width, replace_whitespace=False) or [""]

    def compact_text(self, text: str, max_length: int = 88) -> str:
        compact = " ".join(str(text).split())
        if len(compact) <= max_length:
            return compact
        return compact[: max_length - 3].rstrip() + "..."

    def rule(self, char: str = "-") -> None:
        print(char * self.width)

    def rich_console(self) -> Any:
        assert RichConsole is not None
        return RichConsole(
            file=sys.stdout,
            width=self.width,
            force_terminal=supports_color(),
            color_system="truecolor" if supports_color() else None,
        )

    def rich_block(self, title: str, lines: list[str]) -> None:
        assert Panel is not None
        assert Text is not None
        console = self.rich_console()
        body = Text()
        for line in lines:
            body.append(str(line))
            body.append("\n")
        if lines:
            body = Text(body.plain.rstrip("\n"))
        console.print("")
        console.print(
            Panel(
                body,
                title=f"[bold bright_magenta]{title}",
                border_style="rgb(211,95,255)",
                padding=(0, 1),
                box=box.ROUNDED if box is not None else None,
            )
        )

    def rich_agent_board(self, rows: list[tuple[str, str, str]]) -> None:
        assert Panel is not None
        assert Table is not None
        console = self.rich_console()
        table = Table.grid(expand=True)
        table.add_column("State", width=8)
        table.add_column("Agent", width=28)
        table.add_column("Current work", ratio=1)
        table.add_row(
            Text("STATE", style="bold rgb(255,92,212)"),
            Text("AGENT", style="bold rgb(255,92,212)"),
            Text("CURRENT WORK", style="bold rgb(255,92,212)"),
        )
        for state, agent_id, activity in rows:
            table.add_row(
                self.rich_state_badge(state),
                Text(agent_id, style="rgb(230,214,255)"),
                Text(activity, style=self.rich_activity_style(state)),
            )
        console.print("")
        console.print(
            Panel(
                table,
                title="[bold bright_magenta]Live agent board",
                subtitle="[rgb(126,96,154)]JIMMORIA runtime",
                border_style="rgb(255,79,216)",
                padding=(0, 1),
                box=box.ROUNDED if box is not None else None,
            )
        )

    def rich_workboard(self, rows: list[dict[str, str]]) -> None:
        assert Panel is not None
        assert Table is not None
        console = self.rich_console()
        table = Table.grid(expand=True)
        table.add_column("State", width=7)
        table.add_column("Room", width=18)
        table.add_column("Progress", width=22)
        table.add_column("Quality", width=22)
        table.add_column("Topic / latest", ratio=1)
        table.add_row(
            Text("STATE", style="bold rgb(255,92,212)"),
            Text("ROOM", style="bold rgb(255,92,212)"),
            Text("PROGRESS", style="bold rgb(255,92,212)"),
            Text("QUALITY", style="bold rgb(255,92,212)"),
            Text("TOPIC / LATEST", style="bold rgb(255,92,212)"),
        )
        for row in rows:
            work = Text()
            work.append(row["topic"], style="rgb(230,214,255)")
            work.append("\nlatest: ", style="rgb(126,96,154)")
            work.append(row["latest"], style="rgb(230,214,255)")
            work.append("\nartifact: ", style="rgb(126,96,154)")
            work.append(row["report"], style="rgb(190,162,235)")
            table.add_row(
                Text(row["state"], style=self.rich_room_state_style(row["state"])),
                Text(row["room"], style="rgb(230,214,255)"),
                Text(row["progress"], style="rgb(190,162,235)"),
                Text(row["quality"], style="rgb(160,132,188)"),
                work,
            )
        console.print("")
        console.print(
            Panel(
                table,
                title="[bold bright_magenta]Workload board",
                subtitle="[rgb(126,96,154)]multi-room operations",
                border_style="rgb(255,79,216)",
                padding=(0, 1),
                box=box.ROUNDED if box is not None else None,
            )
        )

    def rich_room_state_style(self, state: str) -> str:
        styles = {
            "DONE": "bold rgb(120,255,190)",
            "RUN": "bold rgb(255,210,245)",
            "FAIL": "bold rgb(255,92,120)",
            "NEW": "rgb(190,162,235)",
        }
        return styles.get(state, "rgb(230,214,255)")

    def rich_state_badge(self, state: str) -> Any:
        assert Text is not None
        styles = {
            "queued": "rgb(126,96,154)",
            "running": "bold rgb(255,79,216)",
            "done": "bold rgb(120,255,190)",
            "failed": "bold rgb(255,92,120)",
        }
        return Text(self.state_label(state), style=styles.get(state, "rgb(230,214,255)"))

    def rich_activity_style(self, state: str) -> str:
        styles = {
            "queued": "rgb(160,132,188)",
            "running": "rgb(255,210,245)",
            "done": "rgb(190,255,220)",
            "failed": "rgb(255,170,185)",
        }
        return styles.get(state, "rgb(230,214,255)")


def print_jimmoria_logo(width: int = 100) -> None:
    width = max(64, min(width, 110))
    if supports_color():
        print_color_hero(width)
    else:
        print_plain_hero(width)


def print_color_hero(width: int) -> None:
    reset = "\033[0m"
    bold = "\033[1m"
    dim = "\033[2m"
    violet = "\033[38;2;181;92;255m"
    pink = "\033[38;2;255;79;216m"
    silver = "\033[38;2;230;214;255m"
    muted = "\033[38;2;126;96;154m"
    line = "=" * width
    logo = jimmoria_3d_logo_layers()
    subtitle = "Multi-agent crypto research company"
    workflow = "research rooms  /  agent bus  /  obsidian memory"

    print(f"{violet}{line}{reset}")
    print("")
    for row_index, (row, layers) in enumerate(logo):
        print(center_ansi(style_logo_layer_line(row, layers, row_index), width))
    print("")
    print(center_ansi(f"{bold}{pink}JIMMORIA v{__version__}{reset}", width))
    print(center_ansi(f"{silver}{subtitle}{reset}", width))
    print(center_ansi(f"{dim}{muted}{workflow}{reset}", width))
    print(f"{pink}{line}{reset}")


def print_plain_hero(width: int) -> None:
    line = "=" * width
    print(line)
    print("")
    for row in jimmoria_block_logo():
        print(center_text(row, width))
    print("")
    print(center_text(f"JIMMORIA v{__version__}", width))
    print(center_text("Multi-agent crypto research company", width))
    print(center_text("research rooms  /  agent bus  /  obsidian memory", width))
    print(line)


def jimmoria_block_logo() -> list[str]:
    return render_block_text("JIMMORIA")


def jimmoria_3d_logo_layers() -> list[tuple[str, str]]:
    rows = jimmoria_block_logo()
    canvas_width = max(len(row) for row in rows) + 2
    canvas_height = len(rows) + 1
    chars = [[" "] * canvas_width for _ in range(canvas_height)]
    layers = [[" "] * canvas_width for _ in range(canvas_height)]

    for row_index, row in enumerate(rows):
        for column_index, char in enumerate(row):
            if char == " ":
                continue
            shadow_column = column_index + 2
            if shadow_column < canvas_width:
                chars[row_index + 1][shadow_column] = char
                layers[row_index + 1][shadow_column] = "S"

    for row_index, row in enumerate(rows):
        for column_index, char in enumerate(row):
            if char == " ":
                continue
            chars[row_index][column_index] = char
            layers[row_index][column_index] = "F"

    output: list[tuple[str, str]] = []
    for char_row, layer_row in zip(chars, layers, strict=True):
        text = "".join(char_row).rstrip()
        layer_text = "".join(layer_row)[: len(text)]
        output.append((text, layer_text))
    return output


def style_logo_layer_line(text: str, layers: str, row_index: int) -> str:
    reset = "\033[0m"
    front_palette = [
        "\033[38;2;255;132;235m",
        "\033[38;2;240;98;232m",
        "\033[38;2;224;86;244m",
        "\033[38;2;202;95;255m",
        "\033[38;2;183;89;255m",
        "\033[38;2;215;83;245m",
        "\033[38;2;255;96;213m",
        "\033[38;2;116;51;180m",
    ]
    shadow = "\033[38;2;90;38;137m"
    front = "\033[1m" + front_palette[row_index % len(front_palette)]
    styled = []
    for char, layer in zip(text, layers, strict=True):
        if layer == "F":
            styled.append(f"{front}{char}{reset}")
        elif layer == "S":
            styled.append(f"{shadow}{char}{reset}")
        else:
            styled.append(char)
    return "".join(styled)


def render_block_text(text: str) -> list[str]:
    letters = {
        "A": [" ### ", "#   #", "#   #", "#####", "#   #", "#   #", "#   #"],
        "I": ["#####", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ", "#####"],
        "J": ["#####", "   # ", "   # ", "   # ", "#  # ", "#  # ", " ##  "],
        "M": ["#   #", "## ##", "# # #", "#   #", "#   #", "#   #", "#   #"],
        "O": [" ### ", "#   #", "#   #", "#   #", "#   #", "#   #", " ### "],
        "R": ["#### ", "#   #", "#   #", "#### ", "# #  ", "#  # ", "#   #"],
    }
    fill = block_fill()
    rows = [""] * 7
    for char in text.upper():
        glyph = letters.get(char)
        if glyph is None:
            glyph = ["     "] * 7
        for index, row in enumerate(glyph):
            rows[index] += expand_block_row(row, fill) + "  "
    return [row.rstrip() for row in rows]


def expand_block_row(row: str, fill: str) -> str:
    empty = " " * len(fill)
    return "".join(fill if char == "#" else empty for char in row)


def block_fill() -> str:
    return "██" if can_encode("█") else "##"


def can_encode(text: str) -> bool:
    encoding = sys.stdout.encoding or "utf-8"
    try:
        text.encode(encoding)
    except UnicodeEncodeError:
        return False
    return True


def supports_color() -> bool:
    if os.getenv("NO_COLOR"):
        return False
    if not sys.stdout.isatty() and not os.getenv("JIMMORIA_FORCE_COLOR"):
        return False
    if os.name == "nt" and not enable_windows_ansi():
        return False
    return True


def enable_windows_ansi() -> bool:
    try:
        import ctypes
    except ImportError:
        return False

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(-11)
    if handle in (-1, 0):
        return False
    mode = ctypes.c_uint32()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return False
    enable_virtual_terminal_processing = 0x0004
    if mode.value & enable_virtual_terminal_processing:
        return True
    return bool(kernel32.SetConsoleMode(handle, mode.value | enable_virtual_terminal_processing))


def center_text(text: str, width: int) -> str:
    return text.center(width)


def center_ansi(text: str, width: int) -> str:
    return " " * max((width - visible_len(text)) // 2, 0) + text


def visible_len(text: str) -> int:
    length = 0
    in_escape = False
    for char in text:
        if char == "\033":
            in_escape = True
            continue
        if in_escape:
            if char == "m":
                in_escape = False
            continue
        length += 1
    return length


def format_duration_ms(value: object) -> str:
    try:
        duration_ms = int(value or 0)
    except (TypeError, ValueError):
        return ""
    if duration_ms <= 0:
        return ""
    if duration_ms < 1000:
        return f"{duration_ms}ms"
    seconds = duration_ms / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    rest = int(seconds % 60)
    return f"{minutes}m {rest}s"


def format_llm_usage(usage: dict[str, object]) -> str:
    calls = _safe_int(usage.get("calls"))
    total_tokens = _safe_int(usage.get("total_tokens"))
    estimated = bool(usage.get("estimated"))
    if calls <= 0 and total_tokens <= 0:
        return ""
    call_text = f"{calls} call" + ("" if calls == 1 else "s")
    token_text = format_token_count(total_tokens, estimated=estimated) if total_tokens else "tokens n/a"
    return f"llm {call_text} / {token_text}"


def format_token_count(total_tokens: int, *, estimated: bool = False) -> str:
    prefix = "~" if estimated else ""
    if total_tokens >= 1_000_000:
        return f"{prefix}{total_tokens / 1_000_000:.1f}m tokens"
    if total_tokens >= 1000:
        return f"{prefix}{total_tokens / 1000:.1f}k tokens"
    return f"{prefix}{total_tokens} tokens"


def _safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
