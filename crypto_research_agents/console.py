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
    "narrative_agent": "Mapping narratives",
    "discovery_agent": "Resolving candidates",
    "social_kol_agent": "Checking social signal",
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
        self.memory_path = Path(memory_path)
        self.runs_dir = Path(runs_dir)
        self.agent_state: dict[str, str] = {}
        self.agent_activity: dict[str, str] = {}
        self.last_room_id = ""
        self.width = min(shutil.get_terminal_size((100, 30)).columns, 110)
        self.use_rich = RichConsole is not None and not os.getenv("JIMMORIA_PLAIN_LOGS")

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
        self.block("You", self.wrap(text))

    def read_chat_input(self) -> str:
        if not sys.stdin.isatty():
            return input(f"\n{APP_NAME.lower()}> ")

        if supports_color():
            return self.read_ansi_boxed_input()
        return self.read_basic_boxed_input()

    def read_ansi_boxed_input(self) -> str:
        hint = "Type a request, URL, /command, or @path/to/file"
        border = self.input_border()
        print("")
        print(self.input_border_style(border))
        print(self.input_border_style(self.input_hint_line(hint)))
        print(self.input_edit_line_style())
        print(self.input_border_style(border))
        try:
            return input(self.input_cursor_sequence())
        finally:
            sys.stdout.write("\033[1B\r")
            sys.stdout.flush()

    def read_basic_boxed_input(self) -> str:
        hint = "Type a request, URL, /command, or @path/to/file"
        border = self.input_border()
        print("")
        print(border)
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

    def input_hint_line(self, text: str) -> str:
        inner_width = self.input_box_width() - 4
        clipped = text[:inner_width]
        return "| " + clipped.ljust(inner_width) + " |"

    def input_edit_line(self) -> str:
        inner_width = self.input_box_width() - 4
        return "| " + "> ".ljust(inner_width) + " |"

    def input_border_style(self, text: str) -> str:
        if not supports_color():
            return text
        return f"\033[38;2;211;95;255m{text}\033[0m"

    def input_edit_line_style(self) -> str:
        violet = "\033[38;2;211;95;255m"
        pink = "\033[38;2;255;92;212m"
        reset = "\033[0m"
        inner_width = self.input_box_width() - 4
        padding = " " * max(inner_width - 2, 0)
        return f"{violet}|{reset} {pink}>{reset} {padding}{violet}|{reset}"

    def input_cursor_sequence(self) -> str:
        return "\033[2A\033[4C"

    def make_event_handler(self) -> Any:
        def handle(event: dict[str, object]) -> None:
            self.handle_event(event)

        return handle

    def handle_event(self, event: dict[str, object]) -> None:
        event_type = str(event.get("type", ""))
        if event_type == "room_created":
            self.last_room_id = str(event.get("room_id", ""))
            self.agent_state = {
                str(agent_id): "queued"
                for agent_id in event.get("agents", [])
            }
            self.agent_activity = {
                str(agent_id): AGENT_ACTIVITY.get(str(agent_id), "Waiting for assignment")
                for agent_id in event.get("agents", [])
            }
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
            label = self.agent_label(agent_id)
            summary = str(event.get("summary", ""))
            self.block(
                f"{label} finished",
                [
                    f"State: {self.state_label('done')}",
                    f"Finished: {summary}",
                    f"Messages: {event.get('messages')}",
                    f"Findings: {event.get('findings')}",
                ],
            )
            return

        if event_type == "agent_failed":
            agent_id = str(event.get("agent_id", ""))
            self.agent_state[agent_id] = "failed"
            self.agent_activity[agent_id] = f"Failed: {event.get('error')}"
            label = self.agent_label(agent_id)
            self.block(
                f"{label} failed",
                [
                    f"State: {self.state_label('failed')}",
                    f"Task type: {event.get('task_type')}",
                    f"Stopped: {event.get('error')}",
                ],
            )
            self.print_agent_state()
            return

        if event_type in {"tool_start", "tool_done", "tool_failed", "tool_denied", "tool_unconfigured"}:
            self.print_tool_event(event_type, event)
            return

        if event_type in {"finding_saved", "source_saved", "report_written", "note_written"}:
            self.print_output_event(event_type, event)
            return

        if event_type == "room_completed":
            self.block(
                "JIMMORIA finalizes the room",
                [
                    f"Room: {event.get('room_id')}",
                    f"Status: {event.get('status')}",
                    f"Messages: {event.get('messages')}",
                    f"Findings: {event.get('findings')}",
                ],
            )
            self.print_agent_state()
            return

        if event_type == "room_failed":
            self.block(
                "JIMMORIA room failed",
                [
                    f"Room: {event.get('room_id')}",
                    f"Status: {event.get('status')}",
                    f"Reason: {event.get('summary')}",
                ],
            )
            self.print_agent_state()

    def print_run_summary(self, result: object) -> None:
        room = result.room
        memory = result.memory
        bus = result.bus
        report_path = room.output_paths.get("report", "")
        vault_path = room.output_paths.get("obsidian_vault", "")
        lines = [
            f"Room: {room.room_id}",
            f"Status: {room.status}",
            f"Messages: {len(bus.messages)}",
            f"Findings: {len(memory.get_room_findings(room.room_id))}",
        ]
        if report_path:
            lines.append(f"Report: {report_path}")
        if vault_path:
            lines.append(f"Vault: {vault_path}")
        lines.append(f"Replay events: {self.runs_dir / room.room_id / 'events.json'}")
        self.block("JIMMORIA response", lines)
        if report_path:
            self.print_report_preview(report_path)

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
        self.block(
            "Latest run",
            [
                f"Room: {room.get('room_id')}",
                f"Topic: {room.get('topic')}",
                f"Status: {room.get('status')}",
                f"Messages: {len(messages)}",
                f"Events: {len(events)}",
                f"Unconfigured tool calls: {unconfigured}",
                f"Report: {output_paths.get('report', '')}",
                f"Vault: {output_paths.get('obsidian_vault', '')}",
            ],
        )

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

    def print_output_event(self, event_type: str, event: dict[str, object]) -> None:
        labels = {
            "finding_saved": "Finding saved",
            "source_saved": "Source saved",
            "report_written": "Report written",
            "note_written": "Vault note written",
        }
        summary = str(event.get("summary") or labels.get(event_type, event_type))
        if event.get("path"):
            summary = f"{summary}"
        self.block(
            labels.get(event_type, event_type),
            [
                f"Agent: {event.get('agent_id')}",
                summary,
            ],
        )

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

    def print_report_preview(self, report_path: str | Path, *, max_lines: int = 12) -> None:
        path = Path(report_path)
        if not path.exists():
            return
        lines = path.read_text(encoding="utf-8").splitlines()
        preview = [line for line in lines if line.strip()][:max_lines]
        self.block("Report preview", preview)

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
