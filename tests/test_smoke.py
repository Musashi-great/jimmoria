from pathlib import Path
from tempfile import TemporaryDirectory
import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
import types
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from crypto_research_agents.runtime import ResearchRuntime
from crypto_research_agents.agents.base import AgentResult, normalize_llm_analysis
from crypto_research_agents.agents.discovery import build_live_candidates, extract_project_query, project_identity_hints, should_live_discover
from crypto_research_agents.agents.social_kol import (
    build_public_social_queries,
    build_who_said_what,
    extract_handles_from_social_results,
)
from crypto_research_agents.agents.report import ReportAgent, assess_report_quality, build_claim_evidence_ledger, diligence_score
from crypto_research_agents.agents.reporting.evidence_ledger import build_project_dossier_evidence_pack
from crypto_research_agents.connectors import register_default_connectors
from crypto_research_agents.core.agent_spec import AgentSpecRegistry
from crypto_research_agents.cli import (
    apply_company_instruction,
    chat_command,
    classify_chat_input,
    chat_input_to_source,
    configure_model_panel,
    configured_model_provider_ready,
    find_saved_report_for_request,
    model_settings_path,
    main as cli_main,
    message_summary,
    previous_report_context,
    print_banner,
)
from crypto_research_agents.console import JimmoriaConsole, display_width, print_jimmoria_logo
from crypto_research_agents.core.company_settings import CompanySettings
from crypto_research_agents.core.input_resolver import resolve_research_input
from crypto_research_agents.core.concurrency import load_concurrency_policy
from crypto_research_agents.core.llm_provider import CodexApiProvider, CodexCliProvider, CodexSdkProvider, LLMRequest, LLMResponse, parse_json_response, provider_from_env
from crypto_research_agents.core.memory import ProjectCandidate, SharedMemory, SourceRecord
from crypto_research_agents.core.model_gateway import ModelGateway
from crypto_research_agents.core.process_spec import ProcessSpecRegistry, load_process_spec
from crypto_research_agents.core.project_profile import find_project_profile
from crypto_research_agents.core.dynamic_dispatch import DynamicCandidateDispatcher
from crypto_research_agents.core.edge_conditions import evaluate_edge_condition
from crypto_research_agents.core.hook_registry import HookRegistry, runtime_event_to_hook_event
from crypto_research_agents.core.korean_style import korean_report_humanize_prompt
from crypto_research_agents.core.quality_gate import review_report_quality
from crypto_research_agents.core.room import ResearchRoom
from crypto_research_agents.core.scheduler import CronRegistry
from crypto_research_agents.core.skill_spec import SkillSpecRegistry
from crypto_research_agents.core.supervisor_brain import SupervisorBrain
from crypto_research_agents.core.supervisor_chat import generate_supervisor_chat_reply
from crypto_research_agents.core.supervisor_intake import decide_supervisor_intake
from crypto_research_agents.core.supervisor_job_contract import (
    build_supervisor_job_contract,
    max_agent_attempts_from_contract,
)
from crypto_research_agents.core.supervisor_memory import SupervisorMemoryStore
from crypto_research_agents.core.supervisor_session import SupervisorSessionStore
from crypto_research_agents.core.capabilities import collect_capabilities
from crypto_research_agents.core.playbook import ResearchPlaybookRegistry
from crypto_research_agents.core.profile import WorkerProfileRegistry
from crypto_research_agents.core.tool_gateway import PolicyEngine, ToolGateway
from crypto_research_agents.core.workflow import LoopCounter
from crypto_research_agents.core.workflow_executor import WorkflowExecutor
from crypto_research_agents.core.workflow_loader import WorkflowSpecRegistry, load_workflow_spec
from crypto_research_agents.storage.artifact_store import ArtifactStore
from crypto_research_agents.storage.run_store import events_after_seq, load_report_index
from crypto_research_agents.storage.session_store import search_sessions
from crypto_research_agents.tools.registry import load_tool_registry
from crypto_research_agents.web import build_overview_payload, build_run_payload, render_dashboard_html


def _offline_no_secret_env() -> dict[str, str]:
    return {
        "LLM_PROVIDER": "offline",
        "X_BEARER_TOKEN": "",
        "TWITTER_BEARER_TOKEN": "",
        "ROOTDATA_API_KEY": "",
        "ETHERSCAN_API_KEY": "",
        "ETH_RPC_URL": "",
        "RPC_URL": "",
        "DUNE_API_KEY": "",
        "THEGRAPH_API_KEY": "",
        "GITHUB_TOKEN": "",
    }


