from __future__ import annotations

import ast
import os
import re
import shutil
import sys
import unicodedata
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
    "supervisor_agent": "리서치 방향과 작업 순서를 정리하는 중",
    "ingestion_agent": "입력 소스와 메타데이터를 정리하는 중",
    "social_kol_agent": "X/KOL/공개 소셜 신호를 모으는 중",
    "narrative_agent": "내러티브와 투자 논리를 매핑하는 중",
    "discovery_agent": "공식 후보와 프로젝트 정체성을 찾는 중",
    "contract_onchain_agent": "체인·토큰·컨트랙트 식별 정보를 확인하는 중",
    "product_tech_agent": "웹사이트·문서·GitHub 제품 근거를 확인하는 중",
    "funding_token_agent": "펀딩·포인트·토큰 단서를 검증하는 중",
    "report_agent": "고객용 리서치 리포트를 작성하는 중",
    "obsidian_curator_agent": "볼트 노트와 지식 기록을 동기화하는 중",
}

AGENT_DISPLAY_NAMES = {
    "supervisor_agent": "슈퍼바이저",
    "ingestion_agent": "아카이비스트",
    "social_kol_agent": "소셜/KOL",
    "narrative_agent": "내러티브",
    "discovery_agent": "스카우터",
    "contract_onchain_agent": "온체인",
    "product_tech_agent": "제품/기술",
    "funding_token_agent": "펀딩/토큰",
    "report_agent": "리포트",
    "obsidian_curator_agent": "옵시디언",
}

STATE_LABELS_KO = {
    "queued": "대기",
    "running": "진행",
    "done": "완료",
    "failed": "실패",
}

INTERNAL_RUNTIME_TOOLS = {
    "create_task",
    "assign_task",
    "agent_handoff",
    "update_task_status",
    "read_agent_status",
}

