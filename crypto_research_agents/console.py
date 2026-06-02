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
        self.last_room_id = ""
        self.width = min(shutil.get_terminal_size((100, 30)).columns, 110)

    def print_intro(self) -> None:
        print_jimmoria_logo(self.width)

    def print_help(self) -> None:
        lines = [
            "Type a research question, pasted source, or URL to open a Research Room.",
            "",
            "Commands:",
            "  /add <text-or-url>       Ingest source only",
            "  /models                  Configure LLM provider/models",
            "  /doctor                  Show configured vs placeholder capabilities",
            "  /company                 Show active and planned agents",
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

    def print_user_message(self, text: str) -> None:
        self.block("You", self.wrap(text))

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
            label = self.agent_label(agent_id)
            self.block(
                f"{label} started",
                [
                    f"Task type: {event.get('task_type')}",
                    "Status: running",
                ],
            )
            return

        if event_type == "agent_done":
            agent_id = str(event.get("agent_id", ""))
            self.agent_state[agent_id] = "done"
            label = self.agent_label(agent_id)
            summary = str(event.get("summary", ""))
            self.block(
                f"{label} finished",
                [
                    f"Summary: {summary}",
                    f"Messages: {event.get('messages')}",
                    f"Findings: {event.get('findings')}",
                ],
            )
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
        rows = []
        for agent_id in DEFAULT_AGENTS:
            if agent_id not in self.agent_state:
                continue
            rows.append(f"{agent_id:<28} {self.agent_state[agent_id]}")
        self.block("Agent board", rows)

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
    cyan = "\033[38;2;84;202;255m"
    blue = "\033[38;2;44;118;255m"
    green = "\033[38;2;76;255;158m"
    muted = "\033[38;2;145;157;183m"
    line = "=" * width
    mark = block_mark(block_char())
    subtitle = "Multi-agent crypto research company"

    print(f"{blue}{line}{reset}")
    print("")
    for row in mark:
        print(center_ansi(f"{bold}{cyan}{row}{reset}", width))
    print("")
    print(center_ansi(f"{bold}{green}JIMMORIA v{__version__}{reset}", width))
    print(center_ansi(f"{muted}{subtitle}{reset}", width))
    print(center_ansi(f"{dim}{muted}Research rooms. Agent bus. Obsidian memory.{reset}", width))
    print(f"{blue}{line}{reset}")


def print_plain_hero(width: int) -> None:
    line = "=" * width
    print(line)
    print("")
    for row in block_mark(block_char()):
        print(center_text(row, width))
    print("")
    print(center_text(f"JIMMORIA v{__version__}", width))
    print(center_text("Multi-agent crypto research company", width))
    print(center_text("Research rooms. Agent bus. Obsidian memory.", width))
    print(line)


def supports_color() -> bool:
    if os.getenv("NO_COLOR"):
        return False
    if not sys.stdout.isatty() and not os.getenv("JIMMORIA_FORCE_COLOR"):
        return False
    if os.name == "nt" and not enable_windows_ansi():
        return False
    return True


def block_mark(fill: str) -> list[str]:
    return [
        f"{fill * 6}    {fill * 3}    {fill * 3}   {fill * 6}",
        f"  {fill * 2}      {fill * 4}  {fill * 4}   {fill * 2}   {fill * 2}",
        f"  {fill * 2}      {fill * 2} {fill * 4} {fill * 2}   {fill * 6}",
        f"{fill * 2} {fill * 2}     {fill * 2}  {fill * 2}  {fill * 2}   {fill * 2}  {fill * 2}",
        f" {fill * 3}      {fill * 2}      {fill * 2}   {fill * 2}   {fill * 2}",
    ]


def block_char() -> str:
    return "█" if can_encode("█") else "#"


def can_encode(text: str) -> bool:
    encoding = sys.stdout.encoding or "utf-8"
    try:
        text.encode(encoding)
    except UnicodeEncodeError:
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