class SmokeTest(unittest.TestCase):
    def test_cli_banner_uses_jimmoria_brand(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            print_banner()

        text = output.getvalue()
        self.assertIn("JIMMORIA v0.1.0", text)
        self.assertIn("Multi-agent crypto research company", text)
        self.assertNotIn("JJJJJJJ", text)
        self.assertNotIn("Company roster", text)

    def test_color_banner_uses_purple_pink_3d_palette(self) -> None:
        output = StringIO()

        with patch("crypto_research_agents.console.supports_color", return_value=True):
            with redirect_stdout(output):
                print_jimmoria_logo(100)

        text = output.getvalue()
        self.assertIn("38;2;255;79;216", text)
        self.assertIn("38;2;90;38;137", text)
        self.assertNotIn("38;2;64;204;255", text)
        self.assertNotIn("38;2;55;120;255", text)

    def test_pyproject_exposes_jimmoria_command(self) -> None:
        pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(pyproject["project"]["scripts"]["jimmoria"], "crypto_research_agents.cli:main")
        self.assertEqual(pyproject["tool"]["setuptools"]["packages"]["find"]["include"], ["crypto_research_agents*"])
        self.assertIn("rich>=13.7.0", pyproject["project"]["dependencies"])
        self.assertIn("all", pyproject["project"]["optional-dependencies"])
        self.assertIn("codex", pyproject["project"]["optional-dependencies"])
        self.assertIn("openai-codex", pyproject["project"]["optional-dependencies"]["codex"])
        self.assertIn("ddgs>=9.14.0", pyproject["project"]["optional-dependencies"]["all"])
        self.assertIn("feedparser>=6.0.11", pyproject["project"]["optional-dependencies"]["all"])
        self.assertIn("openai-codex", pyproject["project"]["optional-dependencies"]["all"])

    def test_chat_input_uses_boxed_prompt(self) -> None:
        output = StringIO()
        console = JimmoriaConsole()

        with patch("sys.stdin.isatty", return_value=True):
            with patch("builtins.input", return_value="/quit") as mocked_input:
                with redirect_stdout(output):
                    value = console.read_chat_input()

        self.assertEqual(value, "/quit")
        self.assertIn("+", output.getvalue())
        self.assertIn("슈퍼바이저 대화", output.getvalue())
        self.assertIn("@path/to/file", output.getvalue())
        self.assertEqual(mocked_input.call_args[0][0], "> ")
        box_lines = [line for line in output.getvalue().splitlines() if line.startswith(("+", "|"))]
        self.assertGreaterEqual(len(box_lines), 4)
        self.assertTrue(box_lines[0].startswith("+"))
        self.assertTrue(box_lines[-1].startswith("+"))

    def test_chat_input_renders_closed_ansi_box(self) -> None:
        output = StringIO()
        console = JimmoriaConsole()
        console.width = 72

        with patch.dict("os.environ", {"JIMMORIA_FORCE_ANSI_INPUT": "1"}, clear=False):
            with patch("sys.stdin.isatty", return_value=True):
                with patch("crypto_research_agents.console.supports_color", return_value=True):
                    with patch("builtins.input", return_value="/quit") as mocked_input:
                        with redirect_stdout(output):
                            value = console.read_chat_input()

        clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", output.getvalue())
        box_lines = [
            line
            for line in clean.splitlines()
            if line.startswith(("+", "|"))
        ]
        self.assertEqual(value, "/quit")
        self.assertEqual(mocked_input.call_args[0][0], "\033[2A\033[4C")
        self.assertGreaterEqual(len(box_lines), 5)
        self.assertTrue(box_lines[0].startswith("+"))
        self.assertIn("슈퍼바이저 대화", box_lines[1])
        self.assertTrue(box_lines[1].endswith("|"))
        self.assertTrue(box_lines[2].endswith("|"))
        self.assertTrue(box_lines[3].endswith("|"))
        self.assertTrue(box_lines[4].startswith("+"))
        self.assertIn("\033[5M", output.getvalue())

    def test_windows_default_chat_input_uses_stable_plain_box(self) -> None:
        output = StringIO()
        console = JimmoriaConsole()

        with patch.dict("os.environ", {"JIMMORIA_DISABLE_ANSI_INPUT": "1"}, clear=False):
            with patch("sys.stdin.isatty", return_value=True):
                with patch("crypto_research_agents.console.supports_color", return_value=True):
                    with patch("builtins.input", return_value="/quit") as mocked_input:
                        with redirect_stdout(output):
                            value = console.read_chat_input()

        self.assertEqual(value, "/quit")
        self.assertEqual(mocked_input.call_args[0][0], "\033[2A\033[4C")
        self.assertNotIn("\033[5M", output.getvalue())
        clean = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", output.getvalue())
        box_lines = [line for line in clean.splitlines() if line.startswith(("+", "|"))]
        self.assertGreaterEqual(len(box_lines), 5)
        self.assertTrue(box_lines[0].startswith("+"))
        self.assertTrue(box_lines[3].startswith("| >"))
        self.assertTrue(box_lines[-1].startswith("+"))
        self.assertIn("\033[1B\r", output.getvalue())

    def test_chat_input_status_line_uses_display_width_for_korean(self) -> None:
        console = JimmoriaConsole()
        console.width = 118

        with patch.dict("os.environ", {"LLM_PROVIDER": "codex_sdk"}, clear=True):
            line = console.input_text_line(console.input_status_text())

        self.assertEqual(display_width(line), console.input_box_width())
        self.assertTrue(line.endswith("|"))
        self.assertIn("JIMMORIA HQ", line)
        self.assertIn("슈퍼바이저 대화", line)

    def test_status_card_uses_display_width_for_korean(self) -> None:
        console = JimmoriaConsole()
        console.width = 118

        card = console.status_card(
            title="슈퍼바이저  [진행]",
            subtitle="supervisor_agent",
            body="리서치 방향과 작업 순서를 정리하는 중",
            state="running",
        )

        self.assertTrue(all(display_width(line) == console.runtime_card_width() for line in card))

    def test_input_status_line_tracks_room_and_agents(self) -> None:
        console = JimmoriaConsole()
        console.last_room_id = "room_1234567890abcdef"
        console.agent_state = {
            "supervisor_agent": "done",
            "ingestion_agent": "running",
            "narrative_agent": "queued",
        }

        status = console.input_status_text()

        self.assertIn("JIMMORIA HQ", status)
        self.assertIn("슈퍼바이저 대화", status)
        self.assertIn("room_12345...bcdef", status)
        self.assertIn("진행 1/대기 1/완료 1", status)

    def test_user_message_prints_compact_log_not_panel(self) -> None:
        output = StringIO()
        console = JimmoriaConsole()
        console.use_rich = False

        with redirect_stdout(output):
            console.print_user_message("안녕하세요")

        text = output.getvalue()
        self.assertIn("사용자 > 안녕하세요", text)
        self.assertNotIn("[You]", text)

    def test_supervisor_working_prints_compact_log(self) -> None:
        output = StringIO()
        console = JimmoriaConsole()
        console.use_rich = False

        with redirect_stdout(output):
            console.print_supervisor_working("Reading and routing.")

        text = output.getvalue()
        self.assertIn("슈퍼바이저 > Reading and routing.", text)
        self.assertNotIn("[Supervisor]", text)

    def test_chat_help_does_not_show_static_agent_roster(self) -> None:
        output = StringIO()

        with patch.dict("os.environ", {"JIMMORIA_NO_RICH": "1"}, clear=False):
            with redirect_stdout(output):
                JimmoriaConsole().print_help()

        text = output.getvalue()
        self.assertIn("JIMMORIA 명령어", text)
        self.assertIn("/settings", text)
        self.assertIn("/research <topic-or-url>", text)
        self.assertIn("/dossier <topic-or-url>", text)
        self.assertIn("+ JIMMORIA 명령어", text)
        self.assertIn("| /settings", text)
        self.assertNotIn("Agents at work:", text)

    def test_plain_block_renders_closed_frame_with_cjk_width(self) -> None:
        output = StringIO()
        console = JimmoriaConsole()
        console.use_rich = False
        console.width = 96

        with redirect_stdout(output):
            console.block("슈퍼바이저", ["한국어 문장이 길어도 오른쪽 테두리가 맞아야 합니다.", "", "/settings  회사 운영 설정 보기"])

        lines = [line for line in output.getvalue().splitlines() if line.startswith(("+", "|"))]
        widths = {display_width(line) for line in lines}
        self.assertEqual(len(widths), 1)
        self.assertTrue(lines[0].startswith("+ 슈퍼바이저"))
        self.assertTrue(lines[-1].startswith("+"))
        self.assertTrue(all(line.endswith(("+", "|")) for line in lines))
        self.assertNotIn("[슈퍼바이저]", output.getvalue())

    def test_chat_intake_routes_company_settings_without_report(self) -> None:
        output = StringIO()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = argparse.Namespace(
                memory=str(root / "memory.json"),
                vault=str(root / "vault"),
                reports=str(root / "reports"),
                skip_model_setup=True,
            )
            with patch.dict("os.environ", {"JIMMORIA_MODEL_SETTINGS_PATH": str(root / "model_settings.json")}, clear=True):
                with patch("sys.stdin.isatty", return_value=True):
                    with patch("builtins.input", side_effect=["보고서는 한글로 만들어봐 영어단어는 사용해도 좋아", "/quit"]):
                        with redirect_stdout(output):
                            chat_command(args)

            settings_path = root / "company_settings.json"
            self.assertTrue(settings_path.exists())
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(settings["report_language"], "ko")
            self.assertTrue(settings["allow_english_terms"])
            self.assertFalse((root / "reports").exists())

        text = output.getvalue()
        self.assertIn("Supervisor", text)
        self.assertIn("회사 운영 지시", text)
        self.assertIn("반영한 내용", text)

    def test_chat_intake_classifies_research_vs_settings(self) -> None:
        self.assertEqual(classify_chat_input("pearl 프로젝트에 대해서 리서치 보고서 만들어봐"), "research_input_resolution")
        self.assertEqual(classify_chat_input("보고서는 한글로 만들어봐 영어단어는 써도 돼"), "company_config")
        self.assertEqual(classify_chat_input("현재 회사 상태랑 설정 보여줘"), "company_status")
        self.assertEqual(classify_chat_input("이 링크는 소스만 저장해줘"), "source_ingestion")
        self.assertEqual(classify_chat_input("지금 보고서 작성은 한글 위주로 세팅된게 맞지?"), "supervisor_chat")
        self.assertEqual(classify_chat_input("안녕"), "supervisor_chat")

    def test_chat_intake_classifies_saved_report_request(self) -> None:
        self.assertEqual(classify_chat_input("3jane 관련 투자 보고서 만들어봐"), "research_input_resolution")
        self.assertEqual(classify_chat_input("3jane 보고서 만든거 보내봐 전체"), "report_retrieval")
        self.assertEqual(classify_chat_input("3jane 보고서 만들어봐"), "research_input_resolution")
        self.assertEqual(classify_chat_input("3jane 보고서 들고와봐"), "report_retrieval")
        self.assertEqual(classify_chat_input("show 3jane full report"), "report_retrieval")
        self.assertEqual(classify_chat_input("3jane 보고서 가지고 와봐"), "report_retrieval")

    def test_company_instruction_expands_supervisor_role(self) -> None:
        settings = CompanySettings()

        applied = apply_company_instruction(
            "슈퍼바이저는 회사의 사장 느낌으로 외주를 받는 역할로 가져가자",
            settings,
        )

        self.assertEqual(settings.supervisor_mode, "company_ceo")
        self.assertEqual(settings.client_relationship, "outsourcing_client")
        self.assertIn("route_all_plain_chat_inputs", settings.supervisor_authority)
        self.assertIn("choose_response_shape_per_request", settings.supervisor_authority)
        self.assertIn("orchestrate_specialist_workflow", settings.supervisor_authority)
        self.assertIn("coordinate_agent_council", settings.supervisor_authority)
        self.assertIn("Supervisor mode: company CEO / outsourcing intake", applied)
        self.assertIn("Supervisor role: orchestrator / specialist coordinator", applied)

    def test_company_instruction_sets_supervisor_as_orchestrator(self) -> None:
        settings = CompanySettings()

        applied = apply_company_instruction(
            "수퍼바이저가 오케스트레이터로 활동을 하고 조율하는거야",
            settings,
        )

        self.assertEqual(settings.supervisor_mode, "company_ceo")
        self.assertEqual(settings.client_relationship, "outsourcing_client")
        self.assertIn("orchestrate_specialist_workflow", settings.supervisor_authority)
        self.assertIn("coordinate_agent_council", settings.supervisor_authority)
        self.assertTrue(any("orchestrator" in item for item in settings.operating_principles))
        self.assertIn("Supervisor role: orchestrator / specialist coordinator", applied)

    def test_supervisor_intake_returns_output_modes(self) -> None:
        settings = CompanySettings(supervisor_mode="company_ceo")

        research = decide_supervisor_intake("pearl 프로젝트를 분석해봐", settings)
        report = decide_supervisor_intake("pearl 프로젝트 리서치 보고서 작성해봐 https://x.com/pearl", settings)
        missing_link_report = decide_supervisor_intake("pearl 프로젝트 리서치 보고서 작성해봐", settings)
        config = decide_supervisor_intake("로그 출력 스타일을 바꿔봐", settings)
        status = decide_supervisor_intake("현재 회사 상태 보여줘", settings)

        self.assertFalse(research.needs_research_room)
        self.assertEqual(research.output_mode, "supervisor_reply")
        self.assertEqual(research.action, "ask_for_source_link")
        self.assertTrue(research.requires_source_link)
        self.assertEqual(missing_link_report.action, "ask_for_source_link")
        self.assertTrue(report.needs_research_room)
        self.assertEqual(report.output_mode, "research_dossier")
        self.assertFalse(config.needs_research_room)
        self.assertEqual(config.output_mode, "settings_update")
        self.assertFalse(status.needs_research_room)
        self.assertEqual(status.output_mode, "settings_panel")
        chat = decide_supervisor_intake("지금 보고서 작성은 한글 위주로 세팅된게 맞지?", settings)
        self.assertFalse(chat.needs_research_room)
        self.assertEqual(chat.output_mode, "supervisor_reply")

    def test_supervisor_job_contract_distinguishes_chat_and_closed_fleet(self) -> None:
        chat_contract = build_supervisor_job_contract(
            line="hello, remember my UI preference",
            decision={
                "intent_type": "supervisor_chat",
                "output_mode": "supervisor_reply",
                "needs_research_room": False,
            },
            topic="hello",
        ).to_dict()

        research_contract = build_supervisor_job_contract(
            line="write a Zcash project report https://z.cash/",
            decision={
                "intent_type": "research_request",
                "output_mode": "research_dossier",
                "needs_research_room": True,
            },
            process_id="project_research_room",
            agent_ids=["supervisor_agent", "ingestion_agent", "report_agent"],
            topic="Zcash",
        ).to_dict()

        self.assertEqual(chat_contract["loop_mode"], "single_agent_chat")
        self.assertEqual(chat_contract["agent_ids"], ["supervisor_agent"])
        self.assertEqual(max_agent_attempts_from_contract(chat_contract), 1)
        self.assertFalse(chat_contract["iteration_policy"]["loop_until_goal_met"])
        self.assertEqual(research_contract["loop_mode"], "closed_fleet")
        self.assertEqual(research_contract["ui_policy"]["visible_mode"], "fixed_agent_dashboard")
        self.assertTrue(research_contract["ui_policy"]["show_total_token_usage"])
        self.assertEqual(research_contract["extension_policy"]["architecture_rule"], "narrow_waist")
        self.assertIn("progressive_disclosure", research_contract["context_policy"])
        self.assertIn("deep_recall", research_contract["memory_policy"])
        self.assertTrue(research_contract["delegation_policy"]["subagents_start_fresh"])
        self.assertIn("verification_gates", research_contract["delegation_policy"]["required_handoff_fields"])
        self.assertIn("Identity Gate", " ".join(research_contract["verification_gates"]))
        self.assertEqual(max_agent_attempts_from_contract(research_contract), 2)

    def test_project_research_input_resolver_requires_source_link_and_classifies_identity(self) -> None:
        resolved = resolve_research_input("$POD Dolphin 리서치 https://x.com/dolphin_xyz https://github.com/dolphin/protocol 0x1234567890abcdef1234567890abcdef12345678")

        self.assertTrue(resolved.required_link_present)
        self.assertIn("ticker", resolved.input_types)
        self.assertIn("x_account", resolved.input_types)
        self.assertIn("github_repo", resolved.input_types)
        self.assertEqual(resolved.tickers, ["POD"])
        self.assertEqual(resolved.contract_addresses, ["0x1234567890abcdef1234567890abcdef12345678"])
        self.assertTrue(any(candidate.source_type == "official_x_candidate" for candidate in resolved.identity_candidates))

        no_link = resolve_research_input("Dolphin이 뭐하는 프로젝트인지 봐줘")
        self.assertFalse(no_link.required_link_present)
        self.assertTrue(no_link.needs_link_for_research)
        self.assertIn("source_link_required_for_research", no_link.warnings)

    def test_project_dossier_evidence_pack_marks_required_slots(self) -> None:
        project = ProjectCandidate(
            project_id="3jane",
            name="3Jane",
            reason_found="On-chain credit project",
            website="https://3jane.xyz",
        )
        source_log = [
            {"source_id": "S1", "label": "Official", "url": "https://3jane.xyz"},
            {"source_id": "S2", "label": "X", "url": "https://x.com/3janexyz"},
            {"source_id": "S3", "label": "Docs", "url": "https://docs.3jane.xyz"},
            {"source_id": "S4", "label": "GitHub", "url": "https://github.com/3jane/credit"},
        ]

        pack = build_project_dossier_evidence_pack(project, [], source_log)

        self.assertIn("official_website", pack)
        self.assertIn("official_x", pack)
        self.assertIn("unanswered_questions", pack)
        self.assertEqual(pack["official_website"]["status"], "confirmed")
        self.assertEqual(pack["official_x"]["status"], "confirmed")
        self.assertEqual(pack["contract"]["status"], "unverified")
        self.assertIn("미확인 evidence slots", " ".join(pack["unanswered_questions"]["notes"]))

    def test_supervisor_chat_answers_without_report(self) -> None:
        output = StringIO()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "company_settings.json").write_text(
                json.dumps(CompanySettings(report_language="ko").to_dict(), ensure_ascii=False),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                memory=str(root / "memory.json"),
                vault=str(root / "vault"),
                reports=str(root / "reports"),
                skip_model_setup=True,
            )
            with patch.dict("os.environ", {"JIMMORIA_MODEL_SETTINGS_PATH": str(root / "model_settings.json")}, clear=True):
                with patch("sys.stdin.isatty", return_value=True):
                    with patch("builtins.input", side_effect=["지금 보고서 작성은 한글 위주로 세팅된게 맞지?", "/quit"]):
                        with redirect_stdout(output):
                            chat_command(args)

            self.assertFalse((root / "reports").exists())

        text = output.getvalue()
        self.assertIn("슈퍼바이저", text)
        self.assertNotIn("Supervisor intake", text)
        self.assertNotIn("Report preview", text)
        self.assertIn("한국어 우선", text)

    def test_small_talk_answers_without_saving_settings(self) -> None:
        output = StringIO()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = argparse.Namespace(
                memory=str(root / "memory.json"),
                vault=str(root / "vault"),
                reports=str(root / "reports"),
                skip_model_setup=True,
            )
            with patch.dict("os.environ", {"JIMMORIA_MODEL_SETTINGS_PATH": str(root / "model_settings.json")}, clear=True):
                with patch("sys.stdin.isatty", return_value=True):
                    with patch("builtins.input", side_effect=["안녕", "/quit"]):
                        with redirect_stdout(output):
                            chat_command(args)

            self.assertFalse((root / "reports").exists())
            self.assertFalse((root / "company_settings.json").exists())
            session_path = root / "supervisor_session.json"
            self.assertTrue(session_path.exists())
            session = json.loads(session_path.read_text(encoding="utf-8"))
            self.assertGreaterEqual(len(session["messages"]), 2)
            self.assertEqual(session["messages"][-2]["role"], "user")
            self.assertEqual(session["messages"][-1]["role"], "supervisor")

        text = output.getvalue()
        self.assertIn("슈퍼바이저", text)
        self.assertNotIn("Supervisor intake", text)
        self.assertIn("JIMMORIA Supervisor", text)
        self.assertNotIn("Company instruction applied", text)

    def test_supervisor_chat_explains_report_structure_naturally(self) -> None:
        settings = CompanySettings(report_language="ko")
        decision = decide_supervisor_intake("보고서 뭐뭐씀?", settings)

        reply = generate_supervisor_chat_reply("보고서 뭐뭐씀?", settings, decision)
        joined = "\n".join(reply)

        self.assertIn("TL;DR", joined)
        self.assertIn("후보 프로젝트", joined)
        self.assertNotIn("report_language", joined.lower())

    def test_supervisor_chat_uses_live_model_when_available(self) -> None:
        class FakeProvider:
            provider_name = "fake_live"

            def complete(self, request: LLMRequest) -> LLMResponse:
                self.request = request
                return LLMResponse(
                    text="좋아. 여기서는 내가 바로 답하고, 리서치가 필요하면 방을 열게.",
                    model=request.model,
                    provider=self.provider_name,
                    usage={"mode": "test"},
                )

        provider = FakeProvider()
        gateway = ModelGateway(provider=provider)
        decision = decide_supervisor_intake("보고서 뭐뭐씀?", CompanySettings(report_language="ko"))

        reply = generate_supervisor_chat_reply(
            "보고서 뭐뭐씀?",
            CompanySettings(report_language="ko"),
            decision,
            history=[{"role": "user", "content": "안녕"}],
            model_gateway=gateway,
        )

        self.assertEqual(reply, ["좋아. 여기서는 내가 바로 답하고, 리서치가 필요하면 방을 열게."])
        self.assertEqual(provider.request.task_type, "supervisor_chat")
        self.assertIn("recent_dialogue", provider.request.user_prompt)

    def test_supervisor_chat_prompt_includes_persistent_memory_context(self) -> None:
        class FakeProvider:
            provider_name = "fake_live"

            def complete(self, request: LLMRequest) -> LLMResponse:
                self.request = request
                return LLMResponse(text="ok", model=request.model, provider=self.provider_name, usage={})

        provider = FakeProvider()
        gateway = ModelGateway(provider=provider)
        settings = CompanySettings(report_language="ko")
        decision = decide_supervisor_intake("what do you remember?", settings)

        generate_supervisor_chat_reply(
            "what do you remember?",
            settings,
            decision,
            history=[{"role": "user", "content": "previous turn"}],
            memory_context=["[preference] supervisor_memory_expected: keep memory across sessions"],
            session_context=["Last Research Room: room_test (Zcash)"],
            model_gateway=gateway,
        )

        self.assertIn("supervisor_memory", provider.request.user_prompt)
        self.assertIn("keep memory across sessions", provider.request.user_prompt)
        self.assertIn("Last Research Room", provider.request.user_prompt)

    def test_supervisor_memory_store_persists_hermes_style_preferences(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "supervisor_memory.json"
            store = SupervisorMemoryStore.load(path)

            captured = store.observe_user_message(
                "Make the supervisor work like Hermes with memory and delegate_task to sub-agent workers.",
                CompanySettings(report_language="ko"),
            )
            store.save()
            loaded = SupervisorMemoryStore.load(path)

        self.assertTrue(captured)
        self.assertIn("supervisor_operating_model", loaded.items)
        self.assertIn("supervisor_memory_expected", loaded.items)
        self.assertIn("delegate_work_to_specialists", loaded.items)
        self.assertTrue(any("Hermes-style" in item.value for item in loaded.items.values()))

    def test_supervisor_session_store_persists_recent_dialogue_and_last_room(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "supervisor_session.json"
            store = SupervisorSessionStore.load(path)
            store.record_turn(
                user_message="hello",
                supervisor_reply="hi, I am the Supervisor",
                decision={"intent_type": "supervisor_chat"},
            )
            store.set_last_room("room_abc", "Zcash report")
            store.save()
            loaded = SupervisorSessionStore.load(path)

        recent = loaded.recent_dialogue()
        self.assertEqual(recent[-2]["role"], "user")
        self.assertEqual(recent[-1]["role"], "supervisor")
        self.assertEqual(loaded.last_room_id, "room_abc")
        self.assertTrue(any("room_abc" in line for line in loaded.memory_summary_lines()))

    def test_supervisor_brain_prepares_turn_with_memory_and_session_context(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = SupervisorBrain.for_memory_path(root / "memory.json")
            brain.session_store.record_turn(
                user_message="previous",
                supervisor_reply="previous reply",
                decision={"intent_type": "supervisor_chat"},
            )
            brain.session_store.save()

            turn = brain.prepare_turn(
                "Make supervisor act like Hermes with memory.",
                "Make supervisor act like Hermes with memory.",
                CompanySettings(report_language="ko"),
            )
            brain.record_reply(turn, ["ok"], room_id="room_xyz", topic="Hermes supervisor")
            reloaded = SupervisorBrain.for_memory_path(root / "memory.json")

        self.assertFalse(turn.decision.needs_research_room)
        self.assertTrue(turn.memory_context)
        self.assertTrue(turn.recent_dialogue)
        self.assertIn("supervisor_operating_model", turn.captured_memory_keys)
        self.assertEqual(reloaded.session_store.last_room_id, "room_xyz")
        self.assertIn("last_research_room", reloaded.memory_store.items)

    def test_chat_intake_status_shows_settings_without_report(self) -> None:
        output = StringIO()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = argparse.Namespace(
                memory=str(root / "memory.json"),
                vault=str(root / "vault"),
                reports=str(root / "reports"),
                skip_model_setup=True,
            )
            with patch.dict("os.environ", {"JIMMORIA_MODEL_SETTINGS_PATH": str(root / "model_settings.json")}, clear=True):
                with patch("sys.stdin.isatty", return_value=True):
                    with patch("builtins.input", side_effect=["현재 회사 상태랑 설정 보여줘", "/quit"]):
                        with redirect_stdout(output):
                            chat_command(args)

            self.assertFalse((root / "reports").exists())

        text = output.getvalue()
        self.assertIn("슈퍼바이저", text)
        self.assertNotIn("Supervisor intake", text)
        self.assertIn("Company settings", text)

    def test_chat_saved_report_request_prints_existing_report_without_room(self) -> None:
        output = StringIO()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "reports" / "3jane-dossier.md"
            run_dir = root / "runs" / "room_3jane"
            report_path.parent.mkdir(parents=True)
            run_dir.mkdir(parents=True)
            report_path.write_text("# 3jane Report\n\nFull dossier body.", encoding="utf-8")
            (run_dir / "room.json").write_text(
                json.dumps(
                    {
                        "room_id": "room_3jane",
                        "topic": "3jane crypto project research",
                        "status": "completed",
                        "output_paths": {"report": str(report_path)},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                memory=str(root / "memory.json"),
                vault=str(root / "vault"),
                reports=str(root / "reports"),
                skip_model_setup=True,
            )
            with patch.dict("os.environ", {"JIMMORIA_MODEL_SETTINGS_PATH": str(root / "model_settings.json")}, clear=True):
                with patch("sys.stdin.isatty", return_value=True):
                    with patch("builtins.input", side_effect=["3jane 보고서 만든거 보내봐 전체", "/quit"]):
                        with redirect_stdout(output):
                            chat_command(args)

            self.assertFalse((run_dir / "messages.json").exists())

        text = output.getvalue()
        self.assertIn("Saved report", text)
        self.assertIn("# 3jane Report", text)
        self.assertIn("Full dossier body.", text)
        self.assertNotIn("JIMMORIA opens a Research Room", text)

    def test_chat_research_request_can_be_cancelled_at_supervisor_check(self) -> None:
        output = StringIO()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = argparse.Namespace(
                memory=str(root / "memory.json"),
                vault=str(root / "vault"),
                reports=str(root / "reports"),
                skip_model_setup=True,
            )
            with patch.dict("os.environ", {"JIMMORIA_MODEL_SETTINGS_PATH": str(root / "model_settings.json")}, clear=True):
                with patch("sys.stdin.isatty", return_value=True):
                    with patch("builtins.input", side_effect=["pearl 프로젝트 리서치 보고서 작성해봐 https://x.com/pearl", "n", "/quit"]):
                        with redirect_stdout(output):
                            chat_command(args)

            self.assertFalse((root / "reports").exists())
            self.assertFalse((root / "runs").exists())

        text = output.getvalue()
        self.assertIn("Supervisor check", text)
        self.assertIn("Research Room은 열지 않겠습니다", text)
        self.assertNotIn("Room > OPEN", text)

    def test_chat_research_without_report_request_keeps_room_closed(self) -> None:
        output = StringIO()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = argparse.Namespace(
                memory=str(root / "memory.json"),
                vault=str(root / "vault"),
                reports=str(root / "reports"),
                skip_model_setup=True,
            )
            with patch.dict("os.environ", {"JIMMORIA_MODEL_SETTINGS_PATH": str(root / "model_settings.json")}, clear=True):
                with patch("sys.stdin.isatty", return_value=True):
                    with patch("builtins.input", side_effect=["pearl 프로젝트에 대해서 리서치 진행해봐", "/quit"]):
                        with redirect_stdout(output):
                            chat_command(args)

            self.assertFalse((root / "reports").exists())
            self.assertFalse((root / "runs").exists())

        text = output.getvalue()
        self.assertIn("최소 1개 링크", text)
        self.assertNotIn("Room > OPEN", text)

    def test_chat_report_retrieval_miss_can_be_corrected_to_creation(self) -> None:
        output = StringIO()
        fake_result = types.SimpleNamespace(
            room=types.SimpleNamespace(
                room_id="room_followup",
                status="completed",
                output_paths={},
                project_card={},
            ),
            memory=types.SimpleNamespace(get_room_findings=lambda room_id: []),
            bus=types.SimpleNamespace(messages=[]),
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = argparse.Namespace(
                memory=str(root / "memory.json"),
                vault=str(root / "vault"),
                reports=str(root / "reports"),
                skip_model_setup=True,
            )
            with patch.dict("os.environ", {"JIMMORIA_MODEL_SETTINGS_PATH": str(root / "model_settings.json")}, clear=True):
                with patch("sys.stdin.isatty", return_value=True):
                    with patch(
                        "builtins.input",
                        side_effect=["3jane 보고서 들고와봐", "만들어보라는거잖아 https://x.com/3jane", "y", "/quit"],
                    ):
                        with patch(
                            "crypto_research_agents.cli.ResearchRuntime.run_article_research",
                            return_value=fake_result,
                        ) as run_article:
                            with redirect_stdout(output):
                                chat_command(args)

            self.assertTrue(run_article.called)
            self.assertIn("3jane", run_article.call_args.kwargs["title"])

        text = output.getvalue()
        self.assertIn("저장된 보고서를 찾지 못했습니다", text)
        self.assertIn("Research Room", text)

    def test_chat_confirmed_report_skips_duplicate_dispatch_reply(self) -> None:
        output = StringIO()
        fake_result = types.SimpleNamespace(
            room=types.SimpleNamespace(
                room_id="room_confirmed",
                status="completed",
                output_paths={},
                project_card={},
            ),
            memory=types.SimpleNamespace(get_room_findings=lambda room_id: []),
            bus=types.SimpleNamespace(messages=[]),
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = argparse.Namespace(
                memory=str(root / "memory.json"),
                vault=str(root / "vault"),
                reports=str(root / "reports"),
                skip_model_setup=True,
            )
            with patch.dict("os.environ", {"JIMMORIA_MODEL_SETTINGS_PATH": str(root / "model_settings.json")}, clear=True):
                with patch("sys.stdin.isatty", return_value=True):
                    with patch("builtins.input", side_effect=["3jane 관련 투자 보고서 만들어봐 https://x.com/3jane", "y", "/quit"]):
                        with patch(
                            "crypto_research_agents.cli.ResearchRuntime.run_article_research",
                            return_value=fake_result,
                        ) as run_article:
                            with redirect_stdout(output):
                                chat_command(args)

            self.assertTrue(run_article.called)

        text = output.getvalue()
        self.assertIn("Supervisor check", text)
        self.assertNotIn("이건 리서치 요청으로 판단했습니다", text)
        self.assertNotIn("작업을 배정하겠습니다", text)
        self.assertNotIn("제가 먼저 목표와 우선순위를", text)

    def test_chat_research_slash_command_runs_staged_report_workflow(self) -> None:
        output = StringIO()
        fake_result = types.SimpleNamespace(
            room=types.SimpleNamespace(
                room_id="room_slash",
                status="completed",
                output_paths={},
                project_card={},
            ),
            memory=types.SimpleNamespace(get_room_findings=lambda room_id: []),
            bus=types.SimpleNamespace(messages=[]),
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = argparse.Namespace(
                memory=str(root / "memory.json"),
                vault=str(root / "vault"),
                reports=str(root / "reports"),
                skip_model_setup=True,
            )
            with patch.dict("os.environ", {"JIMMORIA_MODEL_SETTINGS_PATH": str(root / "model_settings.json")}, clear=True):
                with patch("sys.stdin.isatty", return_value=True):
                    with patch("builtins.input", side_effect=["/research https://z.cash/ 최근 ZEC 하락 보고서", "y", "/quit"]):
                        with patch(
                            "crypto_research_agents.cli.ResearchRuntime.run_article_research",
                            return_value=fake_result,
                        ) as run_article:
                            with redirect_stdout(output):
                                chat_command(args)

            self.assertTrue(run_article.called)
            kwargs = run_article.call_args.kwargs
            self.assertIn("z.cash", kwargs["title"])
            self.assertEqual(kwargs["url"], "https://z.cash/")
            self.assertEqual(kwargs["intake_decision"]["intent_type"], "research_request")
            self.assertEqual(kwargs["intake_decision"]["output_mode"], "research_dossier")
            self.assertEqual(kwargs["job_contract"]["loop_mode"], "closed_fleet")
            self.assertEqual(kwargs["job_contract"]["ui_policy"]["visible_mode"], "fixed_agent_dashboard")
            self.assertEqual(max_agent_attempts_from_contract(kwargs["job_contract"]), 2)

        text = output.getvalue()
        self.assertIn("보고서 작성 워크플로우", text)
        self.assertIn("1. 입력 해석", text)
        self.assertIn("Research Room", text)

    def test_runtime_records_supervisor_intake_decision(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = decide_supervisor_intake("pearl 프로젝트 리서치 보고서 작성해봐 https://x.com/pearl").to_dict()
            with patch.dict("os.environ", {**_offline_no_secret_env(), "JIMMORIA_SKIP_EXTERNAL_SEARCH": "1"}, clear=False):
                runtime = ResearchRuntime()
                result = runtime.run_article_research(
                    title="Pearl",
                    content="Pearl Proof-of-Useful-Work project",
                    vault_dir=root / "vault",
                    reports_dir=root / "reports",
                    memory_path=root / "memory.json",
                    intake_decision=decision,
                )

            findings = result.memory.get_room_findings(result.room.room_id)
            supervision = [item for item in findings if item.finding_type == "supervision_plan"]
            self.assertTrue(supervision)
            self.assertEqual(supervision[0].data["intake_decision"]["intent_type"], "research_request")
            self.assertEqual(supervision[0].data["intake_decision"]["output_mode"], "research_dossier")
            contract = result.room.project_card["job_contract"]
            self.assertEqual(contract["loop_mode"], "closed_fleet")
            self.assertEqual(contract["process_id"], "project_research_room")
            self.assertEqual(contract["output_mode"], "research_dossier")
            self.assertEqual(contract["ui_policy"]["visible_mode"], "fixed_agent_dashboard")
            self.assertEqual(supervision[0].data["job_contract"]["loop_mode"], "closed_fleet")
            self.assertEqual(supervision[0].data["orchestration_plan"]["loop_mode"], "closed_fleet")
            self.assertEqual(supervision[0].data["orchestration_plan"]["extension_policy"]["architecture_rule"], "narrow_waist")
            specialist_requests = [
                message
                for message in result.bus.messages
                if message.from_agent == "supervisor_agent" and message.to_agent != "supervisor_agent"
            ]
            self.assertTrue(specialist_requests)
            first_context = specialist_requests[0].context
            self.assertEqual(first_context["job_contract"]["loop_mode"], "closed_fleet")
            self.assertTrue(first_context["delegation_policy"]["requires_explicit_handoff_context"])
            self.assertIn("verification_gates", first_context)

    def test_live_agent_board_shows_current_work(self) -> None:
        output = StringIO()
        console = JimmoriaConsole()
        console.event_style = "cards"

        with redirect_stdout(output):
            console.handle_event(
                {
                    "type": "room_created",
                    "room_id": "room_test",
                    "topic": "pearl pow project",
                    "goals": ["Investigate the project."],
                    "agents": ["supervisor_agent", "ingestion_agent"],
                }
            )
            console.handle_event(
                {
                    "type": "agent_start",
                    "room_id": "room_test",
                    "agent_id": "ingestion_agent",
                    "task_type": "source_ingestion",
                }
            )

        text = output.getvalue()
        self.assertIn("Live agent board", text)
        self.assertIn("WAIT", text)
        self.assertIn("RUN", text)
        self.assertIn("ingestion_agent", text)
        self.assertIn("진행: 입력 소스와 메타데이터를 정리하는 중", text)
        self.assertEqual(text.count("Live agent board"), 1)

    def test_live_agent_board_shows_failures(self) -> None:
        output = StringIO()
        console = JimmoriaConsole()
        console.event_style = "cards"

        with redirect_stdout(output):
            console.handle_event(
                {
                    "type": "room_created",
                    "room_id": "room_test",
                    "topic": "pearl pow project",
                    "goals": ["Investigate the project."],
                    "agents": ["ingestion_agent"],
                }
            )
            console.handle_event(
                {
                    "type": "agent_failed",
                    "room_id": "room_test",
                    "agent_id": "ingestion_agent",
                    "task_type": "source_ingestion",
                    "error": "Codex CLI provider failed",
                }
            )

        text = output.getvalue()
        self.assertIn("FAIL", text)
        self.assertIn("중단: Codex CLI provider failed", text)

    def test_runtime_events_default_to_agent_dashboard_on_windows(self) -> None:
        output = StringIO()
        console = JimmoriaConsole()

        with redirect_stdout(output):
            console.handle_event(
                {
                    "type": "room_created",
                    "room_id": "room_test",
                    "topic": "pearl pow project",
                    "goals": ["Investigate the project."],
                    "agents": ["supervisor_agent", "ingestion_agent"],
                }
            )
            console.handle_event(
                {
                    "type": "agent_start",
                    "room_id": "room_test",
                    "agent_id": "ingestion_agent",
                    "task_type": "source_ingestion",
                }
            )
            console.handle_event(
                {
                    "type": "agent_done",
                    "room_id": "room_test",
                    "agent_id": "ingestion_agent",
                    "summary": "Source stored and summarized.",
                    "messages": 2,
                    "findings": 3,
                    "duration_ms": 1234,
                    "llm_usage": {
                        "calls": 2,
                        "total_tokens": 4200,
                        "estimated": True,
                    },
                }
            )

        text = output.getvalue()
        self.assertIn("JIMMORIA opens a Research Room", text)
        self.assertIn("Live agent board", text)
        self.assertIn("The Archivist (ingestion_agent) started", text)
        self.assertIn("The Archivist (ingestion_agent) finished", text)
        self.assertIn("Metrics: time 1.2s | llm 2 calls / ~4.2k tokens", text)
        self.assertNotIn("룸 > OPEN room_test", text)
        self.assertNotIn("Tool >", text)
        self.assertEqual(console.runtime_dock_lines, 0)
        self.assertEqual(console.last_room_id, "room_test")
        self.assertEqual(console.agent_state["ingestion_agent"], "done")

    def test_runtime_compact_hides_internal_supervisor_tool_events(self) -> None:
        output = StringIO()
        console = JimmoriaConsole()
        console.event_style = "compact"

        with redirect_stdout(output):
            console.handle_event(
                {
                    "type": "room_created",
                    "room_id": "room_test",
                    "topic": "pearl pow project",
                    "goals": ["Investigate the project."],
                    "agents": ["supervisor_agent", "discovery_agent"],
                }
            )
            console.handle_event(
                {
                    "type": "tool_start",
                    "room_id": "room_test",
                    "agent_id": "supervisor_agent",
                    "tool_name": "create_research_room",
                    "input_preview": "{'topic': 'pearl pow project'}",
                }
            )
            console.handle_event(
                {
                    "type": "tool_done",
                    "room_id": "room_test",
                    "agent_id": "supervisor_agent",
                    "tool_name": "create_research_room",
                    "summary": "Supervisor office opened room_test.",
                    "latency_ms": 1,
                }
            )
            console.handle_event(
                {
                    "type": "tool_start",
                    "room_id": "room_test",
                    "agent_id": "supervisor_agent",
                    "tool_name": "create_task",
                    "input_preview": "{'task_id': 'write_dossier', 'description': 'Write the Korean report.'}",
                }
            )
            console.handle_event(
                {
                    "type": "tool_done",
                    "room_id": "room_test",
                    "agent_id": "supervisor_agent",
                    "tool_name": "create_task",
                    "summary": "Task write_dossier created.",
                    "latency_ms": 0,
                }
            )
            console.handle_event(
                {
                    "type": "tool_start",
                    "room_id": "room_test",
                    "agent_id": "discovery_agent",
                    "tool_name": "web_search",
                    "input_preview": "{'query': 'pearl crypto project official'}",
                }
            )

        text = output.getvalue()
        self.assertNotIn("Tool >", text)
        self.assertNotIn("create_research_room", text)
        self.assertNotIn("create_task", text)
        self.assertIn("작업 > 스카우터 | RUN web_search - pearl crypto project official", text)
        self.assertEqual(console.agent_activity["supervisor_agent"], "작업 카드를 정리하는 중")
        self.assertEqual(
            console.agent_activity["discovery_agent"],
            "툴 실행: web_search - pearl crypto project official",
        )

    def test_runtime_compact_style_does_not_open_live_dock(self) -> None:
        output = StringIO()

        with patch.dict("os.environ", {"JIMMORIA_FORCE_RUNTIME_DOCK": "1", "JIMMORIA_EVENT_STYLE": "compact"}, clear=False):
            console = JimmoriaConsole()
            with patch("crypto_research_agents.console.supports_color", return_value=True):
                with redirect_stdout(output):
                    console.handle_event(
                        {
                            "type": "room_created",
                            "room_id": "room_test",
                            "topic": "pearl pow project",
                            "goals": ["Investigate the project."],
                            "agents": ["supervisor_agent", "ingestion_agent"],
                        }
                    )

        text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", output.getvalue())
        self.assertIn("룸 > OPEN room_test", text)
        self.assertIn("상태 > 대기: 슈퍼바이저, 아카이비스트", text)
        self.assertNotIn("JIMMORIA HQ", text)
        self.assertEqual(console.runtime_dock_lines, 0)

    def test_runtime_stream_event_style_prints_compact_log(self) -> None:
        output = StringIO()
        console = JimmoriaConsole()
        console.event_style = "stream"

        with redirect_stdout(output):
            console.handle_event(
                {
                    "type": "room_created",
                    "room_id": "room_test",
                    "topic": "pearl pow project",
                    "goals": ["Investigate the project."],
                    "agents": ["supervisor_agent", "ingestion_agent"],
                }
            )
            console.handle_event(
                {
                    "type": "agent_start",
                    "room_id": "room_test",
                    "agent_id": "ingestion_agent",
                    "task_type": "source_ingestion",
                }
            )
            console.handle_event(
                {
                    "type": "agent_done",
                    "room_id": "room_test",
                    "agent_id": "ingestion_agent",
                    "summary": "Source stored and summarized.",
                    "messages": 2,
                    "findings": 3,
                    "duration_ms": 1234,
                    "llm_usage": {
                        "calls": 2,
                        "total_tokens": 4200,
                        "estimated": True,
                    },
                }
            )

        text = output.getvalue()
        self.assertIn("룸 > OPEN room_test", text)
        self.assertIn("보드 > 대기 2/완료 0", text)
        self.assertIn("에이전트 > RUN 아카이비스트", text)
        self.assertIn("에이전트 > DONE 아카이비스트", text)
        self.assertIn("time 1.2s", text)
        self.assertIn("llm 2", text)
        self.assertIn("calls / ~4.2k tokens", text)
        self.assertNotIn("JIMMORIA opens a Research Room", text)
        self.assertNotIn("Live agent board", text)

    def test_runtime_stream_keeps_input_dock_visible_during_room(self) -> None:
        output = StringIO()

        with patch.dict("os.environ", {"JIMMORIA_FORCE_RUNTIME_DOCK": "1", "JIMMORIA_EVENT_STYLE": "dock"}, clear=False):
            console = JimmoriaConsole()
            with patch("crypto_research_agents.console.supports_color", return_value=True):
                with redirect_stdout(output):
                    console.handle_event(
                        {
                            "type": "room_created",
                            "room_id": "room_test",
                            "topic": "pearl pow project",
                            "goals": ["Investigate the project."],
                            "agents": ["supervisor_agent", "ingestion_agent"],
                        }
                    )
                    console.handle_event(
                        {
                            "type": "agent_start",
                            "room_id": "room_test",
                            "agent_id": "ingestion_agent",
                            "task_type": "source_ingestion",
                        }
                    )

        clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", output.getvalue())
        self.assertIn("JIMMORIA HQ", clean)
        self.assertIn("리서치룸 실행 중입니다. 슈퍼바이저가 완료하면 입력창이 돌아옵니다.", clean)
        self.assertIn("토큰 사용: 0 tokens | LLM 호출: 0", clean)
        self.assertIn("진행: 아카이비스트 -> 입력 소스와 메타데이터를 정리하는 중", clean)
        self.assertIn("대기: 슈퍼바이저", clean)
        self.assertIn("전체 AI 에이전트 대시보드 - 현재 작업", clean)
        self.assertIn("상태", clean)
        self.assertIn("현재 작업", clean)
        self.assertIn("ingestion_agent", clean)
        self.assertIn("진행: 입력 소스와 메타데이터를 정리하는 중", clean)
        self.assertIn("> 작업중...", clean)
        self.assertIn("\033[13A", output.getvalue())
        self.assertIn("\033[2K", output.getvalue())
        self.assertIn("\033[?25l", output.getvalue())
        self.assertIn("\033[5m\033[38;2;255;92;212m...", output.getvalue())
        self.assertEqual(console.runtime_dock_lines, 13)

    def test_runtime_dock_shows_full_agent_board_for_research_room(self) -> None:
        output = StringIO()

        with patch.dict("os.environ", {"JIMMORIA_FORCE_RUNTIME_DOCK": "1", "JIMMORIA_EVENT_STYLE": "dock"}, clear=False):
            console = JimmoriaConsole()
            console.width = 160
            with patch("crypto_research_agents.console.supports_color", return_value=True):
                with redirect_stdout(output):
                    console.handle_event(
                        {
                            "type": "room_created",
                            "room_id": "room_test",
                            "topic": "3jane report",
                            "goals": ["Investigate the project."],
                            "agents": [
                                "supervisor_agent",
                                "ingestion_agent",
                                "social_kol_agent",
                                "narrative_agent",
                                "discovery_agent",
                                "contract_onchain_agent",
                                "product_tech_agent",
                                "funding_token_agent",
                                "report_agent",
                                "obsidian_curator_agent",
                            ],
                        }
                    )
                    console.handle_event(
                        {
                            "type": "agent_start",
                            "room_id": "room_test",
                            "agent_id": "supervisor_agent",
                            "task_type": "supervision",
                        }
                    )

        clean = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", output.getvalue())
        self.assertIn("진행: 슈퍼바이저 -> 리서치 방향과 작업 순서를 정리하는 중", clean)
        self.assertIn("대기: 아카이비스트, 소셜/KOL, 내러티브, 스카우터 +5", clean)
        self.assertIn("상태", clean)
        self.assertIn("supervisor_agent", clean)
        self.assertIn("ingestion_agent", clean)
        self.assertIn("social_kol_agent", clean)
        self.assertIn("contract_onchain_agent", clean)
        self.assertIn("obsidian_curator_agent", clean)
        self.assertIn("진행: 리서치 방향과 작업 순서를 정리하는 중", clean)
        self.assertIn("대기: 볼트 노트와 지식 기록을 동기화하는 중", clean)
        self.assertNotIn("+--------------------------------------------------------+", clean)
        self.assertEqual(console.runtime_dock_lines, 21)

    def test_runtime_dock_frame_lines_keep_equal_display_width(self) -> None:
        output = StringIO()

        with patch.dict("os.environ", {"JIMMORIA_FORCE_RUNTIME_DOCK": "1", "JIMMORIA_EVENT_STYLE": "dock"}, clear=False):
            console = JimmoriaConsole()
            console.width = 150
            with patch("crypto_research_agents.console.supports_color", return_value=True):
                with redirect_stdout(output):
                    console.handle_event(
                        {
                            "type": "room_created",
                            "room_id": "room_test",
                            "topic": "border stability test",
                            "goals": ["Keep borders aligned."],
                            "agents": [
                                "supervisor_agent",
                                "ingestion_agent",
                                "social_kol_agent",
                                "narrative_agent",
                                "discovery_agent",
                                "contract_onchain_agent",
                                "product_tech_agent",
                                "funding_token_agent",
                                "report_agent",
                                "obsidian_curator_agent",
                            ],
                        }
                    )

        clean = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", output.getvalue()).replace("\r", "")
        frame_lines = [line for line in clean.splitlines() if line.startswith(("+", "|"))]
        self.assertTrue(frame_lines)
        self.assertTrue(all(display_width(line) == console.input_box_width() for line in frame_lines))
        self.assertFalse(any("+---" in line[2:-2] for line in frame_lines))

    def test_runtime_dock_rejects_nested_frame_lines(self) -> None:
        console = JimmoriaConsole()
        console.width = 150
        bad_line = console.input_text_line("+--------------------+   +--------------------+")
        good_line = console.input_text_line("진행    스카우터          discovery_agent             공식 후보 확인 중")

        self.assertFalse(console.runtime_dock_line_is_stable(bad_line))
        self.assertTrue(console.runtime_dock_line_is_stable(good_line))

        stable = console.stable_runtime_dock_lines([bad_line, good_line])
        self.assertEqual(len(stable), 2)
        self.assertTrue(all(display_width(line) == console.input_box_width() for line in stable))
        self.assertNotIn("+--------------------+", stable[0])


    def test_runtime_dock_shows_council_room_card(self) -> None:
        output = StringIO()

        with patch.dict("os.environ", {"JIMMORIA_FORCE_RUNTIME_DOCK": "1", "JIMMORIA_EVENT_STYLE": "dock"}, clear=False):
            console = JimmoriaConsole()
            with patch("crypto_research_agents.console.supports_color", return_value=True):
                with redirect_stdout(output):
                    console.handle_event(
                        {
                            "type": "room_created",
                            "room_id": "room_test",
                            "topic": "3jane report",
                            "goals": ["Investigate the project."],
                            "agents": ["social_kol_agent", "product_tech_agent"],
                        }
                    )
                    console.handle_event(
                        {
                            "type": "deliberation_start",
                            "room_id": "room_test",
                            "participants": ["social_kol_agent", "product_tech_agent"],
                            "summary": "Specialists compare findings.",
                        }
                    )
                    console.handle_event(
                        {
                            "type": "deliberation_statement",
                            "room_id": "room_test",
                            "agent_id": "social_kol_agent",
                            "summary": "공식 X 신호는 약하지만 공개 기사 근거는 있습니다.",
                        }
                    )

        clean = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", output.getvalue())
        self.assertIn("진행    토론방", clean)
        self.assertIn("소셜/KOL, 제품/기술", clean)
        self.assertIn("소셜/KOL: 공식 X 신호는 약하지만 공개 기사 근거는 있습니다.", clean)

    def test_runtime_dock_updates_agent_work_from_tool_events(self) -> None:
        output = StringIO()

        with patch.dict("os.environ", {"JIMMORIA_FORCE_RUNTIME_DOCK": "1", "JIMMORIA_EVENT_STYLE": "dock"}, clear=False):
            console = JimmoriaConsole()
            with patch("crypto_research_agents.console.supports_color", return_value=True):
                with redirect_stdout(output):
                    console.handle_event(
                        {
                            "type": "room_created",
                            "room_id": "room_test",
                            "topic": "pearl pow project",
                            "goals": ["Investigate the project."],
                            "agents": ["discovery_agent"],
                        }
                    )
                    console.handle_event(
                        {
                            "type": "tool_start",
                            "room_id": "room_test",
                            "agent_id": "discovery_agent",
                            "tool_name": "web_search",
                            "input_preview": "pearl crypto project official",
                        }
                    )

        clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", output.getvalue())
        self.assertIn("전체 AI 에이전트 대시보드 - 현재 작업", clean)
        self.assertIn("discovery_agent", clean)
        self.assertIn("진행: 툴 실행: web_search - pearl crypto project", clean)
        self.assertEqual(console.agent_state["discovery_agent"], "running")

    def test_runtime_dock_accumulates_token_usage_in_header(self) -> None:
        output = StringIO()

        with patch.dict("os.environ", {"JIMMORIA_FORCE_RUNTIME_DOCK": "1", "JIMMORIA_EVENT_STYLE": "dock"}, clear=False):
            console = JimmoriaConsole()
            with patch("crypto_research_agents.console.supports_color", return_value=True):
                with redirect_stdout(output):
                    console.handle_event(
                        {
                            "type": "room_created",
                            "room_id": "room_test",
                            "topic": "token meter test",
                            "goals": ["Track tokens."],
                            "agents": ["ingestion_agent", "report_agent"],
                        }
                    )
                    console.handle_event(
                        {
                            "type": "agent_done",
                            "room_id": "room_test",
                            "agent_id": "ingestion_agent",
                            "summary": "Sources summarized.",
                            "messages": 2,
                            "findings": 3,
                            "llm_usage": {
                                "calls": 2,
                                "total_tokens": 4200,
                                "estimated": True,
                            },
                        }
                    )
                    console.handle_event(
                        {
                            "type": "agent_done",
                            "room_id": "room_test",
                            "agent_id": "report_agent",
                            "summary": "Report drafted.",
                            "messages": 3,
                            "findings": 4,
                            "llm_usage": {
                                "calls": 1,
                                "total_tokens": 1800,
                                "estimated": True,
                            },
                        }
                    )

        clean = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", output.getvalue())
        self.assertIn("토큰 사용: ~6.0k tokens | LLM 호출: 3 | 로그: 백단 저장", clean)
        self.assertEqual(console.runtime_usage_totals["calls"], 3)
        self.assertEqual(console.runtime_usage_totals["total_tokens"], 6000)

    def test_runtime_stream_clears_input_dock_when_room_finishes(self) -> None:
        output = StringIO()

        with patch.dict("os.environ", {"JIMMORIA_FORCE_RUNTIME_DOCK": "1", "JIMMORIA_EVENT_STYLE": "dock"}, clear=False):
            console = JimmoriaConsole()
            with patch("crypto_research_agents.console.supports_color", return_value=True):
                with redirect_stdout(output):
                    console.handle_event(
                        {
                            "type": "room_created",
                            "room_id": "room_test",
                            "topic": "pearl pow project",
                            "goals": ["Investigate the project."],
                            "agents": ["supervisor_agent"],
                        }
                    )
                    console.handle_event(
                        {
                            "type": "room_completed",
                            "room_id": "room_test",
                            "status": "completed",
                            "messages": 2,
                            "findings": 1,
                        }
                    )

        self.assertIn("룸", output.getvalue())
        self.assertIn("\033[?25h", output.getvalue())
        self.assertEqual(console.runtime_dock_lines, 0)
        self.assertFalse(console.runtime_room_running)

    def test_workboard_summarizes_multiple_rooms(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs_dir = root / "runs"
            room_dir = runs_dir / "room_alpha"
            room_dir.mkdir(parents=True)
            (room_dir / "room.json").write_text(
                json.dumps(
                    {
                        "room_id": "room_alpha",
                        "topic": "pearl pow project research",
                        "status": "completed",
                        "project_card": {"research_quality_status": "insufficient_evidence"},
                        "output_paths": {"report": str(root / "reports" / "pearl.md")},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (room_dir / "events.json").write_text(
                json.dumps(
                    [
                        {
                            "type": "room_created",
                            "room_id": "room_alpha",
                            "agents": ["supervisor_agent", "ingestion_agent"],
                        },
                        {"type": "agent_done", "agent_id": "supervisor_agent", "summary": "Planned."},
                        {"type": "agent_start", "agent_id": "ingestion_agent"},
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            output = StringIO()
            with patch.dict(os.environ, {"JIMMORIA_PLAIN_LOGS": "1"}):
                console = JimmoriaConsole(runs_dir=runs_dir)
                with redirect_stdout(output):
                    console.print_workboard(limit=5)

            text = output.getvalue()
            self.assertIn("Workload board", text)
            self.assertIn("room_alpha", text)
            self.assertIn("pearl pow project research", text)
            self.assertIn("insufficient_evidence", text)
            self.assertIn("ingestion_agent", text)

    def test_ax_style_event_resume_and_fork_cli(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs_dir = root / "runs"
            run_dir = runs_dir / "room_ax"
            run_dir.mkdir(parents=True)
            (run_dir / "room.json").write_text(
                json.dumps(
                    {
                        "room_id": "room_ax",
                        "topic": "AX-style resume test",
                        "status": "completed",
                        "output_paths": {},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (run_dir / "events.json").write_text(
                json.dumps(
                    [
                        {"seq": 1, "type": "room_created", "room_id": "room_ax"},
                        {"seq": 2, "type": "agent_start", "room_id": "room_ax", "agent_id": "supervisor_agent"},
                        {"seq": 3, "type": "agent_done", "room_id": "room_ax", "agent_id": "supervisor_agent", "summary": "done"},
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (run_dir / "messages.json").write_text("[]", encoding="utf-8")
            (run_dir / "tool_audit_log.json").write_text("[]", encoding="utf-8")
            (run_dir / "llm_call_log.json").write_text("[]", encoding="utf-8")

            output = StringIO()
            with redirect_stdout(output):
                cli_main(["events", "room_ax", "--runs-dir", str(runs_dir), "--after-seq", "1"])
            text = output.getvalue()
            self.assertNotIn("room_created", text)
            self.assertIn("seq=2", text)
            self.assertIn("seq=3", text)

            with redirect_stdout(StringIO()):
                cli_main(["fork", "room_ax", "--runs-dir", str(runs_dir), "--seq", "2", "--dest-room-id", "room_ax_fork"])

            forked_room = json.loads((runs_dir / "room_ax_fork" / "room.json").read_text(encoding="utf-8"))
            forked_events = json.loads((runs_dir / "room_ax_fork" / "events.json").read_text(encoding="utf-8"))

        self.assertEqual(forked_room["room_id"], "room_ax_fork")
        self.assertEqual(forked_room["parent_room_id"], "room_ax")
        self.assertEqual(forked_room["status"], "forked")
        self.assertEqual([event["seq"] for event in forked_events], [1, 2, 3])
        self.assertEqual(forked_events[-1]["type"], "run_forked")

    def test_events_after_seq_normalizes_legacy_event_logs(self) -> None:
        events = [{"type": "room_created"}, {"type": "agent_start"}, {"seq": 7, "type": "agent_done"}]
        resumed = events_after_seq(events, last_seq=1)

        self.assertEqual([event["type"] for event in resumed], ["agent_start", "agent_done"])
        self.assertEqual(resumed[0]["seq"], 2)
        self.assertEqual(resumed[1]["seq"], 7)

    def test_run_summary_labels_insufficient_evidence_as_diagnostic(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "report.md"
            report_path.write_text("# 리서치 미완료\n\n- Evidence URLs: 0\n", encoding="utf-8")
            result = types.SimpleNamespace(
                room=types.SimpleNamespace(
                    room_id="room_diag",
                    status="completed",
                    output_paths={"report": str(report_path)},
                    project_card={
                        "research_quality": {
                            "status": "insufficient_evidence",
                            "reasons": ["no source-backed evidence URLs were collected"],
                        }
                    },
                ),
                memory=types.SimpleNamespace(get_room_findings=lambda room_id: []),
                bus=types.SimpleNamespace(messages=[]),
            )
            output = StringIO()
            console = JimmoriaConsole(runs_dir=root / "runs")

            with redirect_stdout(output):
                console.print_run_summary(result)

        text = output.getvalue()
        self.assertIn("JIMMORIA diagnostic", text)
        self.assertIn("Diagnostic preview", text)
        self.assertNotIn("JIMMORIA response", text)
        self.assertNotIn("Report preview", text)

    def test_run_summary_defaults_to_report_preview_for_completed_research(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "report.md"
            report_path.write_text(
                "\n".join(
                    [
                        "# Project Research Dossier: 3Jane",
                        "## 1. TL;DR",
                        "- complete",
                        *[f"detail line {index}" for index in range(1, 16)],
                    ]
                ),
                encoding="utf-8",
            )
            result = types.SimpleNamespace(
                room=types.SimpleNamespace(
                    room_id="room_full",
                    status="completed",
                    output_paths={"report": str(report_path)},
                    project_card={
                        "research_quality": {
                            "status": "research_complete",
                        }
                    },
                ),
                memory=types.SimpleNamespace(get_room_findings=lambda room_id: []),
                bus=types.SimpleNamespace(messages=[]),
            )
            output = StringIO()
            console = JimmoriaConsole(runs_dir=root / "runs")
            console.agent_state = {
                "supervisor_agent": "done",
                "report_agent": "done",
            }
            console.agent_activity = {
                "supervisor_agent": "Done: plan ready",
                "report_agent": "Done: wrote the final dossier",
            }

            with redirect_stdout(output):
                console.print_run_summary(result)

        text = output.getvalue()
        self.assertIn("JIMMORIA response", text)
        self.assertIn("에이전트 완료 요약", text)
        self.assertIn("슈퍼바이저", text)
        self.assertIn("리포트", text)
        self.assertIn("Report preview", text)
        self.assertIn("Full report command: /report room_full", text)
        self.assertNotIn("+ Full report", text)
        self.assertNotIn("완료: Done:", text)
        self.assertNotIn("detail line 15", text)

    def test_run_summary_prints_full_report_when_requested(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "report.md"
            report_path.write_text(
                "\n".join(
                    [
                        "# Project Research Dossier: 3Jane",
                        "## 1. TL;DR",
                        "- complete",
                        *[f"detail line {index}" for index in range(1, 16)],
                    ]
                ),
                encoding="utf-8",
            )
            result = types.SimpleNamespace(
                room=types.SimpleNamespace(
                    room_id="room_full",
                    status="completed",
                    output_paths={"report": str(report_path)},
                    project_card={
                        "research_quality": {
                            "status": "research_complete",
                        }
                    },
                ),
                memory=types.SimpleNamespace(get_room_findings=lambda room_id: []),
                bus=types.SimpleNamespace(messages=[]),
            )
            output = StringIO()
            console = JimmoriaConsole(runs_dir=root / "runs")

            with patch.dict("os.environ", {"JIMMORIA_REPORT_DISPLAY": "full"}, clear=False):
                with redirect_stdout(output):
                    console.print_run_summary(result)

        text = output.getvalue()
        self.assertIn("JIMMORIA response", text)
        self.assertIn("Full report", text)
        self.assertIn("Full report command: /report room_full", text)
        self.assertIn("detail line 15", text)
        self.assertNotIn("Report preview", text)

    def test_article_research_loop_writes_outputs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict("os.environ", {"JIMMORIA_SKIP_EXTERNAL_SEARCH": "1"}, clear=False):
                runtime = ResearchRuntime()
                result = runtime.run_article_research(
                    title="AI Wallet Automation",
                    content="AI agent wallet automation with points and testnet docs.",
                    vault_dir=root / "vault",
                    reports_dir=root / "reports",
                    memory_path=root / "memory.json",
                )

            self.assertEqual(result.room.status, "completed")
            self.assertTrue(Path(result.room.output_paths["report"]).exists())
            self.assertTrue((root / "vault" / "50_Reports" / "AI-Wallet-Automation.md").exists())
            self.assertTrue((root / "runs" / result.room.room_id / "messages.json").exists())
            self.assertTrue((root / "runs" / result.room.room_id / "events.json").exists())
            self.assertTrue((root / "runs" / result.room.room_id / "llm_call_log.json").exists())
            events = json.loads((root / "runs" / result.room.room_id / "events.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(len(events), 10)
            self.assertEqual(events[0]["type"], "room_created")
            event_types = {event["type"] for event in events}
            self.assertIn("agent_hook", event_types)
            self.assertIn("tool_start", event_types)
            self.assertIn("tool_done", event_types)
            self.assertIn("finding_saved", event_types)
            self.assertIn("source_saved", event_types)
            self.assertIn("report_written", event_types)
            self.assertIn("deliberation_start", event_types)
            self.assertIn("deliberation_done", event_types)
            self.assertIn("final_review_start", event_types)
            self.assertIn("final_review_done", event_types)
            self.assertIn("orchestration_plan", event_types)
            hook_events = [event for event in events if event["type"] == "agent_hook"]
            self.assertTrue(hook_events)
            hook_phases = {event.get("hook_phase") for event in hook_events}
            self.assertIn("before_run", hook_phases)
            self.assertIn("quality_gate", hook_phases)
            self.assertIn("after_run", hook_phases)
            self.assertIn("before_tool_call", hook_phases)
            self.assertIn("after_tool_call", hook_phases)
            self.assertIn("before_report", hook_phases)
            self.assertIn("after_report", hook_phases)
            runtime_hook_events = {event.get("event") for event in runtime.hooks.events if "event" in event}
            self.assertIn("agent:start", runtime_hook_events)
            self.assertIn("tool:done", runtime_hook_events)
            self.assertIn("report:before_render", runtime_hook_events)
            self.assertIn("report:after_render", runtime_hook_events)
            self.assertIn(
                "supervisor_orchestration",
                runtime.agent_specs.get("supervisor_agent").skills,
            )
            agent_done_events = [event for event in events if event["type"] == "agent_done"]
            self.assertTrue(agent_done_events)
            self.assertTrue(all("duration_ms" in event for event in agent_done_events))
            self.assertTrue(all("llm_usage" in event for event in agent_done_events))
            room_done = next(event for event in events if event["type"] == "room_completed")
            self.assertIn("duration_ms", room_done)
            self.assertIn("llm_usage", room_done)
            self.assertGreaterEqual(room_done["llm_usage"]["calls"], 10)
            self.assertGreater(room_done["llm_usage"]["total_tokens"], 0)
            agent_start_order = [
                event.get("agent_id")
                for event in events
                if event["type"] == "agent_start"
            ]
            self.assertLess(agent_start_order.index("social_kol_agent"), agent_start_order.index("narrative_agent"))
            self.assertLess(agent_start_order.index("narrative_agent"), agent_start_order.index("discovery_agent"))
            plan_index = next(index for index, event in enumerate(events) if event["type"] == "orchestration_plan")
            supervisor_done_index = next(
                index
                for index, event in enumerate(events)
                if event["type"] == "agent_done" and event.get("agent_id") == "supervisor_agent"
            )
            self.assertLess(plan_index, supervisor_done_index)
            audit = json.loads((root / "runs" / result.room.room_id / "tool_audit_log.json").read_text(encoding="utf-8"))
            supervisor_tools = [
                item["tool_name"]
                for item in audit
                if item["agent_id"] == "supervisor_agent"
            ]
            self.assertIn("create_research_room", supervisor_tools)
            self.assertIn("create_task", supervisor_tools)
            self.assertIn("assign_task", supervisor_tools)
            self.assertIn("agent_handoff", supervisor_tools)
            supervisor_findings = [
                finding
                for finding in result.memory.get_room_findings(result.room.room_id)
                if finding.agent_id == "supervisor_agent" and finding.finding_type == "supervision_plan"
            ]
            self.assertTrue(supervisor_findings)
            orchestration_plan = supervisor_findings[0].data["orchestration_plan"]
            self.assertEqual(orchestration_plan["mode"], "supervisor_orchestrator")
            self.assertIn("agent_council", [item["checkpoint"] for item in orchestration_plan["coordination_checkpoints"]])
            self.assertGreaterEqual(len(runtime.model_gateway.call_log), 10)
            llm_log = json.loads((root / "runs" / result.room.room_id / "llm_call_log.json").read_text(encoding="utf-8"))
            self.assertTrue(all("duration_ms" in entry for entry in llm_log))
            self.assertTrue(all("token_usage" in entry for entry in llm_log))
            self.assertTrue(all(entry["token_usage"]["total_tokens"] > 0 for entry in llm_log))
            self.assertIn("runtime_metrics", result.room.project_card)
            self.assertGreaterEqual(result.room.project_card["runtime_metrics"]["llm_usage"]["calls"], 10)
            llm_agents = {entry["agent_id"] for entry in llm_log}
            self.assertTrue(
                {
                    "supervisor_agent",
                    "ingestion_agent",
                    "narrative_agent",
                    "discovery_agent",
                    "social_kol_agent",
                    "contract_onchain_agent",
                    "product_tech_agent",
                    "funding_token_agent",
                    "report_agent",
                    "obsidian_curator_agent",
                }.issubset(llm_agents)
            )
            reasoning_tasks = {
                entry["task_type"]: entry["selected_model"]
                for entry in llm_log
                if entry["task_type"] in {"source_ingestion", "candidate_discovery", "social_summary", "contract_info", "product_docs", "funding_token", "obsidian_sync"}
            }
            self.assertEqual(set(reasoning_tasks.values()), {"gpt-5.5"})
            reasoning_efforts = {
                entry["task_type"]: entry.get("reasoning_effort")
                for entry in llm_log
                if entry["task_type"] in {"source_ingestion", "candidate_discovery", "social_summary", "contract_info", "product_docs", "funding_token", "report_writing", "final_synthesis"}
            }
            self.assertEqual(set(reasoning_efforts.values()), {"pro"})
            self.assertGreaterEqual(len(result.bus.messages), 8)
            self.assertGreaterEqual(len(result.memory.get_room_findings(result.room.room_id)), 8)
            finding_types = {
                finding.finding_type
                for finding in result.memory.get_room_findings(result.room.room_id)
            }
            self.assertIn("agent_council_consensus", finding_types)
            self.assertIn("final_supervisor_review", finding_types)
            self.assertIn("agent_council", {message.from_agent for message in result.bus.messages})
            self.assertEqual(result.room.project_card["agent_council"]["decision"], "write_diagnostic_memo")
            self.assertEqual(result.room.project_card["supervisor_final_review"]["delivery_mode"], "diagnostic_memo")

            report = Path(result.room.output_paths["report"]).read_text(encoding="utf-8")
            self.assertEqual(result.room.project_card["research_quality_status"], "insufficient_evidence")
            self.assertIn("## 0. Research Quality Gate", report)
            self.assertIn("Status: `INSUFFICIENT_EVIDENCE`", report)
            self.assertIn("This is not a completed research report.", report)
            self.assertIn("| Project | Origin | Source Backing |", report)
            self.assertIn("mvp_placeholder", report)
            self.assertIn("[MVP Placeholder]", report)
            self.assertIn("LLM provider: `offline_fallback`", report)
            self.assertIn("Live LLM: not configured", report)
            self.assertNotIn("Supervisor Final Review", report)
            self.assertEqual(result.room.project_card["supervisor_final_review"]["delivery_mode"], "diagnostic_memo")

            project_notes = list((root / "vault" / "10_Projects").glob("*.md"))
            self.assertTrue(project_notes)
            note_text = project_notes[0].read_text(encoding="utf-8")
            self.assertIn("candidate_origin: mvp_placeholder", note_text)
            self.assertIn("source_backing: narrative_seed_only", note_text)

    def test_report_only_retention_keeps_report_and_deletes_room_data(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict("os.environ", {"JIMMORIA_SKIP_EXTERNAL_SEARCH": "1"}, clear=False):
                runtime = ResearchRuntime()
                result = runtime.run_article_research(
                    title="AI Wallet Automation",
                    content="AI agent wallet automation with points and testnet docs.",
                    vault_dir=root / "vault",
                    reports_dir=root / "reports",
                    memory_path=root / "memory.json",
                    retention_policy="report_only",
                )

            report_path = Path(result.room.output_paths["report"])
            self.assertTrue(report_path.exists())
            self.assertFalse((root / "runs" / result.room.room_id).exists())
            self.assertFalse((root / "memory.json").exists())
            self.assertEqual(result.room.project_card["run_retention"]["policy"], "report_only")
            self.assertTrue(result.room.project_card["run_retention"]["run_snapshot_deleted"])
            self.assertTrue(result.room.project_card["run_retention"]["memory_deleted"])
            index = load_report_index(root / "runs")
            self.assertEqual(index[0]["room_id"], result.room.room_id)
            self.assertEqual(index[0]["report"], str(report_path))

            found = find_saved_report_for_request(
                "AI Wallet Automation report",
                runs_dir=root / "runs",
                reports_dir=root / "reports",
            )
            self.assertIsNotNone(found)
            assert found is not None
            self.assertEqual(found[0], report_path)
            self.assertEqual(found[1], result.room.room_id)

    def test_previous_report_context_uses_report_index_after_room_cleanup(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "reports" / "3jane-room_old.md"
            report_path.parent.mkdir(parents=True)
            report_path.write_text("# 3Jane Report\n\n이전 리서치 핵심 내용.", encoding="utf-8")
            (root / "report_index.json").write_text(
                json.dumps(
                    [
                        {
                            "room_id": "room_old",
                            "topic": "3jane 보고서 다시 만들어봐",
                            "status": "completed",
                            "report": str(report_path),
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            context = previous_report_context(
                "3jane 보고서 다시 만들어봐",
                runs_dir=root / "runs",
                reports_dir=root / "reports",
            )

            self.assertIn("[Previous JIMMORIA report context]", context)
            self.assertIn("room_old", context)
            self.assertIn("이전 리서치 핵심 내용", context)

    def test_report_uses_korean_company_setting(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = CompanySettings(report_language="ko")
            (root / "company_settings.json").write_text(
                json.dumps(settings.to_dict(), ensure_ascii=False),
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"JIMMORIA_SKIP_EXTERNAL_SEARCH": "1"}, clear=False):
                runtime = ResearchRuntime()
                result = runtime.run_article_research(
                    title="AI Wallet Automation",
                    content="AI agent wallet automation with points and testnet docs.",
                    vault_dir=root / "vault",
                    reports_dir=root / "reports",
                    memory_path=root / "memory.json",
                )

            report = Path(result.room.output_paths["report"]).read_text(encoding="utf-8")
            self.assertIn("# 리서치 미완료 / Research Not Completed", report)
            self.assertIn("## 0. Research Quality Gate", report)
            self.assertIn("Status: `INSUFFICIENT_EVIDENCE`", report)
            self.assertIn("## 2. 목표", report)
            self.assertIn("Report language: `ko`", report)
            self.assertEqual(result.room.project_card["research_quality_status"], "insufficient_evidence")

    def test_default_connectors_register_low_cost_research_stack(self) -> None:
        runtime = ResearchRuntime()

        self.assertIn("fetch_url", runtime.tool_gateway.registered_tools)
        self.assertIn("crawl_website", runtime.tool_gateway.registered_tools)
        self.assertIn("crawl_docs", runtime.tool_gateway.registered_tools)
        self.assertIn("web_search", runtime.tool_gateway.registered_tools)
        self.assertIn("github_search_repos", runtime.tool_gateway.registered_tools)
        self.assertIn("read_github_repo", runtime.tool_gateway.registered_tools)
        self.assertIn("github_get_repo_activity", runtime.tool_gateway.registered_tools)
        self.assertIn("rss_monitor_feed", runtime.tool_gateway.registered_tools)
        self.assertIn("defillama_protocol_search", runtime.tool_gateway.registered_tools)
        self.assertIn("defillama_tvl_snapshot", runtime.tool_gateway.registered_tools)
        self.assertIn("snapshot_get_proposals", runtime.tool_gateway.registered_tools)
        self.assertIn("dexscreener_search_pairs", runtime.tool_gateway.registered_tools)
        self.assertIn("coingecko_coin_metadata", runtime.tool_gateway.registered_tools)
        self.assertIn("create_research_room", runtime.tool_gateway.registered_tools)
        self.assertIn("create_task", runtime.tool_gateway.registered_tools)
        self.assertIn("assign_task", runtime.tool_gateway.registered_tools)
        self.assertIn("agent_handoff", runtime.tool_gateway.registered_tools)
        self.assertIn("update_task_status", runtime.tool_gateway.registered_tools)
        self.assertIn("url_safety_check", runtime.tool_gateway.registered_tools)
        self.assertIn("source_relevance_filter", runtime.tool_gateway.registered_tools)
        self.assertIn("tool_call_guardrail", runtime.tool_gateway.registered_tools)

    def test_parse_html_connector_extracts_official_links(self) -> None:
        policy = PolicyEngine()
        policy.allow("ingestion_agent", "parse_html")
        gateway = ToolGateway(policy)
        register_default_connectors(gateway)

        result = gateway.call(
            "ingestion_agent",
            "parse_html",
            html=(
                "<html><head><title>Pearl</title><meta name='description' content='testnet docs'>"
                "</head><body><a href='/docs'>Docs</a><a href='https://github.com/pearl-labs/app'>GitHub</a>"
                "<a href='https://x.com/pearl'>X</a><p>Join the waitlist and points campaign.</p></body></html>"
            ),
            base_url="https://pearl.example",
        )

        data = result["data"]
        self.assertEqual(result["status"], "success")
        self.assertEqual(data["title"], "Pearl")
        self.assertIn("waitlist", data["signals"]["stage"])
        self.assertIn("points", data["signals"]["points_or_airdrop"])
        self.assertEqual(data["official_links"]["docs"][0]["url"], "https://pearl.example/docs")
        self.assertEqual(data["official_links"]["github"][0]["url"], "https://github.com/pearl-labs/app")

    def test_web_search_can_be_skipped_for_smoke_runs(self) -> None:
        from crypto_research_agents.connectors.web_search import web_search

        with patch.dict("os.environ", {"JIMMORIA_SKIP_EXTERNAL_SEARCH": "1"}, clear=False):
            result = web_search("3Jane Protocol", limit=3)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["results"], [])
        self.assertIn("skipped", result["message"])

    def test_public_web_research_connectors_validate_required_inputs(self) -> None:
        from crypto_research_agents.connectors.defillama_connector import defillama_protocol_search, defillama_tvl_snapshot
        from crypto_research_agents.connectors.github_connector import github_get_repo_activity
        from crypto_research_agents.connectors.market_connectors import get_dex_pair, get_token_metadata
        from crypto_research_agents.connectors.rss_connector import rss_monitor_feed
        from crypto_research_agents.connectors.snapshot_connector import snapshot_get_proposals

        self.assertEqual(rss_monitor_feed()["status"], "missing_input")
        self.assertEqual(defillama_protocol_search()["status"], "missing_input")
        self.assertEqual(defillama_tvl_snapshot()["status"], "missing_input")
        self.assertEqual(snapshot_get_proposals()["status"], "missing_input")
        self.assertEqual(github_get_repo_activity()["status"], "missing_input")
        self.assertEqual(get_dex_pair()["status"], "missing_input")
        self.assertEqual(get_token_metadata()["status"], "missing_input")

    def test_operator_bridge_connectors_are_registered_and_guarded(self) -> None:
        policy = PolicyEngine()
        agent_id = "tester"
        for tool in [
            "skill_view",
            "search_files",
            "execute_code",
            "write_file",
            "terminal",
            "browser_vision",
            "send_message",
            "multi_tool_use.parallel",
        ]:
            policy.allow(agent_id, tool)
        gateway = ToolGateway(policy)
        register_default_connectors(gateway)

        self.assertIn("skill_view", gateway.registered_tools)
        self.assertIn("browser_console", gateway.registered_tools)
        self.assertIn("multi_tool_use.parallel", gateway.registered_tools)

        playbook = gateway.call(agent_id, "skill_view", skill_id="early-token-discovery")
        self.assertEqual(playbook["status"], "success")
        self.assertIn("Representative Web3 Project Diligence", playbook["data"]["content"])
        registry_skill = gateway.call(agent_id, "skill_view", skill_id="market_signal_intake_skill")
        self.assertEqual(registry_skill["status"], "success")
        self.assertEqual(registry_skill["data"]["owner"], "social_kol_agent")
        agent_skill = gateway.call(agent_id, "skill_view", skill_id="identity-gate")
        self.assertEqual(agent_skill["status"], "success")
        self.assertIn(".agents", agent_skill["data"]["path"])
        self.assertIn("Identity Gate Skill", agent_skill["data"]["content"])

        search = gateway.call(
            agent_id,
            "search_files",
            query="Hermes Operator Bridge",
            root="research_playbooks",
        )
        self.assertEqual(search["status"], "success")
        self.assertTrue(search["data"]["results"])

        score = gateway.call(
            agent_id,
            "execute_code",
            operation="score_sum",
            payload={"components": {"identity": 20, "social": 15, "note": "ignore"}},
        )
        self.assertEqual(score["data"]["score"], 35)

        parallel = gateway.call(agent_id, "multi_tool_use.parallel", tasks=[{"tool": "web_search"}])
        self.assertEqual(parallel["status"], "success")
        self.assertIn("parallel tool intent", parallel["message"])

        blocked_terminal = gateway.call(agent_id, "terminal", command="echo hi")
        self.assertEqual(blocked_terminal["status"], "failed")
        self.assertIn("disabled", blocked_terminal["message"])

        blocked_message = gateway.call(agent_id, "send_message", channel="telegram", message="hi")
        self.assertEqual(blocked_message["status"], "failed")
        self.assertIn("disabled", blocked_message["message"])

        vision = gateway.call(agent_id, "browser_vision", url="https://example.com")
        self.assertEqual(vision["status"], "failed")
        self.assertEqual(vision["data"]["status"], "external_connector_required")

    def test_airdrop_checker_filters_generic_non_project_results(self) -> None:
        from crypto_research_agents.connectors.opportunity_connector import check_airdrop_points

        generic_results = {
            "status": "success",
            "data": {
                "results": [
                    {
                        "title": "Crypto Airdrops List June 2026",
                        "url": "https://airdrops.io/",
                        "snippet": "Free token opportunities, testnets, rewards, and points campaigns.",
                    },
                    {
                        "title": "3Jane official rewards update",
                        "url": "https://docs.3jane.xyz/jane/liquidity-mining",
                        "snippet": "3Jane documentation mentions liquidity mining and rewards.",
                    },
                ]
            },
        }
        with patch("crypto_research_agents.connectors.opportunity_connector.web_search", return_value=generic_results):
            result = check_airdrop_points("3Jane Protocol")

        hints = result["data"]["hints"]
        self.assertEqual(result["data"]["classification"], "hint_found")
        self.assertEqual(len(hints), 1)
        self.assertIn("3Jane", hints[0]["title"])

    def test_tool_gateway_redacts_sensitive_audit_inputs(self) -> None:
        policy = PolicyEngine()
        policy.allow("agent", "echo")
        events: list[dict[str, object]] = []
        gateway = ToolGateway(
            policy,
            event_callback=lambda event_type, **payload: events.append({"type": event_type, **payload}),
        )
        gateway.register(
            "echo",
            lambda **kwargs: {
                "status": "success",
                "tool": "echo",
                "message": "ok",
                "data": {
                    "token": kwargs["api_key"],
                    "token_supply": "123",
                    "required_secret": "ETHERSCAN_API_KEY",
                    "normal": kwargs["normal"],
                    "content": "x" * 2500,
                },
            },
        )

        result = gateway.call(
            "agent",
            "echo",
            room_id="room_test",
            api_key="secret-value",
            normal="visible",
            nested={"bearer_token": "nested-secret"},
            content="y" * 2500,
        )

        self.assertEqual(result["data"]["token"], "secret-value")
        audit = gateway.audit_log[-1]
        self.assertEqual(audit["input"]["api_key"], "<redacted>")
        self.assertEqual(audit["input"]["nested"]["bearer_token"], "<redacted>")
        self.assertIn("<truncated", audit["input"]["content"])
        self.assertEqual(audit["result"]["data"]["token"], "<redacted>")
        self.assertEqual(audit["result"]["data"]["token_supply"], "123")
        self.assertEqual(audit["result"]["data"]["required_secret"], "ETHERSCAN_API_KEY")
        self.assertIn("<truncated", audit["result"]["data"]["content"])
        self.assertFalse(any("secret-value" in str(event) for event in events))

    def test_live_discovery_resolves_pearl_project_candidate(self) -> None:
        topic = "pearl 크립토 pow 프로젝트에 대해서 리서칭을 진행해봐"
        query = extract_project_query(topic)
        live_data = {
            "web_results": [
                {
                    "title": "Pearl Whitepaper",
                    "url": "https://pearlresearch.ai/",
                    "snippet": "Pearl Research Labs Proof-of-Useful-Work L1 protocol using matrix multiplication.",
                    "host": "pearlresearch.ai",
                },
                {
                    "title": "GitHub - pearl-research-labs/pearl",
                    "url": "https://github.com/pearl-research-labs/pearl",
                    "snippet": "Monorepo for the Pearl network.",
                    "host": "github.com",
                },
            ],
            "github_repos": [
                {
                    "full_name": "pearl-research-labs/pearl",
                    "html_url": "https://github.com/pearl-research-labs/pearl",
                    "description": "Monorepo for the Pearl network",
                }
            ],
            "coingecko_coins": [],
            "dex_pairs": [],
        }

        candidates = build_live_candidates(["Unclassified Early Crypto"], ["src_test"], topic, query, live_data)

        self.assertTrue(should_live_discover(topic, query))
        self.assertEqual(query, "pearl")
        self.assertEqual(candidates[0].name, "Pearl Network")
        self.assertEqual(candidates[0].website, "https://pearlresearch.ai/")
        self.assertEqual(candidates[0].chain, "Pearl L1")
        self.assertIn("Proof-of-Useful-Work", candidates[0].narratives)
        self.assertGreater(candidates[0].score, 60)
        self.assertEqual(candidates[0].metadata["candidate_origin"], "live_source_backed")
        self.assertEqual(candidates[0].metadata["source_backing"], "web_github_market_search")

    def test_live_discovery_resolves_3jane_report_candidate(self) -> None:
        topic = "3jane 관련 투자 보고서 만들어봐"
        query = extract_project_query(topic)
        live_data = {
            "web_results": project_identity_hints(query),
            "github_repos": [],
            "coingecko_coins": [],
            "dex_pairs": [],
        }

        candidates = build_live_candidates(["Unclassified Early Crypto"], ["src_test"], topic, query, live_data)
        quality = assess_report_quality(candidates)

        self.assertEqual(query, "3jane")
        self.assertEqual(extract_project_query("3jane report create"), "3jane")
        self.assertTrue(should_live_discover(topic, query))
        self.assertEqual(candidates[0].name, "3Jane Protocol")
        self.assertEqual(candidates[0].website, "https://www.3jane.xyz/")
        self.assertEqual(candidates[0].chain, "Ethereum")
        self.assertIn("Crypto Credit", candidates[0].narratives)
        self.assertEqual(candidates[0].metadata["candidate_origin"], "live_source_backed")
        self.assertGreaterEqual(len(candidates[0].metadata["evidence_urls"]), 3)
        self.assertEqual(quality.status, "research_complete")

    def test_project_profile_supplies_3jane_seed_evidence(self) -> None:
        profile = find_project_profile("3jane")

        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.display_name, "3Jane Protocol")
        self.assertEqual(profile.website, "https://www.3jane.xyz/")
        self.assertEqual(profile.chain, "Ethereum")
        self.assertTrue(any("credit based money market" in query for query in profile.search_queries))
        self.assertIn("USD3", profile.address_registry["contracts"])
        self.assertEqual(profile.funding["amount"], "$5.2M")

    def test_claim_ledger_and_score_breakdown_are_separate_from_url_count(self) -> None:
        project = ProjectCandidate(
            name="3Jane Protocol",
            website="https://www.3jane.xyz/",
            chain="Ethereum",
            token_status="usd3_yieldcoin_or_credit_asset_reported",
            narratives=["Crypto Credit", "Undercollateralized Lending"],
            score=80,
            reason_found="Resolved from public profile evidence.",
            sources=["src_test"],
            metadata={
                "candidate_origin": "live_source_backed",
                "evidence_urls": [
                    "https://www.3jane.xyz/",
                    "https://docs.3jane.xyz/introduction",
                    "https://x.com/3janexyz",
                ],
            },
        )
        quality = assess_report_quality([project])
        source_log = [
            {"source_id": "src_site", "label": "3Jane site", "url": "https://www.3jane.xyz/"},
            {"source_id": "src_docs", "label": "docs intro", "url": "https://docs.3jane.xyz/introduction"},
            {"source_id": "src_x", "label": "official X", "url": "https://x.com/3janexyz"},
        ]

        ledger = build_claim_evidence_ledger(project, [], source_log)
        score = diligence_score(project, [], quality, source_log)

        self.assertEqual(
            {item["category"] for item in ledger},
            {
                "identity",
                "product",
                "social_kol",
                "funding_team",
                "token_onchain",
                "github_activity",
                "live_metrics",
            },
        )
        self.assertTrue(any(item["verification_status"] == "unverified" for item in ledger))
        identity = next(item for item in ledger if item["category"] == "identity")
        self.assertIn("src_site", identity["source_ids"])
        self.assertTrue(any(ref["source_id"] == "src_docs" for ref in identity["source_refs"]))
        self.assertTrue(any(url.startswith("https://") for url in identity["source_urls"]))
        self.assertIn("evidence_confidence", score["breakdown"])
        self.assertIn("social_momentum", score["breakdown"])

    def test_social_kol_queries_prioritize_x_kol_and_articles(self) -> None:
        queries = build_public_social_queries("3jane", "3jane report create")

        self.assertEqual(queries[0], 'site:x.com "3jane" crypto')
        self.assertIn('site:x.com "3jane" official', queries)
        self.assertTrue(any("KOL" in query or "thesis" in query for query in queries))
        self.assertTrue(any("article" in query for query in queries))

    def test_social_kol_normalizes_who_said_what_rows(self) -> None:
        rows = build_who_said_what(
            project_name="3jane",
            official_social_sources=[
                {
                    "title": "3Jane official X profile",
                    "url": "https://x.com/3janexyz",
                    "source": "identity_hint",
                }
            ],
            x_posts=[
                {
                    "author_username": "3janexyz",
                    "text": "3Jane is building a credit market protocol.",
                    "url": "https://x.com/3janexyz/status/1",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ],
            timeline_results=[
                {
                    "handle": "3janexyz",
                    "status": "missing_secret",
                    "message": "Set X_BEARER_TOKEN to read X timelines.",
                    "posts": [],
                    "url": "https://x.com/3janexyz",
                }
            ],
            public_x_results=[],
            kol_profiles=[],
            kol_opinion_results=[
                {
                    "title": "3Jane analysis thread",
                    "url": "https://x.com/example/status/2",
                    "snippet": "A public thread discusses 3Jane credit mechanics.",
                }
            ],
        )

        self.assertGreaterEqual(len(rows), 3)
        self.assertIn("@3janexyz", {row["speaker"] for row in rows})
        self.assertTrue(any(row["source_type"] == "kol_article_or_thread" for row in rows))
        self.assertEqual(extract_handles_from_social_results([{"url": "https://x.com/3janexyz/status/1"}])[0], "3janexyz")

    def test_live_discovery_uses_social_seed_who_said_what_as_evidence(self) -> None:
        topic = "3jane report create"
        query = extract_project_query(topic)
        live_data = {
            "web_results": project_identity_hints(query),
            "github_repos": [],
            "coingecko_coins": [],
            "dex_pairs": [],
            "social_seed": {
                "project_query": query,
                "official_social_sources": [
                    {
                        "title": "3Jane official X profile",
                        "url": "https://x.com/3janexyz",
                        "host": "x.com",
                        "source": "identity_hint",
                    }
                ],
                "who_said_what": [
                    {
                        "source_type": "official_social_source",
                        "speaker": "@3janexyz",
                        "claim": "Official/candidate X source identified for 3jane.",
                        "url": "https://x.com/3janexyz",
                        "confidence": "medium",
                    }
                ],
            },
        }

        candidates = build_live_candidates(["Unclassified Early Crypto"], ["src_test"], topic, query, live_data)

        self.assertEqual(candidates[0].metadata["source_backing"], "social_first_web_github_market_search")
        self.assertIn("https://x.com/3janexyz", candidates[0].metadata["evidence_urls"])
        self.assertIn("who_said_what", candidates[0].metadata["social_seed"])

    def test_runtime_3jane_korean_report_request_uses_source_backed_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict("os.environ", {**_offline_no_secret_env(), "JIMMORIA_SKIP_EXTERNAL_SEARCH": "1"}, clear=False):
                runtime = ResearchRuntime()
                result = runtime.run_article_research(
                    title="3jane 관련 투자 보고서 만들어봐",
                    content="3jane 관련 투자 보고서 만들어봐",
                    vault_dir=root / "vault",
                    reports_dir=root / "reports",
                    memory_path=root / "memory.json",
                )
                report = Path(result.room.output_paths["report"]).read_text(encoding="utf-8")
                evidence_packet_path = Path(result.room.output_paths["evidence_packet"])
                evidence_packet_exists = evidence_packet_path.exists()
                evidence_packet = evidence_packet_path.read_text(encoding="utf-8")

        candidates = [candidate.to_dict() for candidate in result.memory.projects.values()]
        quality = result.room.project_card["research_quality"]

        self.assertEqual(result.room.status, "completed")
        self.assertEqual(quality["status"], "research_complete")
        self.assertGreaterEqual(quality["evidence_url_count"], 7)
        self.assertEqual(candidates[0]["name"], "3Jane Protocol")
        self.assertEqual(candidates[0]["metadata"]["candidate_origin"], "live_source_backed")
        self.assertIn("https://www.3jane.xyz/pdf/whitepaper.pdf", candidates[0]["metadata"]["evidence_urls"])
        self.assertTrue(evidence_packet_exists)
        self.assertIn("# 3Jane Protocol 리서치 보고서", report)
        self.assertIn("## 1. 대표님용 투자 메모", report)
        self.assertIn("## 2. 프로젝트 개요", report)
        self.assertIn("## 3. 시장/내러티브와 왜 지금인가", report)
        self.assertIn("## 4. 제품/프로토콜 구조", report)
        self.assertIn("## 5. 토큰/체인/가치 포착", report)
        self.assertIn("## 6. 팀/펀딩/KOL", report)
        self.assertIn("## 7. 리스크와 반론", report)
        self.assertIn("## 8. 다음 실사 질문", report)
        self.assertIn("## 9. 확인된 내용 요약", report)
        self.assertGreater(len(report), 10000)
        self.assertIn("`WATCH`", report)
        self.assertIn("# Evidence Packet: 3Jane Protocol", evidence_packet)
        self.assertIn("## Founder Dossier", evidence_packet)
        self.assertIn("## AntSeed Peer Review", evidence_packet)
        self.assertIn("## Stance", evidence_packet)
        self.assertIn("Evidence Packet", evidence_packet)
        self.assertIn("TOP", evidence_packet)
        self.assertNotIn("GPU Mining", report)
        self.assertNotIn("native_coin_reported", report)
        self.assertNotIn("Crypto Airdrops List", report)
        self.assertNotIn("Connector status: {", report)
        self.assertNotIn("Representative Diligence Brief", report)
        self.assertNotIn("Specialist Coverage", report)
        self.assertNotIn("Research Quality Metadata", report)
        self.assertNotIn("AntSeed Peer Review", report)
        self.assertNotIn("Set X_BEARER_TOKEN", report)
        self.assertNotIn("X_BEARER_TOKEN", report)
        self.assertNotIn("Official/candidate X source identified", report)
        self.assertNotIn("provider:", report)
        self.assertIn("usd3_yieldcoin_or_credit_asset_reported", report)
        self.assertIn("[3Jane address registry](https://docs.3jane.xyz/developers/addresses)", report)
        self.assertIn("$5.2M", report)
        self.assertIn("Paradigm", report)
        self.assertIn("The Block", report)
        self.assertIn("Delphi", report)
        self.assertIn("Wintermute", report)
        self.assertIn("투자 가설", report)
        self.assertIn("한 줄 결론", report)
        self.assertIn("무엇이 성립해야 하는가", report)
        self.assertIn("credit pool 사용량", report)
        self.assertIn("대표님이 읽을 때", report)
        self.assertIn("KOL/리서치 해석", report)
        self.assertIn("소셜 신호의 약점", report)
        self.assertIn("protocol operator 관점", report)
        self.assertIn("경제적 질문", report)
        self.assertIn("가치 포착의 강한 조건", report)
        self.assertIn("운영 리스크", report)
        self.assertIn("반론", report)
        self.assertIn("공식 사이트/화이트페이퍼", report)
        self.assertIn("Docs introduction", report)
        self.assertIn("Supplier docs", report)
        self.assertIn("Risk docs", report)
        self.assertIn("USD3: `0x056B269Eb1f75477a8666ae8C7fE01b64dD55eCc`", report)
        self.assertIn("JANE: `0x333333330522f64ee8d0b3039c460b41670e3404`", report)
        self.assertIn("credit-based money market", report)
        self.assertIn("undercollateralized", report)
        self.assertNotIn("docs.github.com", report)
        self.assertNotIn("github.com/features", report)
        self.assertNotIn("discord.com/invite", report)
        self.assertNotIn("CoinMarketCap", report)
        self.assertNotIn("YouTube", report)
        self.assertNotIn("facebook.com", report)
        self.assertNotIn("Scribd", report)
        self.assertNotIn("Project-specific incentive hints", report)
        self.assertNotIn("????", report)
        self.assertNotIn("?묒", report)
        self.assertNotIn("李", report)
        self.assertNotIn("Supervisor Final Review", report)
        self.assertNotIn("Agent Research Notes", report)
        self.assertNotIn("Agent Research Notes", report)
        self.assertNotIn("LLM synthesis", report)
        self.assertNotIn("LLM 종합", report)
        self.assertNotIn('"title":', report)
        self.assertNotIn("obsidian_note", report)

    def test_runtime_pearl_report_reads_like_investment_research(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict("os.environ", {**_offline_no_secret_env(), "JIMMORIA_SKIP_EXTERNAL_SEARCH": "1"}, clear=False):
                runtime = ResearchRuntime()
                result = runtime.run_article_research(
                    title="pearl 리서치 보고서 만들어봐",
                    content="pearl 리서치 보고서 만들어봐",
                    vault_dir=root / "vault",
                    reports_dir=root / "reports",
                    memory_path=root / "memory.json",
                )
                report = Path(result.room.output_paths["report"]).read_text(encoding="utf-8")

        candidates = [candidate.to_dict() for candidate in result.memory.projects.values()]
        quality = result.room.project_card["research_quality"]

        self.assertEqual(result.room.status, "completed")
        self.assertEqual(quality["status"], "research_complete")
        self.assertEqual(candidates[0]["name"], "Pearl Network")
        self.assertIn("# Pearl Network 리서치 보고서", report)
        self.assertIn("## 1. 대표님용 투자 메모", report)
        self.assertIn("## 2. 프로젝트 개요", report)
        self.assertIn("## 3. 시장/내러티브와 왜 지금인가", report)
        self.assertIn("## 4. 제품/프로토콜 구조", report)
        self.assertIn("## 5. 토큰/체인/가치 포착", report)
        self.assertIn("## 6. 팀/펀딩/KOL", report)
        self.assertIn("## 7. 리스크와 반론", report)
        self.assertIn("## 8. 다음 실사 질문", report)
        self.assertIn("## 9. 확인된 내용 요약", report)
        self.assertGreater(len(report), 10000)
        self.assertIn("Proof-of-Useful-Work", report)
        self.assertIn("matrix multiplication", report)
        self.assertIn("AI compute", report)
        self.assertIn("채굴 경제", report)
        self.assertIn("유용 compute", report)
        self.assertIn("compute buyer", report)
        self.assertIn("Together AI", report)
        self.assertIn("GitHub repo", report)
        self.assertIn("Explorer / Blockbook", report)
        self.assertIn("Mining pool", report)
        self.assertIn("native_coin_reported", report)
        self.assertIn("value-capture", report)
        self.assertIn("반론", report)
        self.assertIn("Pearl을 WATCH에서 TOP으로 올리려면", report)
        self.assertNotIn("Agent Research Notes", report)
        self.assertNotIn("LLM synthesis", report)
        self.assertNotIn('"title":', report)
        self.assertNotIn("????", report)
        self.assertNotIn("?묒", report)
        self.assertNotIn("李", report)

    def test_message_summary_uses_response_result_fallback(self) -> None:
        message = {
            "type": "RESPONSE",
            "task": {},
            "result": {"status": "complete", "summary": "candidate check complete"},
            "status": "created",
        }

        self.assertEqual(message_summary(message), "candidate check complete")

    def test_source_record_dedupes_by_canonical_url(self) -> None:
        memory = SharedMemory()

        first = memory.add_source(SourceRecord(title="One", content="same content", url="https://Example.com/a#frag"))
        second = memory.add_source(SourceRecord(title="Two", content="other content", url="https://example.com/a"))

        self.assertEqual(first.source_id, second.source_id)
        self.assertEqual(len(memory.sources), 1)
        self.assertEqual(first.canonical_url, "https://example.com/a")
        self.assertTrue(first.content_hash)

    def test_agent_specs_load(self) -> None:
        registry = AgentSpecRegistry.load_dir("config/agents")

        social = registry.get("social_kol_agent")
        self.assertIsNotNone(social)
        assert social is not None
        self.assertIn("x_search_posts", social.tools.allow)
        self.assertIn("x_get_user_timeline", social.tools.allow)
        self.assertIn("rss_monitor_feed", social.tools.allow)
        self.assertNotIn("telegram_read_channel", social.tools.allow)
        self.assertNotIn("discord_read_channel", social.tools.allow)
        self.assertIn("oauth_tokens", social.memory_scope.no_access)
        self.assertEqual(social.persona_name, "The Signal Listener")
        self.assertIn("public web and X", social.mission.primary_goal)
        self.assertIn("browser_snapshot", social.tools.allow)
        self.assertIn("social_signal_intake", social.skills)
        self.assertIn("market_signal_intake_skill", social.skills)
        self.assertIn("telegram_channel_reader_skill", social.skills.disabled)
        self.assertIn("prepare_who_said_what_schema", social.hooks["before_run"])
        self.assertIn("no_hype_as_fact", social.hooks["quality_gate"])

        discovery = registry.get("discovery_agent")
        self.assertIsNotNone(discovery)
        assert discovery is not None
        self.assertIn("early_token_discovery", discovery.skills)
        self.assertIn("identity_gate", discovery.skills)
        self.assertIn("candidate_origin_required", discovery.hooks["quality_gate"])

        supervisor = registry.get("supervisor_agent")
        self.assertIsNotNone(supervisor)
        assert supervisor is not None
        self.assertEqual(supervisor.persona_name, "The Company President")
        self.assertIn("company_settings", supervisor.memory_scope.write)
        self.assertIn("supervisor_memory", supervisor.memory_scope.read)
        self.assertIn("supervisor_session", supervisor.memory_scope.write)
        self.assertIn("skill_view", supervisor.tools.allow)
        self.assertIn("multi_tool_use.parallel", supervisor.tools.allow)
        self.assertIn("supervisor_orchestration", supervisor.skills)
        self.assertIn("intake_classification_skill", supervisor.skills.secondary)
        self.assertIn("classify_client_intent", supervisor.hooks["before_run"])
        self.assertIn("verify_specialist_assignment", supervisor.hooks["quality_gate"])
        supervisor_prompt = supervisor.system_prompt()
        self.assertIn("Skills/playbooks: supervisor_orchestration", supervisor_prompt)
        self.assertIn("Runtime hooks:", supervisor_prompt)
        self.assertTrue(any("closed-fleet job contract" in item for item in supervisor.must_follow))
        self.assertTrue(any("fresh context" in item for item in supervisor.must_follow))
        self.assertTrue(any("single-agent loop" in item for item in supervisor.professional_output_contract.get("quality_rules", [])))
        self.assertTrue(any("lightest-footprint" in item for item in supervisor.professional_output_contract.get("quality_rules", [])))

        product = registry.get("product_tech_agent")
        self.assertIsNotNone(product)
        assert product is not None
        self.assertIn("github_search_repos", product.tools.allow)
        self.assertIn("search_files", product.tools.allow)
        self.assertIn("browser_console", product.tools.allow)
        self.assertIn("product_tech_diligence", product.skills)
        self.assertIn("product_claim_requires_source", product.hooks["quality_gate"])

        report = registry.get("report_agent")
        self.assertIsNotNone(report)
        assert report is not None
        self.assertEqual(report.output_schema.type, "project_intelligence_report")
        self.assertIn("investment_report_synthesis", report.skills)
        self.assertIn("project_dossier_render_skill", report.skills.secondary)
        self.assertIn("claim_evidence_check", report.hooks["quality_gate"])
        self.assertIn("no_agent_log_in_final", report.hooks["quality_gate"])
        self.assertIn("risk_to_unclear_points_transform", report.hooks["before_report"])
        self.assertIn("Korean-first investment-style project report", report.mission.primary_goal)
        self.assertTrue(any("client comprehension" in item for item in report.must_follow))
        self.assertTrue(any("project intelligence report" in item for item in report.must_not))
        self.assertTrue(any("raw LLM JSON" in item for item in report.must_not))
        self.assertIn("evidence_packet", report.output_schema.required)
        self.assertIn("write_file", report.tools.allow)
        self.assertIn("execute_code", report.tools.allow)

        funding = registry.get("funding_token_agent")
        self.assertIsNotNone(funding)
        assert funding is not None
        self.assertIn("rootdata_get_project", funding.tools.allow)
        self.assertIn("browser_snapshot", funding.tools.allow)
        self.assertIn("funding_token_diligence", funding.skills)
        self.assertIn("no_airdrop_promise", funding.hooks["quality_gate"])

        triage = registry.get("signal_triage_agent")
        self.assertIsNotNone(triage)
        assert triage is not None
        self.assertIn("signal_triage", triage.skills)
        self.assertIn("signal_dedup_skill", triage.skills.secondary)
        self.assertIn("route_to_archive_watchlist_or_supervisor", triage.hooks["after_run"])

    def test_llm_analysis_normalizes_risks_to_unclear_points(self) -> None:
        normalized = normalize_llm_analysis(
            {
                "summary": "ok",
                "confidence": 0.7,
                "evidence_gaps": ["missing founder source"],
                "risks": ["token value-capture unclear"],
                "next_actions": ["check docs"],
            },
            fallback_summary="fallback",
        )

        self.assertEqual(normalized["summary"], "ok")
        self.assertEqual(normalized["unclear_points"], ["token value-capture unclear"])
        self.assertEqual(normalized["risks"], ["token value-capture unclear"])

    def test_process_specs_load_research_and_ingestion_rooms(self) -> None:
        registry = ProcessSpecRegistry.load_dir("config/processes")
        research = registry.get("project_research_room")
        source_only = registry.get("source_ingestion_room")
        loaded_research = load_process_spec("project_research_room")

        self.assertIsNotNone(research)
        self.assertIsNotNone(source_only)
        assert research is not None
        assert source_only is not None
        self.assertEqual(loaded_research.process_id, research.process_id)
        self.assertEqual(research.process_type, "controlled_p2p_full_parallel_research_swarm")
        self.assertEqual(research.execution_strategy["current_phase"], 3)
        self.assertEqual(research.execution_strategy["current_mode"], "full_parallel_agent_swarm")
        self.assertIn("phase_3", research.execution_strategy)
        self.assertEqual(research.agent_ids[0], "supervisor_agent")
        self.assertEqual(research.agent_ids[-1], "obsidian_curator_agent")
        self.assertEqual(len(research.tasks), 13)
        self.assertEqual(len(research.agent_ids), 10)
        self.assertEqual(research.agent_ids[2], "social_kol_agent")
        self.assertIn("research_swarm", {task.phase for task in research.tasks})
        self.assertIn("deliberation", {task.phase for task in research.tasks})
        self.assertIn("final_review", {task.phase for task in research.tasks})
        self.assertIn("representative_web3_project_diligence", research.playbooks)
        self.assertIn("dossier", research.task_for_agent("report_agent").expected_output.lower())
        self.assertIn("evidence packet", research.task_for_agent("report_agent").expected_output.lower())
        self.assertEqual(research.artifact_contracts["evidence_packet"], "data/evidence_packets/*.md")
        self.assertEqual(source_only.agent_ids, ["supervisor_agent", "ingestion_agent", "obsidian_curator_agent"])
        self.assertIn("prevent unnecessary research", source_only.tasks[0].description)

    def test_concurrency_policy_loads_phase_roadmap(self) -> None:
        policy = load_concurrency_policy()

        self.assertEqual(policy.active.phase, 3)
        self.assertEqual(policy.active.mode, "full_parallel_agent_swarm")
        self.assertEqual(policy.active.max_parallel, 7)
        self.assertEqual(len(policy.phases), 2)
        phase_three = [phase for phase in policy.phases if phase.phase == 3][0]
        self.assertEqual(phase_three.status, "active")
        self.assertEqual(phase_three.mode, "full_parallel_agent_swarm")
        self.assertEqual(phase_three.max_parallel, 7)
        self.assertEqual(phase_three.parallel_groups[0].group_id, "research_swarm")
        self.assertIn("ingestion_agent", phase_three.parallel_groups[0].agents)
        self.assertIn("funding_token_agent", phase_three.parallel_groups[0].agents)

    def test_runtime_room_created_event_includes_concurrency_policy(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = ResearchRuntime()
            result = runtime.run_source_ingestion(
                title="Concurrency Event",
                content="Store this source and expose active concurrency phase.",
                vault_dir=root / "vault",
                memory_path=root / "memory.json",
            )

            room_created = [event for event in runtime.event_log if event["type"] == "room_created"][0]

        self.assertEqual(result.room.status, "completed")
        self.assertEqual(room_created["concurrency"]["active_phase"]["phase"], 3)
        self.assertEqual(room_created["concurrency"]["active_phase"]["mode"], "full_parallel_agent_swarm")
        self.assertEqual(room_created["job_contract"]["loop_mode"], "closed_fleet")
        self.assertEqual(room_created["job_contract"]["output_mode"], "source_note")
        self.assertEqual(result.room.project_card["job_contract"]["process_id"], "source_ingestion_room")

    def test_runtime_runs_research_agents_as_parallel_swarm(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict("os.environ", {**_offline_no_secret_env(), "JIMMORIA_SKIP_EXTERNAL_SEARCH": "1"}, clear=False):
                runtime = ResearchRuntime()
                result = runtime.run_article_research(
                    title="Parallel Research Swarm",
                    content="3Jane crypto credit project report",
                    vault_dir=root / "vault",
                    reports_dir=root / "reports",
                    memory_path=root / "memory.json",
                )

            event_types = [event["type"] for event in runtime.event_log]
            group_start = [event for event in runtime.event_log if event["type"] == "parallel_group_start"][0]
            group_done = [event for event in runtime.event_log if event["type"] == "parallel_group_done"][0]
            first_swarm_done_seq = min(
                event["seq"]
                for event in runtime.event_log
                if event["type"] == "agent_done" and event.get("agent_id") in group_start["agents"]
            )
            swarm_starts_before_done = {
                event.get("agent_id")
                for event in runtime.event_log
                if event["type"] == "agent_start"
                and event.get("agent_id") in group_start["agents"]
                and event["seq"] < first_swarm_done_seq
            }

        self.assertEqual(result.room.status, "completed")
        self.assertIn("parallel_group_start", event_types)
        self.assertIn("parallel_group_done", event_types)
        self.assertEqual(group_start["group_id"], "research_swarm")
        self.assertEqual(group_start["max_parallel"], 7)
        self.assertEqual(set(group_start["agents"]), {
            "ingestion_agent",
            "social_kol_agent",
            "narrative_agent",
            "discovery_agent",
            "contract_onchain_agent",
            "product_tech_agent",
            "funding_token_agent",
        })
        self.assertEqual(swarm_starts_before_done, set(group_start["agents"]))
        self.assertEqual(group_done["group_id"], "research_swarm")

    def test_runtime_retries_only_failed_swarm_agent_once(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = {"product_tech_agent": 0}

            def flaky_product_agent(room, memory, bus, **kwargs):
                del room, memory, bus, kwargs
                calls["product_tech_agent"] += 1
                if calls["product_tech_agent"] == 1:
                    raise RuntimeError("temporary docs crawl failure")
                return AgentResult("product_tech_agent", "Product retry succeeded.", {}, confidence=0.5)

            with patch.dict("os.environ", {**_offline_no_secret_env(), "JIMMORIA_SKIP_EXTERNAL_SEARCH": "1"}, clear=False):
                runtime = ResearchRuntime()
                runtime.agents["product_tech_agent"].run = flaky_product_agent
                result = runtime.run_article_research(
                    title="Retry Swarm Agent",
                    content="3Jane crypto credit project report",
                    vault_dir=root / "vault",
                    reports_dir=root / "reports",
                    memory_path=root / "memory.json",
                )

        event_types = [event["type"] for event in runtime.event_log]
        self.assertEqual(result.room.status, "completed")
        self.assertEqual(calls["product_tech_agent"], 2)
        self.assertIn("agent_failed", event_types)
        self.assertIn("agent_retry_start", event_types)
        self.assertIn("agent_retry_done", event_types)
        self.assertIn("parallel_group_retry_done", event_types)
        self.assertNotIn("parallel_group_failed", event_types)
        retry_start = [event for event in runtime.event_log if event["type"] == "agent_retry_start"][0]
        self.assertEqual(retry_start["max_attempts"], 2)
        self.assertEqual(result.room.project_card["job_contract"]["iteration_policy"]["max_agent_attempts"], 2)

    def test_process_specs_load_when_cli_runs_outside_project_root(self) -> None:
        original_cwd = Path.cwd()
        with TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                loaded_research = load_process_spec("project_research_room")
                registry = AgentSpecRegistry.load_dir("config/agents")
            finally:
                os.chdir(original_cwd)

        self.assertEqual(loaded_research.process_id, "project_research_room")
        self.assertIsNotNone(registry.get("supervisor_agent"))

    def test_workflow_yaml_loads(self) -> None:
        registry = WorkflowSpecRegistry.load_dir("config/workflows")
        early = registry.get("early_radar_v1")
        candidate = registry.get("candidate_diligence_v1")
        project = registry.get("project_diligence_v1")

        self.assertIsNotNone(early)
        self.assertIsNotNone(candidate)
        self.assertIsNotNone(project)
        assert early is not None
        self.assertEqual(early.workflow_id, "early_radar_v1")
        self.assertTrue(any(edge.dynamic.get("type") == "map" for edge in early.edges))

    def test_workflow_graph_validates_edges(self) -> None:
        workflow = load_workflow_spec("early_radar_v1")

        workflow.validate()
        node_ids = workflow.node_ids
        for edge in workflow.edges:
            self.assertIn(edge.from_node, node_ids)
            self.assertIn(edge.to_node, node_ids)

    def test_dynamic_candidate_map_dispatch(self) -> None:
        dispatcher = DynamicCandidateDispatcher(max_parallel=2)
        candidates = [{"project": "Example"}, {"project": "Second"}]

        results = dispatcher.dispatch(candidates, handler=lambda task: {"status": "completed", "name": task.display_name})

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].status, "completed")
        self.assertEqual(results[0].result["name"], "Example")

    def test_candidate_task_failure_becomes_risk_finding(self) -> None:
        dispatcher = DynamicCandidateDispatcher(max_parallel=2)

        def fail_candidate(_task: object) -> dict[str, object]:
            raise RuntimeError("source unavailable")

        results = dispatcher.dispatch([{"project": "Broken"}], handler=fail_candidate)

        self.assertEqual(results[0].status, "failed")
        self.assertIsNotNone(results[0].risk_finding)
        assert results[0].risk_finding is not None
        self.assertEqual(results[0].risk_finding["type"], "candidate_task_failure")

    def test_edge_condition_has_candidates(self) -> None:
        self.assertTrue(evaluate_edge_condition({"type": "has_candidates"}, {"candidates": [{"project": "A"}]}))
        self.assertFalse(evaluate_edge_condition({"type": "has_candidates"}, {"candidates": []}))
        self.assertTrue(evaluate_edge_condition({"type": "no_kill_switch"}, {"kill_switch": False}))
        self.assertFalse(evaluate_edge_condition({"type": "has_kill_switch"}, {"kill_switch": False}))

    def test_loop_counter_stops_after_max_iterations(self) -> None:
        counter = LoopCounter(counter_id="Citation QA Loop", max_iterations=2, reset_on_emit=True)

        self.assertTrue(counter.tick())
        self.assertTrue(counter.tick())
        self.assertFalse(counter.tick())
        self.assertFalse(counter.can_continue())
        counter.reset()
        self.assertTrue(counter.can_continue())

    def test_artifact_store_writes_workflow_trace(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = ResearchRuntime()
            result = runtime.run_source_ingestion(
                title="Workflow Source",
                content="Store this source for workflow artifact testing.",
                vault_dir=root / "vault",
                memory_path=root / "memory.json",
            )
            workflow = load_workflow_spec("project_diligence_v1")
            trace = WorkflowExecutor().execute(workflow, {"run_id": result.room.room_id, "sources": result.room.source_inputs}).trace
            artifact_dir = ArtifactStore(root / "runs").archive_workflow_run(
                result=result,
                workflow=workflow,
                workflow_trace=trace,
                event_log=runtime.event_log,
            )

            self.assertTrue((artifact_dir / "workflow.yaml").exists())
            self.assertTrue((artifact_dir / "workflow_trace.json").exists())
            self.assertTrue((artifact_dir / "events.jsonl").exists())
            self.assertTrue((artifact_dir / "sources.json").exists())

    def test_workflow_cli_list(self) -> None:
        output = StringIO()

        with redirect_stdout(output):
            cli_main(["workflow", "list"])

        self.assertIn("early_radar_v1", output.getvalue())
        self.assertIn("candidate_diligence_v1", output.getvalue())

    def test_workflow_cli_run_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = StringIO()

            with redirect_stdout(output):
                cli_main(
                    [
                        "workflow",
                        "run",
                        "project_diligence_v1",
                        "--text",
                        "Pearl crypto project diligence request. https://x.com/pearl",
                        "--memory",
                        str(root / "memory.json"),
                        "--vault",
                        str(root / "vault"),
                        "--reports",
                        str(root / "reports"),
                        "--json",
                    ]
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(payload["workflow_id"], "project_diligence_v1")
            self.assertTrue((Path(payload["artifact_dir"]) / "workflow_trace.json").exists())
            self.assertTrue((Path(payload["artifact_dir"]) / "report.json").exists())

    def test_quality_gate_rejects_missing_citation(self) -> None:
        result = review_report_quality("Project looks promising. Evidence URLs: 0")

        self.assertFalse(result.passed)
        self.assertEqual(result.next_action, "revise_report")
        self.assertEqual(result.issues[0].issue_type, "missing_citation")

    def test_quality_gate_blocks_investment_advice_language(self) -> None:
        result = review_report_quality("You should buy this token. https://example.com")

        self.assertFalse(result.passed)
        self.assertTrue(any(issue.issue_type == "investment_advice_language" for issue in result.issues))

    def test_runtime_room_created_event_includes_process_spec(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = ResearchRuntime()
            result = runtime.run_source_ingestion(
                title="Source Only",
                content="Store this source only.",
                vault_dir=root / "vault",
                memory_path=root / "memory.json",
            )

            first_event = runtime.event_log[0]
            self.assertEqual(first_event["type"], "room_created")
            self.assertEqual(first_event["process"]["process_id"], "source_ingestion_room")
            self.assertEqual(result.room.agents, ["supervisor_agent", "ingestion_agent", "obsidian_curator_agent"])

    def test_saved_report_request_finds_existing_report_without_new_room(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "reports" / "3jane-dossier.md"
            run_dir = root / "data" / "runs" / "room_3jane"
            report_path.parent.mkdir(parents=True)
            run_dir.mkdir(parents=True)
            report_path.write_text("# 3jane Report\n\nFull dossier body.", encoding="utf-8")
            (run_dir / "room.json").write_text(
                json.dumps(
                    {
                        "room_id": "room_3jane",
                        "topic": "3jane crypto project research",
                        "status": "completed",
                        "output_paths": {"report": str(report_path)},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            found = find_saved_report_for_request(
                "3jane 보고서 들고와봐",
                runs_dir=root / "data" / "runs",
                reports_dir=root / "reports",
            )

        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found[0], report_path)
        self.assertEqual(found[1], "room_3jane")

    def test_saved_report_request_finds_make_phrasing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "reports" / "3jane-dossier.md"
            run_dir = root / "data" / "runs" / "room_3jane"
            report_path.parent.mkdir(parents=True)
            run_dir.mkdir(parents=True)
            report_path.write_text("# 3jane Report\n\nFull dossier body.", encoding="utf-8")
            (run_dir / "room.json").write_text(
                json.dumps(
                    {
                        "room_id": "room_3jane",
                        "topic": "3jane crypto project research",
                        "status": "completed",
                        "output_paths": {"report": str(report_path)},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            found = find_saved_report_for_request(
                "3jane 보고서 만들어봐",
                runs_dir=root / "data" / "runs",
                reports_dir=root / "reports",
            )

        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found[0], report_path)
        self.assertEqual(found[1], "room_3jane")

    def test_saved_report_request_finds_have_bring_phrasing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "reports" / "3jane-dossier.md"
            run_dir = root / "data" / "runs" / "room_3jane"
            report_path.parent.mkdir(parents=True)
            run_dir.mkdir(parents=True)
            report_path.write_text("# 3jane Report\n\nFull dossier body.", encoding="utf-8")
            (run_dir / "room.json").write_text(
                json.dumps(
                    {
                        "room_id": "room_3jane",
                        "topic": "3jane crypto project research",
                        "status": "completed",
                        "output_paths": {"report": str(report_path)},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            found = find_saved_report_for_request(
                "3jane 보고서 가지고 와봐",
                runs_dir=root / "data" / "runs",
                reports_dir=root / "reports",
            )

        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found[0], report_path)
        self.assertEqual(found[1], "room_3jane")

    def test_cli_research_command(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = StringIO()
            with redirect_stdout(output):
                cli_main(
                    [
                        "research",
                        "--title",
                        "CLI Test",
                        "--text",
                        "AI wallet automation with docs and points. https://example.com/project",
                        "--vault",
                        str(root / "vault"),
                        "--reports",
                        str(root / "reports"),
                        "--memory",
                        str(root / "memory.json"),
                    ]
                )

            self.assertIn("status: completed", output.getvalue())
            self.assertTrue((root / "memory.json").exists())

    def test_cli_events_command(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = ResearchRuntime()
            result = runtime.run_article_research(
                title="Event Replay Check",
                content="AI wallet automation with docs and points.",
                vault_dir=root / "vault",
                reports_dir=root / "reports",
                memory_path=root / "memory.json",
            )

            output = StringIO()
            with redirect_stdout(output):
                cli_main(["events", result.room.room_id, "--runs-dir", str(root / "runs")])

            text = output.getvalue()
            self.assertIn("room_created", text)
            self.assertIn("agent_start", text)

    def test_cli_report_alias_prints_saved_report(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "reports" / "3jane-room_alias.md"
            run_dir = root / "runs" / "room_alias"
            report_path.parent.mkdir(parents=True)
            run_dir.mkdir(parents=True)
            report_path.write_text("# 3Jane Full Report\n\nFull saved body.", encoding="utf-8")
            (run_dir / "room.json").write_text(
                json.dumps(
                    {
                        "room_id": "room_alias",
                        "topic": "3jane report",
                        "status": "completed",
                        "output_paths": {"report": str(report_path)},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            output = StringIO()
            with redirect_stdout(output):
                cli_main(["report", "room_alias", "--runs-dir", str(root / "runs")])

            text = output.getvalue()
            self.assertIn("# 3Jane Full Report", text)
            self.assertIn("Full saved body.", text)

    def test_codex_sdk_provider_can_be_selected(self) -> None:
        with patch.dict(
            "os.environ",
            {"LLM_PROVIDER": "codex_sdk"},
            clear=True,
        ):
            provider = provider_from_env()

        self.assertEqual(provider.provider_name, "codex_sdk")

    def test_codex_cli_provider_can_be_selected(self) -> None:
        with patch.dict("os.environ", {"LLM_PROVIDER": "codex_cli"}, clear=True):
            provider = provider_from_env()

        self.assertEqual(provider.provider_name, "codex_cli")

    def test_codex_api_provider_can_be_selected_with_api_key(self) -> None:
        with patch.dict("os.environ", {"LLM_PROVIDER": "codex_api", "OPENAI_API_KEY": "openai-test"}, clear=True):
            provider = provider_from_env()

        self.assertEqual(provider.provider_name, "codex_api")
        self.assertTrue(getattr(provider, "is_configured", False))

    def test_codex_api_provider_calls_openai_responses_api(self) -> None:
        seen: dict[str, object] = {}

        class FakeHTTPResponse:
            def __enter__(self) -> "FakeHTTPResponse":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps({"output_text": "{\"summary\": \"ok\"}", "usage": {"input_tokens": 2}}).encode("utf-8")

        def fake_urlopen(request: object, timeout: float) -> FakeHTTPResponse:
            seen["timeout"] = timeout
            seen["url"] = getattr(request, "full_url")
            seen["authorization"] = request.get_header("Authorization")  # type: ignore[attr-defined]
            seen["payload"] = json.loads(request.data.decode("utf-8"))  # type: ignore[attr-defined]
            return FakeHTTPResponse()

        request = LLMRequest(
            agent_id="report_agent",
            task_type="final_synthesis",
            model="gpt-5.5",
            system_prompt="Write JSON.",
            user_prompt="3jane report",
            max_tokens=500,
            temperature=0.2,
            response_format="json",
            reasoning_effort="pro",
        )

        with patch.dict("os.environ", {"OPENAI_API_KEY": "openai-test"}, clear=True):
            with patch("crypto_research_agents.core.llm_provider.urllib.request.urlopen", side_effect=fake_urlopen):
                response = CodexApiProvider().complete(request)

        payload = seen["payload"]
        assert isinstance(payload, dict)
        self.assertEqual(seen["url"], "https://api.openai.com/v1/responses")
        self.assertEqual(seen["authorization"], "Bearer openai-test")
        self.assertEqual(payload["model"], "gpt-5.5")
        self.assertEqual(payload["reasoning"], {"effort": "high"})
        self.assertEqual(payload["text"], {"format": {"type": "json_object"}})
        self.assertEqual(response.text, '{"summary": "ok"}')
        self.assertEqual(response.provider, "codex_api")

    def test_grok_provider_can_be_selected_with_bearer_token(self) -> None:
        with patch.dict("os.environ", {"LLM_PROVIDER": "grok", "XAI_API_KEY": "xai-test"}, clear=True):
            provider = provider_from_env()

        self.assertEqual(provider.provider_name, "grok")
        self.assertTrue(getattr(provider, "is_configured", False))

    def test_codex_grok_hybrid_provider_can_be_selected(self) -> None:
        with patch.dict("os.environ", {"LLM_PROVIDER": "codex_grok"}, clear=True):
            provider = provider_from_env()

        self.assertEqual(provider.provider_name, "codex_grok")

    def test_grok_oauth_provider_reuses_hermes_xai_auth_json(self) -> None:
        with TemporaryDirectory() as tmp:
            hermes_home = Path(tmp) / ".hermes"
            hermes_home.mkdir()
            (hermes_home / "auth.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "providers": {
                            "xai-oauth": {
                                "tokens": {
                                    "access_token": "oauth-access-token",
                                    "refresh_token": "oauth-refresh-token",
                                },
                                "base_url": "https://api.x.ai/v1",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch.dict(
                "os.environ",
                {
                    "LLM_PROVIDER": "xai_oauth",
                    "HERMES_HOME": str(hermes_home),
                    "XAI_API_KEY": "api-key-should-not-win",
                },
                clear=True,
            ):
                provider = provider_from_env()

        self.assertEqual(provider.provider_name, "grok")
        self.assertEqual(getattr(provider, "api_key", ""), "oauth-access-token")
        self.assertIn("Hermes", getattr(provider, "auth_source", ""))

    def test_grok_oauth_provider_does_not_fallback_to_api_key(self) -> None:
        with TemporaryDirectory() as tmp:
            with patch.dict(
                "os.environ",
                {
                    "LLM_PROVIDER": "xai_oauth",
                    "HERMES_HOME": tmp,
                    "XAI_API_KEY": "api-key-should-not-win",
                },
                clear=True,
            ):
                provider = provider_from_env()

        self.assertEqual(provider.provider_name, "grok")
        self.assertEqual(getattr(provider, "api_key", ""), "")
        self.assertEqual(getattr(provider, "auth_source", ""), "missing")
        self.assertFalse(getattr(provider, "is_configured", True))

    def test_grok_plain_provider_prefers_api_key_before_hermes_oauth(self) -> None:
        with TemporaryDirectory() as tmp:
            hermes_home = Path(tmp) / ".hermes"
            hermes_home.mkdir()
            (hermes_home / "auth.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "providers": {
                            "xai-oauth": {
                                "tokens": {
                                    "access_token": "oauth-access-token",
                                    "refresh_token": "oauth-refresh-token",
                                }
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch.dict(
                "os.environ",
                {
                    "LLM_PROVIDER": "grok",
                    "HERMES_HOME": str(hermes_home),
                    "XAI_API_KEY": "api-key-wins",
                },
                clear=True,
            ):
                provider = provider_from_env()

        self.assertEqual(getattr(provider, "api_key", ""), "api-key-wins")
        self.assertEqual(getattr(provider, "auth_source", ""), "XAI_API_KEY")

    def test_grok_provider_calls_xai_responses_api(self) -> None:
        seen: dict[str, object] = {}

        class FakeHTTPResponse:
            def __enter__(self) -> "FakeHTTPResponse":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(
                    {
                        "output_text": '{"summary": "ok"}',
                        "usage": {"input_tokens": 5, "output_tokens": 3},
                    }
                ).encode("utf-8")

        def fake_urlopen(request: object, timeout: float) -> FakeHTTPResponse:
            seen["timeout"] = timeout
            seen["url"] = getattr(request, "full_url")
            seen["authorization"] = request.get_header("Authorization")  # type: ignore[attr-defined]
            seen["payload"] = json.loads(request.data.decode("utf-8"))  # type: ignore[attr-defined]
            return FakeHTTPResponse()

        request = LLMRequest(
            agent_id="report_agent",
            task_type="final_synthesis",
            model="grok-4.3",
            system_prompt="Write JSON.",
            user_prompt="3jane report",
            max_tokens=500,
            temperature=0.2,
            response_format="json",
            reasoning_effort="pro",
        )

        with patch.dict("os.environ", {"LLM_PROVIDER": "grok", "XAI_API_KEY": "xai-test"}, clear=True):
            with patch("crypto_research_agents.core.llm_provider.urllib.request.urlopen", side_effect=fake_urlopen):
                response = provider_from_env().complete(request)

        payload = seen["payload"]
        assert isinstance(payload, dict)
        self.assertEqual(seen["url"], "https://api.x.ai/v1/responses")
        self.assertEqual(seen["authorization"], "Bearer xai-test")
        self.assertEqual(payload["model"], "grok-4.3")
        self.assertEqual(payload["reasoning"], {"effort": "high"})
        self.assertEqual(payload["text"], {"format": {"type": "json_object"}})
        self.assertEqual(response.text, '{"summary": "ok"}')
        self.assertEqual(response.provider, "grok")
        self.assertEqual(response.usage["xai_reasoning_effort"], "high")

    def test_codex_cli_provider_uses_supported_exec_flags(self) -> None:
        help_text = """
Usage: codex exec [OPTIONS] [PROMPT]
  --ephemeral
  --skip-git-repo-check
  -s, --sandbox <SANDBOX_MODE>
  -o, --output-last-message <FILE>
  -m, --model <MODEL>
  -c, --config <key=value>
"""
        commands: list[list[str]] = []
        run_kwargs: list[dict[str, object]] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[object]:
            commands.append(command)
            run_kwargs.append(kwargs)
            if command == ["codex", "exec", "--help"]:
                return subprocess.CompletedProcess(command, 0, stdout=help_text, stderr="")

            output_index = command.index("--output-last-message") + 1
            Path(command[output_index]).write_text('{"summary": "ok"}', encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        request = LLMRequest(
            agent_id="ingestion_agent",
            task_type="source_ingestion",
            model="codex-test-model",
            system_prompt="Return JSON.",
            user_prompt="pearl 크립토 프로젝트",
            max_tokens=100,
            temperature=0.1,
            response_format="json",
            reasoning_effort="pro",
        )

        with patch("crypto_research_agents.core.llm_provider.shutil.which", return_value="/usr/local/bin/codex"):
            with patch("crypto_research_agents.core.llm_provider.subprocess.run", side_effect=fake_run):
                response = CodexCliProvider().complete(request)

        exec_command = commands[1]
        self.assertEqual(response.text, '{"summary": "ok"}')
        self.assertIn("--sandbox", exec_command)
        self.assertIn("--output-last-message", exec_command)
        self.assertIn("--model", exec_command)
        self.assertIn("--config", exec_command)
        self.assertIn('model_reasoning_effort="xhigh"', exec_command)
        self.assertNotIn("--ask-for-approval", exec_command)
        self.assertEqual(exec_command[-1], "-")
        exec_kwargs = run_kwargs[1]
        self.assertIsInstance(exec_kwargs["input"], bytes)
        self.assertIn("pearl 크립토 프로젝트", exec_kwargs["input"].decode("utf-8"))
        self.assertEqual(response.usage["reasoning_effort"], "pro")
        self.assertEqual(response.usage["codex_model_reasoning_effort"], "xhigh")
        self.assertIs(exec_kwargs["text"], False)

    def test_model_setup_all_logged_in_applies_codex_and_grok_without_raw_tokens(self) -> None:
        output = StringIO()
        with TemporaryDirectory() as tmp:
            settings_path = str(Path(tmp) / "model_settings.json")
            with patch.dict(
                "os.environ",
                {
                    "JIMMORIA_MODEL_SETTINGS_PATH": settings_path,
                    "OPENAI_API_KEY": "openai-secret",
                    "XAI_API_KEY": "xai-secret",
                    "GROK_OAUTH_TOKEN": "oauth-secret",
                },
                clear=True,
            ):
                with patch("crypto_research_agents.cli.codex_sdk_available", return_value=False):
                    with patch("crypto_research_agents.cli.codex_login_status", return_value="Codex logged in"):
                        with patch("crypto_research_agents.cli.claude_cli_available", return_value=False):
                            with patch("builtins.input", return_value="1"):
                                with redirect_stdout(output):
                                    configure_model_panel()
                self.assertEqual(os.environ["LLM_PROVIDER"], "multi")
                self.assertEqual(os.environ["JIMMORIA_MODEL_FAMILIES"], "codex,grok")
                self.assertEqual(os.environ["JIMMORIA_CODEX_PROVIDER"], "codex_cli")
                self.assertEqual(os.environ["JIMMORIA_CODEX_AUTH_PROVIDER"], "oauth")
                self.assertEqual(os.environ["JIMMORIA_GROK_AUTH_PROVIDER"], "xai_oauth")
                settings = json.loads(Path(settings_path).read_text(encoding="utf-8"))
                self.assertEqual(settings["LLM_PROVIDER"], "multi")
                self.assertEqual(settings["JIMMORIA_MODEL_FAMILIES"], "codex,grok")
                self.assertEqual(settings["JIMMORIA_CODEX_PROVIDER"], "codex_cli")
                self.assertEqual(settings["JIMMORIA_CODEX_AUTH_PROVIDER"], "oauth")
                self.assertEqual(settings["JIMMORIA_GROK_AUTH_PROVIDER"], "xai_oauth")
                self.assertNotIn("OPENAI_API_KEY", settings)
                self.assertNotIn("XAI_API_KEY", settings)
                self.assertNotIn("GROK_OAUTH_TOKEN", settings)

        text = output.getvalue()
        self.assertIn("[All logged-in models]", text)
        self.assertIn("API keys are ignored", text)
        self.assertIn("Grok OAuth", text)

    def test_model_setup_offline_choice_uses_screen_flow(self) -> None:
        output = StringIO()
        with TemporaryDirectory() as tmp:
            settings_path = str(Path(tmp) / "model_settings.json")
            with patch.dict(
                "os.environ",
                {"JIMMORIA_MODEL_SETTINGS_PATH": settings_path, "GROK_OAUTH_TOKEN": "session-secret"},
                clear=True,
            ):
                with patch("builtins.input", return_value="6"):
                    with redirect_stdout(output):
                        configure_model_panel()
                self.assertEqual(os.environ["LLM_PROVIDER"], "offline")
                settings = json.loads(Path(settings_path).read_text(encoding="utf-8"))
                self.assertEqual(settings["LLM_PROVIDER"], "offline")

        text = output.getvalue()
        self.assertIn("[Model Setup]", text)
        self.assertIn("[Offline diagnostic fallback]", text)

    def test_model_setup_claude_cli_choice_saves_provider_without_raw_token(self) -> None:
        output = StringIO()
        with TemporaryDirectory() as tmp:
            settings_path = str(Path(tmp) / "model_settings.json")
            with patch.dict(
                "os.environ",
                {"JIMMORIA_MODEL_SETTINGS_PATH": settings_path, "ANTHROPIC_API_KEY": "anthropic-secret"},
                clear=True,
            ):
                with patch("crypto_research_agents.cli.claude_cli_available", return_value=True):
                    with patch("builtins.input", side_effect=["4", "", ""]):
                        with redirect_stdout(output):
                            configure_model_panel()
                self.assertEqual(os.environ["LLM_PROVIDER"], "claude_cli")
                self.assertEqual(os.environ["JIMMORIA_MODEL_FAMILIES"], "claude")
                self.assertEqual(os.environ["JIMMORIA_CLAUDE_AUTH_PROVIDER"], "claude_cli")
                settings = json.loads(Path(settings_path).read_text(encoding="utf-8"))
                self.assertEqual(settings["LLM_PROVIDER"], "claude_cli")
                self.assertEqual(settings["JIMMORIA_MODEL_FAMILIES"], "claude")
                self.assertEqual(settings["JIMMORIA_CLAUDE_AUTH_PROVIDER"], "claude_cli")
                self.assertNotIn("ANTHROPIC_API_KEY", settings)

        text = output.getvalue()
        self.assertIn("[Claude / Anthropic]", text)
        self.assertIn("Supported models are fixed to the Claude model list.", text)

    def test_model_setup_grok_choice_saves_provider_without_raw_token(self) -> None:
        output = StringIO()
        with TemporaryDirectory() as tmp:
            settings_path = str(Path(tmp) / "model_settings.json")
            with patch.dict(
                "os.environ",
                {"JIMMORIA_MODEL_SETTINGS_PATH": settings_path, "GROK_OAUTH_TOKEN": "session-secret"},
                clear=True,
            ):
                with patch("builtins.input", side_effect=["3", "", ""]):
                    with redirect_stdout(output):
                        configure_model_panel()
                self.assertEqual(os.environ["LLM_PROVIDER"], "xai_oauth")
                self.assertEqual(os.environ["JIMMORIA_GROK_AUTH_PROVIDER"], "xai_oauth")
                settings = json.loads(Path(settings_path).read_text(encoding="utf-8"))
                self.assertEqual(settings["LLM_PROVIDER"], "xai_oauth")
                self.assertEqual(settings["JIMMORIA_GROK_AUTH_PROVIDER"], "xai_oauth")
                self.assertNotIn("GROK_OAUTH_TOKEN", settings)

        text = output.getvalue()
        self.assertIn("[Grok / xAI]", text)
        self.assertIn("Supported models are fixed to the Grok/xAI model list.", text)

    def test_model_setup_grok_oauth_choice_uses_hermes_session_without_raw_token(self) -> None:
        output = StringIO()
        with TemporaryDirectory() as tmp:
            settings_path = str(Path(tmp) / "model_settings.json")
            with patch.dict(
                "os.environ",
                {"JIMMORIA_MODEL_SETTINGS_PATH": settings_path, "GROK_OAUTH_TOKEN": "session-secret"},
                clear=True,
            ):
                with patch("builtins.input", side_effect=["3", "2", ""]):
                    with redirect_stdout(output):
                        configure_model_panel()
                self.assertEqual(os.environ["LLM_PROVIDER"], "xai_oauth")
                settings = json.loads(Path(settings_path).read_text(encoding="utf-8"))
                self.assertEqual(settings["LLM_PROVIDER"], "xai_oauth")
                self.assertNotIn("GROK_OAUTH_TOKEN", settings)

        text = output.getvalue()
        self.assertIn("Hermes xAI OAuth", text)
        self.assertIn("Provider: xai_oauth", text)

    def test_chat_input_promotes_first_url_without_fetching_mixed_instruction(self) -> None:
        with patch("crypto_research_agents.cli.fetch_url_text", side_effect=AssertionError("should not fetch mixed chat input")):
            title, content, url = chat_input_to_source("https://x.com/3janexyz 보고서 만들어봐")

        self.assertEqual(url, "https://x.com/3janexyz")
        self.assertIn("보고서", content)
        self.assertIn("https://x.com/3janexyz", title)

    def test_model_settings_default_path_is_user_scoped(self) -> None:
        with TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"XDG_CONFIG_HOME": tmp}, clear=True):
                self.assertEqual(model_settings_path(), Path(tmp) / "jimmoria" / "model_settings.json")

    def test_stale_hybrid_model_settings_are_not_ready_without_user_credentials(self) -> None:
        with TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"LLM_PROVIDER": "codex_grok", "HERMES_HOME": tmp}, clear=True):
                with patch("crypto_research_agents.cli.codex_sdk_available", return_value=False):
                    with patch("crypto_research_agents.cli.codex_login_status", return_value="Codex login status unknown"):
                        self.assertFalse(configured_model_provider_ready())

    def test_stale_codex_cli_settings_are_not_ready_without_codex_command(self) -> None:
        with patch.dict("os.environ", {"LLM_PROVIDER": "codex_cli"}, clear=True):
            with patch("crypto_research_agents.cli.codex_login_status", return_value="Codex CLI not found"):
                self.assertFalse(configured_model_provider_ready())

    def test_codex_setup_can_use_default_model_routes_without_model_names(self) -> None:
        output = StringIO()
        with TemporaryDirectory() as tmp:
            settings_path = str(Path(tmp) / "model_settings.json")
            env = {
                "JIMMORIA_MODEL_SETTINGS_PATH": settings_path,
                "CODEX_MODEL_FAST": "bad-manual-value",
            }
            with patch.dict("os.environ", env, clear=True):
                with patch("crypto_research_agents.cli.codex_login_status", return_value="Logged in using ChatGPT"):
                    with patch("crypto_research_agents.cli.codex_sdk_available", return_value=True):
                        with patch("builtins.input", side_effect=["2", "1", "", ""]):
                            with redirect_stdout(output):
                                configure_model_panel()
                self.assertEqual(os.environ["LLM_PROVIDER"], "codex_sdk")
                self.assertNotIn("CODEX_MODEL_FAST", os.environ)
                settings = json.loads(Path(settings_path).read_text(encoding="utf-8"))
                self.assertEqual(settings["LLM_PROVIDER"], "codex_sdk")
                self.assertNotIn("CODEX_MODEL_FAST", settings)

        text = output.getvalue()
        self.assertIn("Supported models are fixed to the Codex model list.", text)
        self.assertIn("Using provider default for every agent.", text)

    def test_chat_skips_startup_model_setup_when_provider_is_saved(self) -> None:
        output = StringIO()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings_path = root / "model_settings.json"
            settings_path.write_text('{"LLM_PROVIDER": "codex_sdk"}', encoding="utf-8")
            args = argparse.Namespace(
                memory=str(root / "memory.json"),
                vault=str(root / "vault"),
                reports=str(root / "reports"),
                skip_model_setup=False,
            )
            with patch.dict("os.environ", {"JIMMORIA_MODEL_SETTINGS_PATH": str(settings_path)}, clear=True):
                with patch("crypto_research_agents.cli.codex_login_status", return_value="Logged in using ChatGPT"):
                    with patch("crypto_research_agents.cli.codex_sdk_available", return_value=True):
                        with patch("sys.stdin.isatty", return_value=True):
                            with patch("builtins.input", return_value="/quit"):
                                with patch("crypto_research_agents.cli.configure_model_panel") as setup_panel:
                                    with redirect_stdout(output):
                                        chat_command(args)
                                    self.assertFalse(setup_panel.called)

        self.assertIn("JIMMORIA v0.1.0", output.getvalue())

    def test_chat_autodetects_existing_codex_login(self) -> None:
        output = StringIO()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings_path = root / "model_settings.json"
            args = argparse.Namespace(
                memory=str(root / "memory.json"),
                vault=str(root / "vault"),
                reports=str(root / "reports"),
                skip_model_setup=False,
            )
            with patch.dict("os.environ", {"JIMMORIA_MODEL_SETTINGS_PATH": str(settings_path)}, clear=True):
                with patch("crypto_research_agents.cli.codex_login_status", return_value="Logged in using ChatGPT"):
                    with patch("crypto_research_agents.cli.codex_sdk_available", return_value=True):
                        with patch("sys.stdin.isatty", return_value=True):
                            with patch("builtins.input", return_value="/quit"):
                                with patch("crypto_research_agents.cli.configure_model_panel") as setup_panel:
                                    with redirect_stdout(output):
                                        chat_command(args)
                                    self.assertFalse(setup_panel.called)
                self.assertEqual(os.environ["LLM_PROVIDER"], "codex_sdk")
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
                self.assertEqual(settings["LLM_PROVIDER"], "codex_sdk")

        self.assertIn("JIMMORIA v0.1.0", output.getvalue())

    def test_codex_sdk_provider_runs_thread_with_selected_model(self) -> None:
        calls: dict[str, object] = {}

        class FakeResult:
            id = "turn_test"
            status = "completed"
            duration_ms = 123
            final_response = "sdk ok"
            usage = None

        class FakeThread:
            def run(self, prompt: str, **kwargs: object) -> FakeResult:
                calls["prompt"] = prompt
                calls["run_kwargs"] = kwargs
                return FakeResult()

        class FakeCodex:
            def __init__(self, config: object | None = None) -> None:
                calls["config"] = config

            def __enter__(self) -> "FakeCodex":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def thread_start(self, **kwargs: object) -> FakeThread:
                calls["thread_start"] = kwargs
                return FakeThread()

        class FakeCodexConfig:
            def __init__(self, **kwargs: object) -> None:
                self.kwargs = kwargs

        fake_module = types.SimpleNamespace(
            ApprovalMode=types.SimpleNamespace(
                deny_all="deny_all",
                auto_review="auto_review",
            ),
            Codex=FakeCodex,
            CodexConfig=FakeCodexConfig,
            Sandbox=types.SimpleNamespace(
                read_only="read_only",
                workspace_write="workspace_write",
                full_access="full_access",
            ),
        )
        request = LLMRequest(
            agent_id="supervisor_agent",
            task_type="supervisor_chat",
            model="gpt-5.4-mini",
            system_prompt="Talk as supervisor.",
            user_prompt="안녕",
            max_tokens=100,
            temperature=0.2,
        )

        with patch.dict(sys.modules, {"openai_codex": fake_module}):
            with patch.dict("os.environ", {"CODEX_SDK_SANDBOX": "workspace_write"}, clear=True):
                response = CodexSdkProvider().complete(request)

        self.assertEqual(response.text, "sdk ok")
        thread_start = calls["thread_start"]
        assert isinstance(thread_start, dict)
        self.assertEqual(thread_start["model"], "gpt-5.4-mini")
        self.assertEqual(thread_start["sandbox"], "workspace_write")
        self.assertEqual(thread_start["approval_mode"], "deny_all")
        self.assertTrue(thread_start["ephemeral"])
        self.assertEqual(thread_start["developer_instructions"], "Talk as supervisor.")
        self.assertIn("안녕", str(calls["prompt"]))
        self.assertEqual(response.usage["approval_mode"], "deny_all")
        self.assertEqual(response.usage["duration_ms"], 123)
        self.assertEqual(response.usage["reasoning_effort"], "standard")

    def test_codex_model_env_has_priority(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "CODEX_CLI_MODEL_FAST": "codex-cli-fast",
                "CODEX_MODEL_FAST": "gpt-5.5",
            },
            clear=True,
        ):
            gateway = ModelGateway(provider=None)

        self.assertEqual(gateway.default_model, "gpt-5.5")

    def test_grok_model_env_routes_reasoning_work(self) -> None:
        class FakeGrokProvider:
            provider_name = "grok"

            def complete(self, request: LLMRequest) -> LLMResponse:
                return LLMResponse(text="ok", model=request.model, provider=self.provider_name, usage={})

        with patch.dict(
            "os.environ",
            {"GROK_MODEL_REASONING": "grok-4.20-multi-agent"},
            clear=True,
        ):
            gateway = ModelGateway(provider=FakeGrokProvider())
            reasoning = gateway.select(agent_id="discovery_agent", task_type="candidate_discovery")
            writing = gateway.select(agent_id="report_agent", task_type="final_synthesis")

        self.assertEqual(gateway.default_model, "grok-4.3")
        self.assertEqual(reasoning.selected_model, "grok-4.20-multi-agent")
        self.assertEqual(writing.selected_model, "grok-4.3")

    def test_codex_grok_hybrid_routes_tasks_to_both_providers(self) -> None:
        class FakeCodexProvider:
            provider_name = "codex_cli"

            def __init__(self) -> None:
                self.requests: list[LLMRequest] = []

            def complete(self, request: LLMRequest) -> LLMResponse:
                self.requests.append(request)
                return LLMResponse(text="codex ok", model=request.model, provider=self.provider_name, usage={})

        class FakeGrokProvider:
            provider_name = "grok"

            def __init__(self) -> None:
                self.requests: list[LLMRequest] = []

            def complete(self, request: LLMRequest) -> LLMResponse:
                self.requests.append(request)
                return LLMResponse(text="grok ok", model=request.model, provider=self.provider_name, usage={})

        codex = FakeCodexProvider()
        grok = FakeGrokProvider()
        with patch.dict("os.environ", {"LLM_PROVIDER": "codex_grok"}, clear=True):
            gateway = ModelGateway(providers={"codex": codex, "grok": grok})
            discovery_decision = gateway.select(agent_id="discovery_agent", task_type="candidate_discovery")
            narrative_decision = gateway.select(agent_id="narrative_agent", task_type="narrative_reasoning")
            social_decision = gateway.select(agent_id="social_kol_agent", task_type="social_summary")
            supervisor_decision = gateway.select(agent_id="supervisor_agent", task_type="supervisor_chat")
            ingestion_decision = gateway.select(agent_id="ingestion_agent", task_type="source_ingestion")
            contract_decision = gateway.select(agent_id="contract_onchain_agent", task_type="contract_info")
            product_decision = gateway.select(agent_id="product_tech_agent", task_type="product_docs")
            funding_decision = gateway.select(agent_id="funding_token_agent", task_type="funding_token")
            report_decision = gateway.select(agent_id="report_agent", task_type="final_synthesis")
            obsidian_decision = gateway.select(agent_id="obsidian_curator_agent", task_type="obsidian_sync")
            social = gateway.complete(
                agent_id="social_kol_agent",
                task_type="social_summary",
                system_prompt="social",
                user_prompt="summarize X/KOL",
            )
            report = gateway.complete(
                agent_id="report_agent",
                task_type="final_synthesis",
                system_prompt="report",
                user_prompt="write final report",
            )

        self.assertEqual(gateway.provider_name, "codex_grok")
        self.assertEqual(discovery_decision.provider_family, "grok")
        self.assertEqual(narrative_decision.provider_family, "grok")
        self.assertEqual(social_decision.provider_family, "grok")
        self.assertEqual(supervisor_decision.provider_family, "codex")
        self.assertEqual(ingestion_decision.provider_family, "codex")
        self.assertEqual(contract_decision.provider_family, "codex")
        self.assertEqual(product_decision.provider_family, "codex")
        self.assertEqual(funding_decision.provider_family, "codex")
        self.assertEqual(discovery_decision.selected_model, "grok-4.3")
        self.assertEqual(report_decision.provider_family, "codex")
        self.assertEqual(obsidian_decision.provider_family, "codex")
        self.assertEqual(report_decision.selected_model, "gpt-5.5")
        self.assertEqual(social.provider, "grok")
        self.assertEqual(report.provider, "codex_cli")
        self.assertEqual(len(grok.requests), 1)
        self.assertEqual(len(codex.requests), 1)
        self.assertEqual(gateway.call_log[0]["provider_family"], "grok")
        self.assertEqual(gateway.call_log[1]["provider_family"], "codex")

    def test_multi_model_families_route_agents_internally(self) -> None:
        class FakeProvider:
            def __init__(self, provider_name: str) -> None:
                self.provider_name = provider_name
                self.requests: list[LLMRequest] = []

            def complete(self, request: LLMRequest) -> LLMResponse:
                self.requests.append(request)
                return LLMResponse(text=f"{self.provider_name} ok", model=request.model, provider=self.provider_name, usage={})

        codex = FakeProvider("codex_api")
        grok = FakeProvider("grok")
        claude = FakeProvider("claude_api")
        with patch.dict("os.environ", {"LLM_PROVIDER": "multi", "JIMMORIA_MODEL_FAMILIES": "codex,grok,claude"}, clear=True):
            gateway = ModelGateway(providers={"codex": codex, "grok": grok, "claude": claude})
            supervisor = gateway.select(agent_id="supervisor_agent", task_type="supervisor_chat")
            social = gateway.select(agent_id="social_kol_agent", task_type="social_summary")
            product = gateway.select(agent_id="product_tech_agent", task_type="product_docs")
            report = gateway.select(agent_id="report_agent", task_type="final_synthesis")

        self.assertEqual(supervisor.provider_family, "codex")
        self.assertEqual(social.provider_family, "grok")
        self.assertEqual(product.provider_family, "claude")
        self.assertEqual(report.provider_family, "claude")
        self.assertEqual(report.selected_model, "claude-sonnet-4-5")

    def test_codex_grok_hybrid_falls_back_to_codex_when_grok_fails(self) -> None:
        class FakeCodexProvider:
            provider_name = "codex_cli"

            def __init__(self) -> None:
                self.requests: list[LLMRequest] = []

            def complete(self, request: LLMRequest) -> LLMResponse:
                self.requests.append(request)
                return LLMResponse(text="codex fallback ok", model=request.model, provider=self.provider_name, usage={})

        class FailingGrokProvider:
            provider_name = "grok"

            def __init__(self) -> None:
                self.requests: list[LLMRequest] = []

            def complete(self, request: LLMRequest) -> LLMResponse:
                self.requests.append(request)
                raise RuntimeError("Grok provider failed: HTTP 403: bad-credentials")

        codex = FakeCodexProvider()
        grok = FailingGrokProvider()
        with patch.dict("os.environ", {"LLM_PROVIDER": "codex_grok"}, clear=True):
            gateway = ModelGateway(providers={"codex": codex, "grok": grok})
            response = gateway.complete(
                agent_id="narrative_agent",
                task_type="narrative_reasoning",
                system_prompt="narrative",
                user_prompt="map market narrative",
                response_format="json",
            )

        self.assertEqual(response.provider, "codex_cli")
        self.assertEqual(response.model, "gpt-5.5")
        self.assertEqual(len(grok.requests), 1)
        self.assertEqual(grok.requests[0].model, "grok-4.3")
        self.assertEqual(len(codex.requests), 1)
        self.assertEqual(codex.requests[0].model, "gpt-5.5")
        self.assertEqual(response.usage["fallback_from_provider"], "grok")
        self.assertEqual(response.usage["fallback_from_provider_family"], "grok")
        self.assertIn("bad-credentials", response.usage["fallback_error"])
        self.assertEqual(gateway.call_log[0]["requested_provider_family"], "grok")
        self.assertEqual(gateway.call_log[0]["provider_family"], "codex")
        self.assertEqual(gateway.call_log[0]["fallback_from_provider"], "grok")

    def test_codex_grok_hybrid_agent_override_can_route_one_worker(self) -> None:
        class FakeProvider:
            def __init__(self, provider_name: str) -> None:
                self.provider_name = provider_name

            def complete(self, request: LLMRequest) -> LLMResponse:
                return LLMResponse(text="ok", model=request.model, provider=self.provider_name, usage={})

        with patch.dict(
            "os.environ",
            {
                "LLM_PROVIDER": "codex_grok",
                "JIMMORIA_AGENT_PROVIDER_FUNDING_TOKEN_AGENT": "xai",
            },
            clear=True,
        ):
            gateway = ModelGateway(
                providers={
                    "codex": FakeProvider("codex_cli"),
                    "grok": FakeProvider("grok"),
                }
            )
            funding = gateway.select(agent_id="funding_token_agent", task_type="funding_token")
            report = gateway.select(agent_id="report_agent", task_type="final_synthesis")

        self.assertEqual(funding.provider_family, "grok")
        self.assertEqual(report.provider_family, "codex")

    def test_codex_grok_hybrid_grok_auth_provider_controls_oauth_priority(self) -> None:
        class FakeProvider:
            def __init__(self, provider_name: str, *, prefer_hermes_oauth: bool | None = None) -> None:
                self.provider_name = provider_name
                self.prefer_hermes_oauth = prefer_hermes_oauth

            def complete(self, request: LLMRequest) -> LLMResponse:
                return LLMResponse(text="ok", model=request.model, provider=self.provider_name, usage={})

        seen: dict[str, object] = {}

        def fake_grok_provider(*, prefer_hermes_oauth: bool) -> FakeProvider:
            seen["prefer_hermes_oauth"] = prefer_hermes_oauth
            return FakeProvider("grok", prefer_hermes_oauth=prefer_hermes_oauth)

        with patch.dict(
            "os.environ",
            {
                "LLM_PROVIDER": "codex_grok",
                "JIMMORIA_GROK_AUTH_PROVIDER": "api_key",
            },
            clear=True,
        ):
            with patch(
                "crypto_research_agents.core.model_gateway._codex_provider_from_env",
                return_value=FakeProvider("codex_cli"),
            ):
                with patch("crypto_research_agents.core.model_gateway.GrokProvider", side_effect=fake_grok_provider):
                    gateway = ModelGateway()

        self.assertEqual(gateway.provider_name, "codex_grok")
        self.assertFalse(seen["prefer_hermes_oauth"])

    def test_parse_json_response_accepts_fenced_or_prefaced_json(self) -> None:
        fenced = LLMResponse(
            text='```json\n{"summary": "ok", "confidence": 0.8}\n```',
            model="test",
            provider="fake",
            usage={},
        )
        prefaced = LLMResponse(
            text='Here is the result:\n{"summary": "wrapped", "risks": ["none"]}\nDone.',
            model="test",
            provider="fake",
            usage={},
        )

        self.assertEqual(parse_json_response(fenced)["summary"], "ok")
        self.assertEqual(parse_json_response(prefaced)["summary"], "wrapped")

    def test_codex_defaults_use_official_models_by_route(self) -> None:
        class FakeCodexCliProvider:
            provider_name = "codex_cli"

            def complete(self, request: LLMRequest) -> LLMResponse:
                return LLMResponse(
                    text="{}" if request.response_format == "json" else "ok",
                    model=request.model,
                    provider=self.provider_name,
                    usage={},
                )

        with patch.dict("os.environ", {}, clear=True):
            gateway = ModelGateway(provider=FakeCodexCliProvider())

        reasoning = gateway.select(agent_id="discovery_agent", task_type="candidate_discovery")
        ingestion = gateway.select(agent_id="ingestion_agent", task_type="source_ingestion")
        obsidian = gateway.select(agent_id="obsidian_curator_agent", task_type="obsidian_sync")
        writing = gateway.select(agent_id="report_agent", task_type="final_synthesis")

        fast = gateway.select(agent_id="supervisor_agent", task_type="supervisor_chat")

        self.assertEqual(fast.selected_model, "gpt-5.4-mini")
        self.assertEqual(ingestion.selected_model, "gpt-5.5")
        self.assertEqual(reasoning.selected_model, "gpt-5.5")
        self.assertEqual(obsidian.selected_model, "gpt-5.5")
        self.assertEqual(writing.selected_model, "gpt-5.5")
        self.assertEqual(fast.reasoning_effort, "standard")
        self.assertEqual(ingestion.reasoning_effort, "pro")
        self.assertEqual(reasoning.reasoning_effort, "pro")
        self.assertEqual(obsidian.reasoning_effort, "pro")
        self.assertEqual(writing.reasoning_effort, "pro")

    def test_doctor_marks_secret_backed_connectors_as_missing_secret(self) -> None:
        with patch.dict("os.environ", _offline_no_secret_env(), clear=False):
            statuses = {item.name: item.status for item in collect_capabilities()}

        self.assertEqual(statuses["Runtime scaffold"], "configured")
        self.assertEqual(statuses["Agent specs/personas"], "configured")
        self.assertEqual(statuses["Agent LLM routing"], "fallback")
        self.assertEqual(statuses["X/Twitter search"], "missing_secret")
        self.assertEqual(statuses["RootData project directory"], "missing_secret")
        self.assertEqual(statuses["Explorer contract lookup"], "missing_secret")
        self.assertEqual(statuses["Funding/airdrop checker"], "configured")
        self.assertEqual(statuses["Docs crawler"], "configured")
        self.assertEqual(statuses["GitHub reader"], "configured")
        self.assertEqual(statuses["GitHub repo search"], "configured")
        self.assertEqual(statuses["RSS feed monitor"], "configured")
        self.assertEqual(statuses["DefiLlama protocol data"], "configured")
        self.assertEqual(statuses["Snapshot governance API"], "configured")
        self.assertEqual(statuses["CoinGecko metadata"], "configured")
        self.assertEqual(statuses["DEX pair lookup"], "configured")
        self.assertEqual(statuses["DEX Screener pair search"], "configured")
        self.assertEqual(statuses["Dune query execution"], "missing_secret")
        self.assertEqual(statuses["The Graph subgraph query"], "missing_secret")
        self.assertEqual(statuses["Overall"], "placeholder")

    def test_tool_registry_contains_required_research_stack(self) -> None:
        registry = json.loads(Path("config/tools/tool_registry.yaml").read_text(encoding="utf-8"))

        self.assertIn("x_search_posts", registry["minimum_viable_live_stack"])
        self.assertIn("rootdata_search_projects", registry["minimum_viable_live_stack"])
        self.assertIn("claim_evidence_check", registry["safety"])
        self.assertEqual(registry["tool_meta"]["fetch_url"]["implementation_status"], "implemented")
        self.assertEqual(registry["tool_meta"]["web_search"]["implementation_status"], "implemented")
        self.assertEqual(registry["tool_meta"]["crawl_docs"]["implementation_status"], "implemented")
        self.assertEqual(registry["tool_meta"]["x_search_posts"]["priority"], "required")
        self.assertEqual(registry["tool_meta"]["rootdata_get_project"]["owner_agent"], "funding_token_agent")
        self.assertIn("github_get_repo_activity", registry["minimum_viable_live_stack"])
        self.assertIn("defillama_protocol_search", registry["minimum_viable_live_stack"])
        self.assertIn("snapshot_get_proposals", registry["minimum_viable_live_stack"])
        self.assertIn("skill_view", registry["operator_bridge"])
        self.assertIn("browser_navigate", registry["operator_bridge"])
        self.assertIn("multi_tool_use.parallel", registry["operator_bridge"])
        self.assertEqual(registry["tool_meta"]["github_get_repo_activity"]["implementation_status"], "implemented")
        self.assertEqual(registry["tool_meta"]["defillama_protocol_search"]["implementation_status"], "implemented")
        self.assertEqual(registry["tool_meta"]["snapshot_get_proposals"]["implementation_status"], "implemented")
        self.assertEqual(registry["tool_meta"]["get_dex_pair"]["implementation_status"], "implemented")
        self.assertEqual(registry["tool_meta"]["get_token_metadata"]["implementation_status"], "implemented")
        self.assertEqual(registry["tool_meta"]["terminal"]["implementation_status"], "implemented")
        self.assertEqual(registry["tool_meta"]["browser_vision"]["implementation_status"], "external_connector_required")

    def test_model_router_contains_supervisor_chat_route(self) -> None:
        router = json.loads(Path("config/models/model_router.yaml").read_text(encoding="utf-8"))

        self.assertEqual(router["provider"], "multi_model")
        self.assertIn("gpt-5.5", router["supported_models"]["codex"])
        self.assertIn("grok-4.3", router["supported_models"]["grok"])
        self.assertIn("codex_grok", router["runtime_order"])
        self.assertIn("codex_sdk", router["runtime_order"])
        self.assertIn("xai_oauth", router["runtime_order"])
        self.assertIn("grok", router["runtime_order"])
        self.assertEqual(router["env"]["hybrid_codex_provider"], "JIMMORIA_CODEX_PROVIDER")
        self.assertEqual(router["env"]["hybrid_grok_auth_provider"], "JIMMORIA_GROK_AUTH_PROVIDER")
        self.assertEqual(router["env"]["hybrid_grok_tasks"], "JIMMORIA_GROK_TASKS")
        self.assertEqual(router["env"]["hybrid_grok_agents"], "JIMMORIA_GROK_AGENTS")
        self.assertEqual(router["env"]["agent_provider_override"], "JIMMORIA_AGENT_PROVIDER_<AGENT_ID>")
        self.assertEqual(router["env"]["hermes_home"], "HERMES_HOME")
        self.assertEqual(router["env"]["hermes_auth_json"], "HERMES_AUTH_JSON")
        self.assertEqual(router["routes"]["supervisor_chat"], "fast_chat_model")
        for task_type in [
            "supervision",
            "source_ingestion",
            "narrative_reasoning",
            "candidate_discovery",
            "social_summary",
            "contract_info",
            "product_docs",
            "funding_token",
            "obsidian_sync",
        ]:
            self.assertEqual(router["routes"][task_type], "reasoning_model")
        self.assertEqual(router["defaults"]["codex"]["reasoning_model"], "gpt-5.5")
        self.assertEqual(router["defaults"]["codex"]["writing_model"], "gpt-5.5")
        self.assertEqual(router["defaults"]["codex"]["fast_chat_model"], "gpt-5.4-mini")
        self.assertEqual(router["defaults"]["grok"]["reasoning_model"], "grok-4.3")
        self.assertEqual(router["defaults"]["reasoning_effort"], "pro")
        self.assertEqual(router["routes"]["source_ingestion"], "reasoning_model")
        self.assertEqual(router["hybrid_provider_routes"]["provider"], "codex_grok")
        self.assertEqual(router["hybrid_provider_routes"]["agent_provider_families"]["supervisor_agent"], "codex")
        self.assertEqual(router["hybrid_provider_routes"]["agent_provider_families"]["narrative_agent"], "grok")
        self.assertEqual(router["hybrid_provider_routes"]["agent_provider_families"]["discovery_agent"], "grok")
        self.assertEqual(router["hybrid_provider_routes"]["agent_provider_families"]["social_kol_agent"], "grok")
        self.assertEqual(router["hybrid_provider_routes"]["agent_provider_families"]["report_agent"], "codex")
        self.assertIn("social_summary", router["hybrid_provider_routes"]["grok_task_types"])
        self.assertIn("report_writing", router["hybrid_provider_routes"]["codex_task_types"])
        self.assertEqual(router["reasoning_effort"]["codex_cli_pro_value"], "xhigh")

    def test_doctor_command_outputs_current_limitations(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = StringIO()
            with patch.dict("os.environ", _offline_no_secret_env(), clear=False):
                with redirect_stdout(output):
                    cli_main(
                        [
                            "doctor",
                            "--vault",
                            str(root / "vault"),
                            "--reports",
                            str(root / "reports"),
                            "--memory",
                            str(root / "memory.json"),
                        ]
                    )

            text = output.getvalue()
            self.assertIn("Runtime scaffold", text)
            self.assertIn("X/Twitter search: missing_secret", text)
            self.assertIn("Core runtime and low-cost connectors run", text)

    def test_tool_audit_log_records_live_connector_secret_states(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict("os.environ", _offline_no_secret_env(), clear=False):
                runtime = ResearchRuntime()
                result = runtime.run_article_research(
                    title="Live Connector Reality Check",
                    content="AI wallet automation with KOL mentions, docs, GitHub, and airdrop points.",
                    vault_dir=root / "vault",
                    reports_dir=root / "reports",
                    memory_path=root / "memory.json",
                )

            audit_path = root / "runs" / result.room.room_id / "tool_audit_log.json"
            audit_log = json.loads(audit_path.read_text(encoding="utf-8"))
            statuses = {(item["tool_name"], item["status"]) for item in audit_log}

            self.assertIn(("x_search_posts", "missing_secret"), statuses)
            self.assertIn(("get_contract_address", "missing_input"), statuses)
            crawl_docs_statuses = {status for tool, status in statuses if tool == "crawl_docs"}
            self.assertTrue(crawl_docs_statuses)
            self.assertFalse("unconfigured" in crawl_docs_statuses)
            self.assertFalse(any(tool == "check_airdrop_points" and status == "unconfigured" for tool, status in statuses))

    def test_tool_registry_registers_existing_connectors(self) -> None:
        registry = load_tool_registry()

        self.assertEqual(registry.get("web_search").implementation_status, "implemented")
        self.assertEqual(registry.get("github_search_repos").implementation_status, "implemented")
        self.assertEqual(registry.get("read_github_repo").implementation_status, "implemented")
        self.assertEqual(registry.get("github_get_repo_activity").implementation_status, "implemented")
        self.assertEqual(registry.get("rss_monitor_feed").implementation_status, "implemented")
        self.assertEqual(registry.get("defillama_protocol_search").implementation_status, "implemented")
        self.assertEqual(registry.get("defillama_tvl_snapshot").implementation_status, "implemented")
        self.assertEqual(registry.get("snapshot_get_proposals").implementation_status, "implemented")
        self.assertEqual(registry.get("dexscreener_search_pairs").implementation_status, "implemented")
        self.assertEqual(registry.get("coingecko_coin_metadata").implementation_status, "implemented")
        self.assertEqual(registry.get("get_dex_pair").implementation_status, "implemented")
        self.assertEqual(registry.get("get_token_metadata").implementation_status, "implemented")
        self.assertEqual(registry.get("create_task").implementation_status, "implemented")
        self.assertEqual(registry.get("assign_task").implementation_status, "implemented")
        self.assertEqual(registry.get("x_search_posts").implementation_status, "implemented")
        self.assertNotIn("telegram_read_channel", registry.definitions)
        self.assertNotIn("discord_read_channel", registry.definitions)
        self.assertEqual(registry.get("rootdata_search_projects").implementation_status, "implemented")
        self.assertEqual(registry.get("explorer_lookup").implementation_status, "implemented")
        self.assertEqual(registry.get("check_airdrop_points").implementation_status, "implemented")
        self.assertEqual(registry.get("skill_view").implementation_status, "implemented")
        self.assertEqual(registry.get("browser_console").implementation_status, "implemented")
        self.assertEqual(registry.get("write_file").implementation_status, "implemented")
        self.assertEqual(registry.get("terminal").mode, "dangerous")
        self.assertEqual(registry.get("send_message").mode, "write")
        self.assertEqual(registry.get("browser_vision").implementation_status, "external_connector_required")

    def test_toolset_limits_agent_access(self) -> None:
        registry = load_tool_registry()
        tools = registry.allowed_tools_for_toolsets(["research_basic"])

        self.assertIn("web_search", tools)
        self.assertIn("read_github_repo", tools)
        self.assertNotIn("wallet_sign", tools)

        bridge_tools = registry.allowed_tools_for_toolsets(["operator_research_bridge"])
        self.assertIn("browser_navigate", bridge_tools)
        self.assertIn("write_file", bridge_tools)
        self.assertIn("multi_tool_use.parallel", bridge_tools)
        self.assertNotIn("terminal", bridge_tools)
        self.assertNotIn("send_message", bridge_tools)
        registry.assert_toolsets_research_safe(["operator_research_bridge"])

    def test_supervisor_office_toolset_is_research_safe(self) -> None:
        registry = load_tool_registry()
        tools = registry.allowed_tools_for_toolsets(["supervisor_office"])

        self.assertIn("create_task", tools)
        self.assertIn("assign_task", tools)
        self.assertIn("agent_handoff", tools)
        registry.assert_toolsets_research_safe(["supervisor_office"])

    def test_read_only_boundary_blocks_dangerous_tools(self) -> None:
        registry = load_tool_registry()

        self.assertFalse(registry.is_tool_allowed_for_research("swap"))
        self.assertFalse(registry.is_tool_allowed_for_research("wallet_sign"))
        with self.assertRaises(PermissionError):
            registry.assert_toolsets_research_safe(["blocked_by_default"])

    def test_cron_no_signal_silent_output(self) -> None:
        result = CronRegistry.load().run_job("early_radar_30m")

        self.assertEqual(result.status, "no_signal")
        self.assertFalse(result.should_notify)
        self.assertEqual(result.output, "")

    def test_skill_loader_attaches_playbook(self) -> None:
        workflow = load_workflow_spec("project_diligence_v1")
        registry = ResearchPlaybookRegistry.load_dir()

        attached = registry.attach_to_workflow(
            workflow,
            ["base_token_identity_gate", "ticker_collision_review"],
        )

        self.assertEqual([item.playbook_id for item in attached], ["base_token_identity_gate", "ticker_collision_review"])
        self.assertEqual(workflow.metadata["attached_playbooks"], ["base_token_identity_gate", "ticker_collision_review"])

    def test_skill_spec_registry_loads_config_and_registry_entries(self) -> None:
        registry = SkillSpecRegistry.load_dir("config/skills")

        identity_gate = registry.get("identity_gate")
        self.assertIsNotNone(identity_gate)
        assert identity_gate is not None
        self.assertIn("check_ticker_collision", identity_gate.steps)
        self.assertIn("identity_status", identity_gate.required_output)

        market_signal = registry.get("market_signal_intake_skill")
        self.assertIsNotNone(market_signal)
        assert market_signal is not None
        self.assertEqual(market_signal.owner_agents, ["social_kol_agent"])
        self.assertIn("build_who_said_what_rows", market_signal.steps)

        localization = registry.get("report_language_localization_skill")
        self.assertIsNotNone(localization)
        assert localization is not None
        self.assertIn("epoko77-ai/im-not-ai", localization.description)
        self.assertIn("preserve_facts_numbers_names_quotes", localization.steps)
        self.assertIn("remove_korean_translationese", localization.steps)
        self.assertIn("no_over_humanizing", localization.quality_gates)

    def test_korean_report_humanize_prompt_includes_im_not_ai_contract(self) -> None:
        prompt = korean_report_humanize_prompt()

        self.assertIn("epoko77-ai/im-not-ai", prompt)
        self.assertIn("Korean report localization style contract", prompt)
        self.assertIn("translationese", prompt)
        self.assertIn("facts, numbers, dates", prompt)
        self.assertIn("Do not over-humanize", prompt)

    def test_report_agent_injects_korean_humanize_prompt_for_ko_reports(self) -> None:
        class CapturingGateway:
            def __init__(self) -> None:
                self.system_prompts: list[str] = []

            def complete(self, **kwargs: object) -> LLMResponse:
                self.system_prompts.append(str(kwargs["system_prompt"]))
                return LLMResponse(text="summary", model="test", provider="fake", usage={})

        gateway = CapturingGateway()
        agent = ReportAgent(model_gateway=gateway)  # type: ignore[arg-type]
        room = ResearchRoom(topic="Zcash report", goals=["write a report"], agents=["report_agent"])

        agent._write_llm_summary(
            room,
            SharedMemory(),
            [],
            company_settings=CompanySettings(report_language="ko"),
        )

        self.assertTrue(gateway.system_prompts)
        self.assertIn("epoko77-ai/im-not-ai", gateway.system_prompts[-1])
        self.assertIn("Korean report localization style contract", gateway.system_prompts[-1])

    def test_report_agent_skips_korean_humanize_prompt_for_non_ko_reports(self) -> None:
        class CapturingGateway:
            def __init__(self) -> None:
                self.system_prompts: list[str] = []

            def complete(self, **kwargs: object) -> LLMResponse:
                self.system_prompts.append(str(kwargs["system_prompt"]))
                return LLMResponse(text="summary", model="test", provider="fake", usage={})

        gateway = CapturingGateway()
        agent = ReportAgent(model_gateway=gateway)  # type: ignore[arg-type]
        room = ResearchRoom(topic="Zcash report", goals=["write a report"], agents=["report_agent"])

        agent._write_llm_summary(
            room,
            SharedMemory(),
            [],
            company_settings=CompanySettings(report_language="en"),
        )

        self.assertTrue(gateway.system_prompts)
        self.assertNotIn("epoko77-ai/im-not-ai", gateway.system_prompts[-1])

    def test_report_agent_spec_uses_korean_humanize_rules(self) -> None:
        spec = AgentSpecRegistry.load_dir("config/agents").get("report_agent")
        self.assertIsNotNone(spec)
        assert spec is not None

        prompt = spec.system_prompt()

        self.assertIn("report_language_localization_skill", spec.skills.secondary)
        self.assertIn("epoko77-ai/im-not-ai", prompt)
        self.assertIn("translationese", prompt)

    def test_hook_registry_loads_common_hooks_and_manifest_dirs(self) -> None:
        registry = HookRegistry.load_dir("config/hooks")

        self.assertIsNotNone(registry.get("source_id_writer"))
        self.assertIn(
            "source_id_writer",
            [hook.hook_id for hook in registry.hooks_for_event("tool:done")],
        )
        self.assertIn(
            "claim_coverage_check",
            [hook.hook_id for hook in registry.hooks_for_phase("before_report")],
        )
        self.assertEqual(runtime_event_to_hook_event("agent_start"), "agent:start")

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_dir = root / "claim_coverage_check"
            manifest_dir.mkdir()
            (manifest_dir / "HOOK.yaml").write_text(
                json.dumps(
                    {
                        "name": "claim_coverage_check",
                        "description": "Check claims before final report render.",
                        "events": ["report:before_render", "supervisor:final_review"],
                        "blocking": False,
                        "priority": 25,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            custom = HookRegistry.load_dir(root)
            hook = custom.get("claim_coverage_check")

        self.assertIsNotNone(hook)
        assert hook is not None
        self.assertEqual(hook.priority, 25)
        self.assertIn("report:before_render", hook.events)

    def test_profile_worker_allowed_tools(self) -> None:
        tool_registry = load_tool_registry()
        profile = WorkerProfileRegistry.load().get("researcher")

        self.assertIsNotNone(profile)
        assert profile is not None
        allowed = profile.allowed_tools(tool_registry)
        self.assertIn("web_search", allowed)
        self.assertIn("dexscreener_search_pairs", allowed)
        self.assertNotIn("wallet_sign", allowed)

    def test_artifact_store_writes_tool_calls(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = ResearchRuntime()
            result = runtime.run_source_ingestion(
                title="Artifact Contract Check",
                content="Store this source and archive tool calls.",
                vault_dir=root / "vault",
                memory_path=root / "memory.json",
            )
            workflow = load_workflow_spec("project_diligence_v1")
            artifact_dir = ArtifactStore(root / "runs").archive_workflow_run(
                result=result,
                workflow=workflow,
                workflow_trace=[],
                event_log=runtime.event_log,
                tool_audit_log=[{"tool_name": "fetch_url", "status": "success"}],
                input_payload={"topic": "Artifact Contract Check"},
            )

            self.assertTrue((artifact_dir / "input.json").exists())
            self.assertTrue((artifact_dir / "tool_calls.jsonl").exists())
            self.assertIn("fetch_url", (artifact_dir / "tool_calls.jsonl").read_text(encoding="utf-8"))

    def test_session_search_by_contract(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "room_contract"
            run_dir.mkdir()
            (run_dir / "candidates.json").write_text(
                json.dumps(
                    [{"project": "Pearl", "contract": "0xabc123def456"}],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            results = search_sessions("0xabc123def456", runs_dir=root)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].room_id, "room_contract")
        self.assertEqual(results[0].matched_file, "candidates.json")

    def test_doctor_uses_public_web_research_stack_without_chat_connectors(self) -> None:
        capabilities = {item.name: item for item in collect_capabilities()}

        self.assertEqual(capabilities["Tool registry"].status, "configured")
        self.assertEqual(capabilities["Scheduled jobs"].status, "configured")
        self.assertEqual(capabilities["Worker profiles"].status, "configured")
        self.assertNotIn("Telegram delivery config", capabilities)
        self.assertNotIn("Telegram read", capabilities)
        self.assertNotIn("Discord read", capabilities)
        self.assertIn("Public web search", capabilities)

    def test_web_dashboard_html_exposes_company_structure(self) -> None:
        html = render_dashboard_html()

        self.assertIn("JIMMORIA Web Research HQ", html)
        self.assertIn("Company Structure", html)
        self.assertIn("Agent Council", html)
        self.assertIn("Supervisor Final Review", html)

    def test_web_overview_payload_lists_agents_and_runtime_layers(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            overview = build_overview_payload(
                memory_path=root / "memory.json",
                runs_dir=root / "runs",
                vault_dir=root / "vault",
                reports_dir=root / "reports",
            )

        self.assertEqual(overview["app"], "JIMMORIA")
        self.assertTrue(any(item["id"] == "supervisor_agent" for item in overview["agents"]))
        self.assertTrue(any(item["id"] == "agent_council" for item in overview["workflow"]))
        self.assertTrue(any(item["label"] == "ToolGateway" for item in overview["infrastructure"]))

    def test_web_run_payload_reads_room_events_and_report_preview(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "room_web"
            run_dir.mkdir(parents=True)
            report_path = root / "reports" / "web-room.md"
            report_path.parent.mkdir()
            report_path.write_text("# Web Report\n\nAgent output.", encoding="utf-8")
            (run_dir / "room.json").write_text(
                json.dumps(
                    {
                        "room_id": "room_web",
                        "topic": "Web dashboard check",
                        "agents": ["supervisor_agent", "report_agent"],
                        "shared_findings": ["finding_1"],
                        "output_paths": {"report": str(report_path), "obsidian_vault": str(root / "vault")},
                        "status": "completed",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (run_dir / "events.json").write_text(
                json.dumps(
                    [
                        {"type": "agent_start", "agent_id": "supervisor_agent", "task_type": "supervision"},
                        {
                            "type": "agent_done",
                            "agent_id": "supervisor_agent",
                            "task_type": "supervision",
                            "summary": "Supervisor planned the room.",
                        },
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (run_dir / "messages.json").write_text("[]", encoding="utf-8")
            (run_dir / "tool_audit_log.json").write_text("[]", encoding="utf-8")
            (run_dir / "llm_call_log.json").write_text("[]", encoding="utf-8")

            payload = build_run_payload("room_web", runs_dir=root / "runs")

        self.assertEqual(payload["room"]["room_id"], "room_web")
        self.assertEqual(payload["counters"]["events"], 2)
        self.assertEqual(payload["events"][0]["seq"], 1)
        self.assertEqual(payload["events"][1]["seq"], 2)
        self.assertEqual(payload["event_cursor"]["last_seq"], 2)
        self.assertEqual(payload["agent_state"][0]["state"], "DONE")
        self.assertIn("Web Report", payload["report_preview"])

    def test_agent_skill_and_hook_references_are_registered(self) -> None:
        agent_registry = AgentSpecRegistry.load_dir("config/agents")
        skill_registry = SkillSpecRegistry.load_dir("config/skills")
        hook_registry = HookRegistry.load_dir("config/hooks")

        missing_skills: list[str] = []
        missing_hooks: list[str] = []
        for spec in agent_registry.specs.values():
            for skill_id in spec.skills.all():
                if skill_registry.get(skill_id) is None:
                    missing_skills.append(f"{spec.agent_id}:{skill_id}")
            for phase, hook_ids in spec.hooks.items():
                for hook_id in hook_ids:
                    if hook_registry.get(hook_id) is None:
                        missing_hooks.append(f"{spec.agent_id}:{phase}:{hook_id}")

        self.assertEqual([], missing_skills)
        self.assertEqual([], missing_hooks)

    def test_agent_system_prompt_includes_persona_scope_memory_and_hooks(self) -> None:
        spec = AgentSpecRegistry.load_dir("config/agents").get("social_kol_agent")
        self.assertIsNotNone(spec)
        prompt = spec.system_prompt()  # type: ignore[union-attr]

        self.assertIn("Persona: The Signal Listener", prompt)
        self.assertIn("Strengths to use:", prompt)
        self.assertIn("Biases to avoid:", prompt)
        self.assertIn("Memory access contract:", prompt)
        self.assertIn("Runtime hooks:", prompt)
        self.assertIn("Primary skills:", prompt)

    def test_safety_gate_blocks_investment_advice(self) -> None:
        result = review_report_quality("This is not advice but you should swap into it. https://example.com")

        self.assertFalse(result.passed)
        self.assertTrue(any(issue.issue_type == "investment_advice_language" for issue in result.issues))

    def test_report_requires_citations_or_unverified_label(self) -> None:
        without_label = review_report_quality("The token is live on Base and the team is funded.")
        with_label = review_report_quality("Unverified: the token may be live on Base and the team may be funded.")

        self.assertFalse(without_label.passed)
        self.assertTrue(any(issue.issue_type == "missing_citation" for issue in without_label.issues))
        self.assertTrue(with_label.passed)


if __name__ == "__main__":
    unittest.main()