INTERNAL_TOOL_ACTIVITY = {
    "create_task": "작업 카드를 정리하는 중",
    "assign_task": "담당 에이전트에게 작업을 배정하는 중",
    "agent_handoff": "다음 에이전트로 인수인계하는 중",
    "update_task_status": "작업 상태를 정리하는 중",
    "read_agent_status": "에이전트 상태를 확인하는 중",
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
        terminal_width = shutil.get_terminal_size((136, 30)).columns
        self.width = safe_terminal_width(terminal_width)
        self.use_rich = RichConsole is not None and rich_blocks_enabled()
        self.event_style = default_event_style()
        self.runtime_room_running = False
        self.runtime_dock_lines = 0
        self.runtime_dock_frame = 0
        self.last_runtime_metrics: dict[str, object] = {}
        self.council_state = "idle"
        self.council_activity = "토론방 대기 중"
        self.council_participants: list[str] = []

    def print_intro(self) -> None:
        print_jimmoria_logo(self.width)

    def print_help(self) -> None:
        command_rows = [
            ("/add <text-or-url>", "소스만 저장"),
            ("/models", "LLM provider/model 설정"),
            ("/doctor", "연결 가능한 도구와 placeholder 상태 확인"),
            ("/company", "활성/예정 에이전트 보기"),
            ("/settings", "회사 운영 설정 보기"),
            ("/board", "현재 에이전트 작업 보드 보기"),
            ("/context", "공유 메모리와 최근 실행 컨텍스트 보기"),
            ("/rooms", "여러 리서치룸 워크로드 보기"),
            ("/runs", "이전 실행 기록 보기"),
            ("/status [room_id]", "최신/선택 룸 상태 보기"),
            ("/messages [room_id]", "협업 메시지 보기"),
            ("/events [room_id]", "저장된 UI/replay 이벤트 보기"),
            ("/report [room_id]", "저장된 리포트 출력"),
            ("/last", "최신 실행 카드 보기"),
            ("/help", "도움말"),
            ("/quit", "종료"),
        ]
        lines = [
            "메시지를 입력하면 슈퍼바이저가 대화/설정/소스저장/리서치룸 실행 여부를 판단합니다.",
            "진행 중에는 raw tool 로그 대신 에이전트별 작업 상태와 compact event line이 업데이트됩니다.",
            "",
            "COMMAND                         설명",
            "-------                         ----",
            *self.format_columns(command_rows, left_width=30),
        ]
        self.block("JIMMORIA 명령어", lines)

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
        self.block("슈퍼바이저", lines)

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

    def print_supervisor_working(self, activity: str = "요청을 읽고 다음 진행 방식을 정하는 중입니다.") -> None:
        self.print_log_line("슈퍼바이저", activity, muted=True)

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
        self.print_log_line("사용자", text)

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
        if self.show_event_log_lines():
            self.print_log_line(label, text, muted=muted)
        if self.use_runtime_dock() and self.runtime_room_running:
            self.print_runtime_dock()
        elif self.use_runtime_dock():
            self.show_cursor()

    def use_stream_events(self) -> bool:
        return self.event_style not in {"card", "cards", "panel", "panels"}

    def show_event_log_lines(self) -> bool:
        return self.use_compact_event_log() or self.use_debug_event_log()

    def use_compact_event_log(self) -> bool:
        return self.event_style in {"compact", "safe", "stable"}

    def use_debug_event_log(self) -> bool:
        return self.event_style in {"stream", "log", "logs", "debug", "trace"}

    def use_runtime_dock(self) -> bool:
        return (
            self.event_style == "dock"
            and supports_color()
            and runtime_dock_enabled()
            and not os.getenv("JIMMORIA_NO_RUNTIME_DOCK")
        )

    def read_chat_input(self) -> str:
        if not sys.stdin.isatty():
            return input(f"\n{APP_NAME.lower()}> ")

        if self.use_ansi_input_box():
            return self.read_ansi_boxed_input()
        return self.read_basic_boxed_input()

    def use_ansi_input_box(self) -> bool:
        return supports_color() and ansi_input_box_enabled()

    def read_ansi_boxed_input(self) -> str:
        self.show_cursor()
        hint = "요청, URL, /command, @path/to/file 을 입력하세요"
        border = self.input_border()
        print("")
        print(self.input_border_style(border))
        print(self.input_status_line_style(self.input_text_line(self.input_status_text())))
        print(self.input_hint_line_style(self.input_hint_line(hint)))
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
            self.input_active_summary_line_style(),
            self.input_hint_line_style(self.input_hint_line("리서치룸 실행 중입니다. 슈퍼바이저가 완료하면 입력창이 돌아옵니다.")),
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
        hint = "요청, URL, /command, @path/to/file 을 입력하세요"
        border = self.input_border()
        print("")
        print(self.input_border_style(border))
        print(self.input_status_line_style(self.input_text_line(self.input_status_text())))
        print(self.input_hint_line_style(self.input_hint_line(hint)))
        print(self.input_border_style(border))
        return input("> ")

    def input_box_width(self) -> int:
        available_width = max(32, self.width - 4)
        return max(32, min(available_width, max_input_width()))

    def input_border(self) -> str:
        return "+" + "-" * (self.input_box_width() - 2) + "+"

    def input_text_line(self, text: str) -> str:
        inner_width = self.input_box_width() - 4
        clipped = clip_display(text, inner_width)
        return "| " + pad_display(clipped, inner_width) + " |"

    def input_hint_line(self, text: str) -> str:
        return self.input_text_line(text)

    def input_divider_line(self) -> str:
        return "|" + "-" * (self.input_box_width() - 2) + "|"

    def input_edit_line(self) -> str:
        inner_width = self.input_box_width() - 4
        return "| " + pad_display("> ", inner_width) + " |"

    def input_status_text(self) -> str:
        provider = os.getenv("LLM_PROVIDER") or "offline"
        room = self.short_room_label()
        agents = self.agent_state_label()
        return f"JIMMORIA HQ | 슈퍼바이저 대화 | provider: {provider} | room: {room} | agents: {agents}"

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
            return f"실패 {failed}/진행 {running}/대기 {queued}"
        if running:
            return f"진행 {running}/대기 {queued}/완료 {done}"
        if queued:
            return f"대기 {queued}/완료 {done}"
        return f"완료 {done}"

    def input_border_style(self, text: str) -> str:
        if not supports_color():
            return text
        return f"\033[38;2;211;95;255m{text}\033[0m"

    def input_hint_line_style(self, text: str) -> str:
        if not supports_color():
            return text
        violet = "\033[38;2;211;95;255m"
        muted = "\033[38;2;160;132;188m"
        reset = "\033[0m"
        if not text.startswith("|") or not text.endswith("|"):
            return f"{muted}{text}{reset}"
        return f"{violet}|{reset}{muted}{text[1:-1]}{reset}{violet}|{reset}"

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
            "슈퍼바이저 대화",
            f"{dim}슈퍼바이저 대화{reset}",
            1,
        )

    def input_edit_line_style(self) -> str:
        violet = "\033[38;2;211;95;255m"
        pink = "\033[38;2;255;92;212m"
        reset = "\033[0m"
        inner_width = self.input_box_width() - 4
        padding = " " * max(inner_width - display_width("> "), 0)
        return f"{violet}|{reset} {pink}>{reset} {padding}{violet}|{reset}"

    def input_locked_line_style(self) -> str:
        violet = "\033[38;2;211;95;255m"
        muted = "\033[38;2;126;96;154m"
        pink = "\033[38;2;255;92;212m"
        blink = "\033[5m"
        reset = "\033[0m"
        inner_width = self.input_box_width() - 4
        text = "> 작업중..."
        visible_prefix = "> 작업중"
        visible_dots = "..."
        padding = " " * max(inner_width - display_width(text), 0)
        return (
            f"{violet}|{reset} {muted}{visible_prefix}{reset}"
            f"{blink}{pink}{visible_dots}{reset}{padding}{violet}|{reset}"
        )

    def input_active_summary_line_style(self) -> str:
        line = self.input_text_line(self.runtime_active_summary())
        if not supports_color():
            return line
        violet = "\033[38;2;211;95;255m"
        pink = "\033[38;2;255;92;212m"
        muted = "\033[38;2;160;132;188m"
        reset = "\033[0m"
        styled = line.replace("|", f"{violet}|{reset}", 2)
        styled = styled.replace("진행:", f"{pink}진행:{reset}", 1)
        styled = styled.replace("대기:", f"{muted}대기:{reset}", 1)
        styled = styled.replace("완료:", f"{muted}완료:{reset}", 1)
        return styled

    def runtime_active_summary(self) -> str:
        if not self.agent_state:
            return "진행: 대기 중 | 다음 요청을 기다립니다"
        running = [agent_id for agent_id in DEFAULT_AGENTS if self.agent_state.get(agent_id) == "running"]
        queued = [agent_id for agent_id in DEFAULT_AGENTS if self.agent_state.get(agent_id) == "queued"]
        done = [agent_id for agent_id in DEFAULT_AGENTS if self.agent_state.get(agent_id) == "done"]
        failed = [agent_id for agent_id in DEFAULT_AGENTS if self.agent_state.get(agent_id) == "failed"]
        if running:
            active_parts = [
                f"{self.agent_display_name(agent_id)} -> {self.compact_text(self.agent_activity.get(agent_id) or AGENT_ACTIVITY.get(agent_id, ''), 38)}"
                for agent_id in running[:2]
            ]
            active = "; ".join(active_parts)
        elif failed:
            active = "실패: " + ", ".join(self.agent_display_name(agent_id) for agent_id in failed[:2])
        elif queued:
            active = "준비 중"
        else:
            active = "룸 마무리"
        waiting = self.compact_agent_list(queued, limit=4)
        done_text = f" | 완료: {len(done)}" if done else ""
        return f"진행: {active} | 대기: {waiting or '없음'}{done_text}"

    def compact_runtime_state(self) -> str:
        if not self.agent_state:
            return "대기: 활성 리서치룸 없음"
        running = [agent_id for agent_id in DEFAULT_AGENTS if self.agent_state.get(agent_id) == "running"]
        queued = [agent_id for agent_id in DEFAULT_AGENTS if self.agent_state.get(agent_id) == "queued"]
        done = [agent_id for agent_id in DEFAULT_AGENTS if self.agent_state.get(agent_id) == "done"]
        failed = [agent_id for agent_id in DEFAULT_AGENTS if self.agent_state.get(agent_id) == "failed"]
        parts: list[str] = []
        if running:
            parts.append(f"진행: {self.compact_agent_list(running, limit=4)}")
        if queued:
            parts.append(f"대기: {self.compact_agent_list(queued, limit=5)}")
        if done:
            parts.append(f"완료: {len(done)}")
        if failed:
            parts.append(f"실패: {self.compact_agent_list(failed, limit=3)}")
        return " | ".join(parts) or "대기: 없음"

    def compact_agent_list(self, agent_ids: list[str], *, limit: int = 4) -> str:
        if not agent_ids:
            return ""
        shown = agent_ids[:limit]
        suffix = f" +{len(agent_ids) - limit}" if len(agent_ids) > limit else ""
        return ", ".join(self.agent_display_name(agent_id) for agent_id in shown) + suffix

    def input_board_title_line_style(self) -> str:
        line = self.input_text_line("에이전트 작업 카드 - 현재 진행 상황")
        if not supports_color():
            return line
        pink = "\033[38;2;255;92;212m"
        violet = "\033[38;2;211;95;255m"
        reset = "\033[0m"
        return line.replace("|", f"{violet}|{reset}", 2).replace(
            "에이전트 작업 카드",
            f"{pink}에이전트 작업 카드{reset}",
            1,
        )

    def input_board_header_line_style(self) -> str:
        line = self.input_text_line(f"{'상태':<6} {'에이전트':<20} 현재 작업")
        if not supports_color():
            return line
        violet = "\033[38;2;211;95;255m"
        muted = "\033[38;2;160;132;188m"
        reset = "\033[0m"
        return line.replace("|", f"{violet}|{reset}", 2).replace(
            "상태",
            f"{muted}상태{reset}",
            1,
        ).replace(
            "에이전트",
            f"{muted}에이전트{reset}",
            1,
        ).replace(
            "현재 작업",
            f"{muted}현재 작업{reset}",
            1,
        )

    def runtime_agent_board_lines(self) -> list[str]:
        if not self.agent_state:
            return [self.input_text_line("대기   활성 리서치룸 없음        다음 요청을 기다립니다")]

        cards: list[list[str]] = []
        for agent_id in DEFAULT_AGENTS:
            if agent_id not in self.agent_state:
                continue
            cards.append(self.agent_status_card(agent_id))
        lines = self.card_grid_lines(cards)
        if self.council_state != "idle" or self.runtime_room_running:
            lines.extend(self.card_grid_lines([self.council_status_card(full_width=True)], columns=1))
        return lines

    def agent_status_card(self, agent_id: str) -> list[str]:
        state = self.agent_state.get(agent_id, "queued")
        activity = self.agent_activity.get(agent_id) or AGENT_ACTIVITY.get(agent_id, "")
        name = self.agent_display_name(agent_id)
        state_text = self.state_label_ko(state)
        return self.status_card(
            title=f"{name}  [{state_text}]",
            subtitle=agent_id,
            body=self.activity_label(state, activity),
            state=state,
        )

    def council_status_card(self, *, full_width: bool = False) -> list[str]:
        participant_text = self.compact_agent_list(self.council_participants, limit=3)
        if participant_text:
            participant_text = f"참여: {participant_text}"
        else:
            participant_text = "참여: 대기"
        return self.status_card(
            title=f"토론방  [{self.state_label_ko(self.council_state)}]",
            subtitle=participant_text,
            body=self.council_activity,
            state=self.council_state,
            width=self.runtime_wide_card_width() if full_width else None,
        )

    def status_card(self, *, title: str, subtitle: str, body: str, state: str, width: int | None = None) -> list[str]:
        width = width or self.runtime_card_width()
        inner = width - 4
        return [
            "+" + "-" * (width - 2) + "+",
            "| " + pad_display(self.compact_text(title, inner), inner) + " |",
            "| " + pad_display(self.compact_text(subtitle, inner), inner) + " |",
            "| " + pad_display(self.compact_text(body, inner), inner) + " |",
            "+" + "-" * (width - 2) + "+",
        ]

    def runtime_card_width(self) -> int:
        if self.input_box_width() >= 112:
            return max(46, min(58, (self.input_box_width() - 8) // 2))
        return max(36, min(72, self.input_box_width() - 4))

    def runtime_wide_card_width(self) -> int:
        return max(36, self.input_box_width() - 4)

    def card_grid_lines(self, cards: list[list[str]], *, columns: int | None = None) -> list[str]:
        if not cards:
            return []
        columns = columns or (2 if self.input_box_width() >= 112 else 1)
        lines: list[str] = []
        for index in range(0, len(cards), columns):
            row_cards = cards[index : index + columns]
            for line_index in range(len(row_cards[0])):
                joined = "   ".join(card[line_index] for card in row_cards)
                lines.append(self.runtime_card_line_style(self.input_text_line(joined), self.card_row_state(row_cards, line_index)))
        return lines

    def card_row_state(self, row_cards: list[list[str]], line_index: int) -> str:
        # Use a neutral style for borders and row text; state labels are highlighted separately.
        return "card"

    def runtime_card_line_style(self, line: str, state: str) -> str:
        if not supports_color():
            return line
        violet = "\033[38;2;211;95;255m"
        reset = "\033[0m"
        styled = line.replace("|", f"{violet}|{reset}").replace("+", f"{violet}+{reset}")
        for label, color in {
            "진행": "\033[38;2;255;210;245m",
            "대기": "\033[38;2;160;132;188m",
            "완료": "\033[38;2;120;255;190m",
            "실패": "\033[38;2;255;92;120m",
        }.items():
            styled = styled.replace(f"[{label}]", f"[{color}{label}{reset}]", 1)
        return styled

    def agent_display_name(self, agent_id: str) -> str:
        return AGENT_DISPLAY_NAMES.get(agent_id, agent_id)

    def state_label_ko(self, state: str) -> str:
        return STATE_LABELS_KO.get(state, state)

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
                str(agent_id): AGENT_ACTIVITY.get(str(agent_id), "배정 대기 중")
                for agent_id in event.get("agents", [])
            }
            self.council_state = "queued"
            self.council_activity = "전문 에이전트 결과를 기다리는 중"
            self.council_participants = []
            if self.use_stream_events():
                topic = self.compact_text(str(event.get("topic", "")), 72)
                agent_count = len(event.get("agents", []))
                process = event.get("process") if isinstance(event.get("process"), dict) else {}
                process_id = process.get("process_id") if isinstance(process, dict) else ""
                process_text = f" | process {process_id}" if process_id else ""
                self.print_event_line("룸", f"OPEN {event.get('room_id')} | agents {agent_count}{process_text} | {topic}")
                if self.use_compact_event_log():
                    self.print_event_line("상태", self.compact_runtime_state(), muted=True)
                else:
                    self.print_event_line("보드", self.agent_state_label(), muted=True)
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
                self.print_event_line("에이전트", f"RUN {self.agent_display_name(agent_id)} | {self.agent_activity[agent_id]}")
                return
            label = self.agent_label(agent_id)
            self.block(
                f"{label} started",
                [
                    f"State: {self.state_label('running')}",
                    f"Task type: {event.get('task_type')}",
                    f"진행: {self.agent_activity[agent_id]}",
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
                    "에이전트",
                    f"DONE {self.agent_display_name(agent_id)} | {summary} | msg {event.get('messages')} / findings {event.get('findings')}{self.event_metrics_suffix(event)}",
                )
                return
            label = self.agent_label(agent_id)
            summary = str(event.get("summary", ""))
            self.block(
                f"{label} finished",
                [
                    f"State: {self.state_label('done')}",
                    f"완료: {summary}",
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
                self.print_event_line("에이전트", f"FAIL {self.agent_display_name(agent_id)} | {error}{self.event_metrics_suffix(event)}")
                return
            label = self.agent_label(agent_id)
            self.block(
                f"{label} failed",
                [
                    f"State: {self.state_label('failed')}",
                    f"Task type: {event.get('task_type')}",
                    f"중단: {event.get('error')}",
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
                if self.use_compact_event_log():
                    self.print_event_line(
                        "계획",
                        f"슈퍼바이저 | 작업 {delegated_count}개 배정 | 체크포인트 {checkpoint_count}개 | {summary}",
                    )
                    return
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

        if event_type == "parallel_group_start":
            agents = event.get("agents", [])
            agent_count = len(agents) if isinstance(agents, list) else 0
            if self.use_stream_events():
                if self.use_compact_event_log():
                    self.print_event_line(
                        "병렬",
                        f"START {event.get('group_id')} | {agent_count}개 에이전트 실행 | max {event.get('max_parallel')}",
                    )
                    self.print_event_line("상태", self.compact_runtime_state(), muted=True)
                    return
                self.print_event_line(
                    "Parallel",
                    f"START {event.get('group_id')} | agents {agent_count} | max {event.get('max_parallel')} | {event.get('summary')}",
                )
                return
            self.block(
                "Parallel agent group started",
                [
                    f"Group: {event.get('group_id')}",
                    f"Agents: {agent_count}",
                    f"Max parallel: {event.get('max_parallel')}",
                    str(event.get("summary") or ""),
                ],
            )
            return

        if event_type == "parallel_group_done":
            agents = event.get("agents", [])
            agent_count = len(agents) if isinstance(agents, list) else 0
            if self.use_stream_events():
                if self.use_compact_event_log():
                    self.print_event_line(
                        "병렬",
                        f"DONE {event.get('group_id')} | {agent_count}개 에이전트 완료 | msg {event.get('messages')} / findings {event.get('findings')}",
                    )
                    return
                self.print_event_line(
                    "Parallel",
                    f"DONE {event.get('group_id')} | agents {agent_count} | msg {event.get('messages')} / findings {event.get('findings')}",
                )
                return
            self.block(
                "Parallel agent group finished",
                [
                    f"Group: {event.get('group_id')}",
                    f"Agents: {agent_count}",
                    f"Messages: {event.get('messages')}",
                    f"Findings: {event.get('findings')}",
                ],
            )
            return

        if event_type == "parallel_group_failed":
            failures = event.get("failures", [])
            failure_count = len(failures) if isinstance(failures, list) else 0
            if self.use_stream_events():
                if self.use_compact_event_log():
                    self.print_event_line(
                        "병렬",
                        f"FAIL {event.get('group_id')} | 실패 {failure_count}개 | {event.get('summary')}",
                    )
                    return
                self.print_event_line(
                    "Parallel",
                    f"FAIL {event.get('group_id')} | failures {failure_count} | {event.get('summary')}",
                )
                return
            self.block(
                "Parallel agent group failed",
                [
                    f"Group: {event.get('group_id')}",
                    f"Failures: {failure_count}",
                    str(event.get("summary") or ""),
                ],
            )
            return

        if event_type == "deliberation_start":
            participants = event.get("participants", [])
            self.council_participants = [str(agent_id) for agent_id in participants] if isinstance(participants, list) else []
            self.council_state = "running"
            self.council_activity = self.compact_text(str(event.get("summary") or "전문 에이전트들이 근거를 비교하는 중"), 92)
            count = len(self.council_participants)
            if self.use_stream_events():
                self.print_event_line("토론방", f"START specialist roundtable | agents {count}")
                return
            self.block(
                "에이전트 토론방 시작",
                [
                    f"참여 에이전트: {count}",
                    self.council_activity,
                ],
            )
            return

        if event_type == "deliberation_statement":
            agent_id = str(event.get("agent_id") or "")
            summary = self.compact_text(str(event.get("summary") or ""), 92)
            self.council_state = "running"
            self.council_activity = f"{self.agent_display_name(agent_id)}: {summary}" if agent_id else summary
            if self.use_stream_events():
                self.print_event_line("토론방", self.council_activity)
            return

        if event_type == "deliberation_done":
            decision = str(event.get("decision") or "")
            summary = self.compact_text(str(event.get("summary") or ""), 84)
            self.council_state = "done"
            self.council_activity = f"합의: {decision} | {summary}" if decision else summary
            if self.use_stream_events():
                self.print_event_line(
                    "토론방",
                    f"DONE {decision} | {summary} | msg {event.get('messages')} / findings {event.get('findings')}",
                )
                return
            self.block(
                "에이전트 토론방 합의",
                [
                    f"결론: {decision}",
                    f"요약: {event.get('summary')}",
                    f"메시지: {event.get('messages')}",
                    f"근거: {event.get('findings')}",
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
        retention = room.project_card.get("run_retention") if isinstance(room.project_card, dict) else {}
        if not isinstance(retention, dict):
            retention = {}
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
        if evidence_packet_path and not retention.get("evidence_packet_deleted"):
            lines.append(f"Evidence packet: {evidence_packet_path}")
        if vault_path:
            lines.append(f"Vault: {vault_path}")
        if isinstance(retention, dict) and retention.get("run_snapshot_deleted"):
            lines.append("Room data: cleaned after report delivery")
            if retention.get("report_index"):
                lines.append(f"Report index: {retention.get('report_index')}")
        else:
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
            tool_name = str(event.get("tool_name") or "tool")
            if self.use_compact_event_log():
                if self.is_internal_runtime_tool(tool_name) and event_type in {"tool_start", "tool_done"}:
                    return
                agent_name = self.agent_display_name(str(event.get("agent_id") or ""))
                detail = self.compact_tool_detail(event_type, event, max_length=68)
                latency = f" | {event.get('latency_ms')}ms" if event.get("latency_ms") is not None else ""
                suffix = f" - {detail}" if detail else ""
                self.print_event_line("작업", f"{agent_name} | {marker} {tool_name}{suffix}{latency}")
                return
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
            "tool_start": "툴 실행",
            "tool_done": "툴 완료",
            "tool_failed": "툴 실패",
            "tool_denied": "툴 거절",
            "tool_unconfigured": "툴 대기",
        }.get(event_type, "툴")
        if self.is_internal_runtime_tool(tool_name):
            self.agent_activity[agent_id] = INTERNAL_TOOL_ACTIVITY.get(tool_name, "내부 작업 상태를 정리하는 중")
        else:
            detail = self.compact_tool_detail(event_type, event, max_length=64) or tool_name
            self.agent_activity[agent_id] = f"{marker}: {tool_name} - {detail}"
        if self.agent_state.get(agent_id) not in {"done", "failed"}:
            self.agent_state[agent_id] = "running"

    def is_internal_runtime_tool(self, tool_name: str) -> bool:
        return tool_name in INTERNAL_RUNTIME_TOOLS

    def compact_tool_detail(self, event_type: str, event: dict[str, object], *, max_length: int = 72) -> str:
        summary = str(event.get("summary") or "").strip()
        preview = self.readable_tool_input(event.get("input_preview"))
        if event_type == "tool_start" and preview:
            return self.compact_text(preview, max_length)
        if summary and summary.lower() not in {"success", "ok"}:
            return self.compact_text(summary, max_length)
        return self.compact_text(preview or summary, max_length)

    def readable_tool_input(self, value: object) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        parsed: object = raw
        try:
            parsed = ast.literal_eval(raw)
        except (SyntaxError, ValueError):
            return self.compact_text(raw, 72)
        if isinstance(parsed, dict):
            return self.readable_mapping_preview(parsed)
        if isinstance(parsed, list):
            if not parsed:
                return ""
            return self.compact_text(", ".join(self.readable_value(item) for item in parsed[:3]), 72)
        return self.readable_value(parsed)

    def readable_mapping_preview(self, data: dict[object, object]) -> str:
        priority_keys = (
            "query",
            "q",
            "search_query",
            "url",
            "title",
            "topic",
            "objective",
            "description",
            "task_id",
            "to_agent",
            "from_agent",
            "path",
            "contract_address",
            "token_symbol",
        )
        for key in priority_keys:
            if key in data and data[key]:
                return self.readable_value(data[key])
        parts: list[str] = []
        for key, item in list(data.items())[:3]:
            if item in (None, "", [], {}):
                continue
            parts.append(f"{key}: {self.readable_value(item)}")
        return "; ".join(parts)

    def readable_value(self, value: object) -> str:
        if isinstance(value, (list, tuple, set)):
            return ", ".join(self.readable_value(item) for item in list(value)[:3])
        if isinstance(value, dict):
            nested = self.readable_mapping_preview(value)
            return nested or "{}"
        return self.compact_text(str(value), 72)

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
            "queued": "대기",
            "running": "진행",
            "done": "완료",
            "failed": "중단",
        }.get(state, "상태")
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
        print(self.block_border_style(self.frame_border(title=title)))
        for line in lines:
            if not line:
                print(self.frame_text_line(""))
                continue
            for wrapped in self.wrap_for_frame(line):
                print(self.frame_line_style(self.frame_text_line(wrapped)))
        print(self.block_border_style(self.frame_border()))

    def wrap(self, text: str) -> list[str]:
        width = max(40, self.width - 4)
        return wrap_display(str(text), width) or [""]

    def wrap_for_frame(self, text: str) -> list[str]:
        return wrap_display(str(text), self.frame_inner_width()) or [""]

    def frame_width(self) -> int:
        return self.input_box_width()

    def frame_inner_width(self) -> int:
        return self.frame_width() - 4

    def frame_border(self, *, title: str = "") -> str:
        inner_width = self.frame_width() - 2
        if not title:
            return "+" + "-" * inner_width + "+"
        label = f" {title} "
        clipped = clip_display(label, inner_width)
        right = max(inner_width - display_width(clipped), 0)
        return "+" + clipped + "-" * right + "+"

    def frame_text_line(self, text: str) -> str:
        inner_width = self.frame_inner_width()
        clipped = clip_display(str(text), inner_width)
        return "| " + pad_display(clipped, inner_width) + " |"

    def block_border_style(self, text: str) -> str:
        if not supports_color():
            return text
        violet = "\033[38;2;211;95;255m"
        pink = "\033[38;2;255;92;212m"
        reset = "\033[0m"
        if text.startswith("+ ") and " " in text[2:]:
            end = text.find(" ", 2)
            if end != -1:
                return f"{violet}{text[:2]}{reset}{pink}{text[2:end]}{reset}{violet}{text[end:]}{reset}"
        return f"{violet}{text}{reset}"

    def frame_line_style(self, text: str) -> str:
        if not supports_color():
            return text
        if not text.startswith("|") or not text.endswith("|"):
            return text
        violet = "\033[38;2;211;95;255m"
        reset = "\033[0m"
        return f"{violet}|{reset}{text[1:-1]}{violet}|{reset}"

    def format_columns(self, rows: list[tuple[str, str]], *, left_width: int = 28) -> list[str]:
        formatted: list[str] = []
        gap = "  "
        right_width = max(20, self.frame_inner_width() - left_width - display_width(gap))
        for left, right in rows:
            left_text = clip_display(left, left_width)
            right_lines = wrap_display(right, right_width) or [""]
            formatted.append(f"{pad_display(left_text, left_width)}{gap}{right_lines[0]}")
            for continuation in right_lines[1:]:
                formatted.append(f"{' ' * left_width}{gap}{continuation}")
        return formatted

    def compact_text(self, text: str, max_length: int = 88) -> str:
        compact = " ".join(str(text).split())
        if display_width(compact) <= max_length:
            return compact
        return clip_display(compact, max_length, suffix="...").rstrip()

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
    width = max(72, min(width, 132))
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
    korean_subtitle = "슈퍼바이저가 이끄는 온체인 리서치 HQ"
    workflow = "tmux-friendly TUI  /  agent cards  /  council room  /  obsidian memory"

    print(f"{violet}{line}{reset}")
    print("")
    for row_index, (row, layers) in enumerate(logo):
        print(center_ansi(style_logo_layer_line(row, layers, row_index), width))
    print("")
    print(center_ansi(f"{bold}{pink}JIMMORIA v{__version__}{reset}", width))
    print(center_ansi(f"{silver}{subtitle}{reset}", width))
    print(center_ansi(f"{silver}{korean_subtitle}{reset}", width))
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
    print(center_text("슈퍼바이저가 이끄는 온체인 리서치 HQ", width))
    print(center_text("tmux-friendly TUI  /  agent cards  /  council room  /  obsidian memory", width))
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


def rich_blocks_enabled() -> bool:
    if os.getenv("JIMMORIA_PLAIN_LOGS") or os.getenv("JIMMORIA_NO_RICH"):
        return False
    if os.getenv("JIMMORIA_FORCE_RICH") or os.getenv("JIMMORIA_RICH"):
        return True
    # Windows PowerShell commonly mismeasures CJK text inside rich box borders.
    # Keep the live input dock colored, but render help/config blocks as plain text.
    if os.name == "nt":
        return False
    return True


def default_event_style() -> str:
    configured = os.getenv("JIMMORIA_EVENT_STYLE", "").strip().lower()
    if configured:
        return configured
    # Windows terminals often leave stale lines when we use ANSI cursor-up and
    # delete-line sequences for a live dock. Compact logs are boring, but solid.
    if os.name == "nt" and not os.getenv("JIMMORIA_FORCE_RUNTIME_DOCK"):
        return "compact"
    return "dock"


def runtime_dock_enabled() -> bool:
    if os.getenv("JIMMORIA_FORCE_RUNTIME_DOCK"):
        return True
    if os.getenv("JIMMORIA_DISABLE_RUNTIME_DOCK"):
        return False
    return os.name != "nt"


def ansi_input_box_enabled() -> bool:
    if os.getenv("JIMMORIA_FORCE_ANSI_INPUT"):
        return True
    if os.getenv("JIMMORIA_DISABLE_ANSI_INPUT"):
        return False
    return os.name != "nt"


def safe_terminal_width(terminal_width: int) -> int:
    return max(72, min(terminal_width, max_ui_width()))


def max_ui_width() -> int:
    default = "136" if os.name == "nt" else "160"
    return positive_int_env("JIMMORIA_MAX_UI_WIDTH", default)


def max_input_width() -> int:
    default = str(max_ui_width())
    return positive_int_env("JIMMORIA_MAX_INPUT_WIDTH", default)


def positive_int_env(name: str, default: str) -> int:
    raw = os.getenv(name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = int(default)
    return max(32, value)


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
    return " " * max((width - display_width(text)) // 2, 0) + text


def visible_len(text: str) -> int:
    return display_width(text)


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def display_width(text: object) -> int:
    value = ANSI_RE.sub("", str(text))
    return sum(char_display_width(char) for char in value)


def char_display_width(char: str) -> int:
    if not char:
        return 0
    if unicodedata.combining(char):
        return 0
    category = unicodedata.category(char)
    if category.startswith("C"):
        return 0
    if char == "\t":
        return 4
    return 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1


def clip_display(text: object, max_width: int, *, suffix: str = "") -> str:
    value = str(text)
    if max_width <= 0:
        return ""
    if display_width(value) <= max_width:
        return value
    suffix_width = display_width(suffix)
    content_width = max(max_width - suffix_width, 0)
    output: list[str] = []
    width = 0
    for char in value:
        char_width = char_display_width(char)
        if width + char_width > content_width:
            break
        output.append(char)
        width += char_width
    if suffix and width + suffix_width <= max_width:
        output.append(suffix)
    return "".join(output)


def wrap_display(text: object, max_width: int) -> list[str]:
    value = str(text)
    if max_width <= 0:
        return [value] if value else [""]
    if not value:
        return [""]
    lines: list[str] = []
    current: list[str] = []
    width = 0
    last_space_index = -1

    for char in value:
        if char == "\n":
            lines.append("".join(current).rstrip())
            current = []
            width = 0
            last_space_index = -1
            continue
        char_width = char_display_width(char)
        if width + char_width <= max_width:
            current.append(char)
            width += char_width
            if char.isspace():
                last_space_index = len(current) - 1
            continue

        if last_space_index > 0:
            lines.append("".join(current[:last_space_index]).rstrip())
            remainder = "".join(current[last_space_index + 1 :]).lstrip()
            current = list(remainder)
            width = display_width(remainder)
        elif current:
            lines.append("".join(current).rstrip())
            current = []
            width = 0

        current.append(char)
        width += char_width
        last_space_index = len(current) - 1 if char.isspace() else -1

    if current or not lines:
        lines.append("".join(current).rstrip())
    return lines


def pad_display(text: object, width: int) -> str:
    clipped = clip_display(text, width)
    return clipped + " " * max(width - display_width(clipped), 0)


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
