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
from crypto_research_agents.agents.discovery import build_live_candidates, extract_project_query, project_identity_hints, should_live_discover
from crypto_research_agents.agents.social_kol import (
    build_public_social_queries,
    build_who_said_what,
    extract_handles_from_social_results,
)
from crypto_research_agents.agents.report import assess_report_quality, build_claim_evidence_ledger, diligence_score
from crypto_research_agents.connectors import register_default_connectors
from crypto_research_agents.core.agent_spec import AgentSpecRegistry
from crypto_research_agents.cli import (
    apply_company_instruction,
    chat_command,
    classify_chat_input,
    configure_model_panel,
    find_saved_report_for_request,
    main as cli_main,
    message_summary,
    print_banner,
)
from crypto_research_agents.console import JimmoriaConsole, print_jimmoria_logo
from crypto_research_agents.core.company_settings import CompanySettings
from crypto_research_agents.core.concurrency import load_concurrency_policy
from crypto_research_agents.core.llm_provider import CodexCliProvider, CodexSdkProvider, LLMRequest, LLMResponse, grok_auth_status, parse_json_response, provider_from_env
from crypto_research_agents.core.memory import FindingRecord, ProjectCandidate, SharedMemory, SourceRecord
from crypto_research_agents.core.model_gateway import ModelGateway
from crypto_research_agents.core.process_spec import ProcessSpecRegistry, load_process_spec
from crypto_research_agents.core.project_profile import find_project_profile
from crypto_research_agents.core.dynamic_dispatch import DynamicCandidateDispatcher
from crypto_research_agents.core.edge_conditions import evaluate_edge_condition
from crypto_research_agents.core.quality_gate import review_report_quality
from crypto_research_agents.core.scheduler import CronRegistry
from crypto_research_agents.core.supervisor_chat import generate_supervisor_chat_reply
from crypto_research_agents.core.supervisor_intake import decide_supervisor_intake
from crypto_research_agents.core.capabilities import collect_capabilities
from crypto_research_agents.core.playbook import ResearchPlaybookRegistry
from crypto_research_agents.core.profile import WorkerProfileRegistry
from crypto_research_agents.core.tool_gateway import PolicyEngine, ToolGateway
from crypto_research_agents.core.workflow import LoopCounter
from crypto_research_agents.core.workflow_executor import WorkflowExecutor
from crypto_research_agents.core.workflow_loader import WorkflowSpecRegistry, load_workflow_spec
from crypto_research_agents.storage.artifact_store import ArtifactStore
from crypto_research_agents.storage.run_store import events_after_seq
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
        self.assertIn("Supervisor channel", output.getvalue())
        self.assertIn("@path/to/file", output.getvalue())
        self.assertEqual(mocked_input.call_args[0][0], "| > ")

    def test_chat_input_renders_closed_ansi_box(self) -> None:
        output = StringIO()
        console = JimmoriaConsole()
        console.width = 72

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
        self.assertIn("Supervisor channel", box_lines[1])
        self.assertTrue(box_lines[1].endswith("|"))
        self.assertTrue(box_lines[2].endswith("|"))
        self.assertTrue(box_lines[3].endswith("|"))
        self.assertTrue(box_lines[4].startswith("+"))
        self.assertIn("\033[5M", output.getvalue())

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
        self.assertIn("Supervisor channel", status)
        self.assertIn("room_12345...bcdef", status)
        self.assertIn("1 run/1 wait/1 done", status)

    def test_user_message_prints_compact_log_not_panel(self) -> None:
        output = StringIO()
        console = JimmoriaConsole()
        console.use_rich = False

        with redirect_stdout(output):
            console.print_user_message("안녕하세요")

        text = output.getvalue()
        self.assertIn("You > 안녕하세요", text)
        self.assertNotIn("[You]", text)

    def test_supervisor_working_prints_compact_log(self) -> None:
        output = StringIO()
        console = JimmoriaConsole()
        console.use_rich = False

        with redirect_stdout(output):
            console.print_supervisor_working("Reading and routing.")

        text = output.getvalue()
        self.assertIn("Supervisor > Reading and routing.", text)
        self.assertNotIn("[Supervisor]", text)

    def test_chat_help_does_not_show_static_agent_roster(self) -> None:
        output = StringIO()

        with redirect_stdout(output):
            JimmoriaConsole().print_help()

        text = output.getvalue()
        self.assertIn("JIMMORIA commands", text)
        self.assertIn("/settings", text)
        self.assertNotIn("Agents at work:", text)

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
        self.assertEqual(classify_chat_input("pearl 프로젝트에 대해서 리서치 보고서 만들어봐"), "research_request")
        self.assertEqual(classify_chat_input("보고서는 한글로 만들어봐 영어단어는 써도 돼"), "company_config")
        self.assertEqual(classify_chat_input("현재 회사 상태랑 설정 보여줘"), "company_status")
        self.assertEqual(classify_chat_input("이 링크는 소스만 저장해줘"), "source_ingestion")
        self.assertEqual(classify_chat_input("지금 보고서 작성은 한글 위주로 세팅된게 맞지?"), "supervisor_chat")
        self.assertEqual(classify_chat_input("안녕"), "supervisor_chat")

    def test_chat_intake_classifies_saved_report_request(self) -> None:
        self.assertEqual(classify_chat_input("3jane 관련 투자 보고서 만들어봐"), "research_request")
        self.assertEqual(classify_chat_input("3jane 보고서 만든거 보내봐 전체"), "report_retrieval")
        self.assertEqual(classify_chat_input("3jane 보고서 만들어봐"), "research_request")
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
        report = decide_supervisor_intake("pearl 프로젝트 리서치 보고서 작성해봐", settings)
        config = decide_supervisor_intake("로그 출력 스타일을 바꿔봐", settings)
        status = decide_supervisor_intake("현재 회사 상태 보여줘", settings)

        self.assertFalse(research.needs_research_room)
        self.assertEqual(research.output_mode, "supervisor_reply")
        self.assertEqual(research.action, "ask_report_confirmation")
        self.assertTrue(report.needs_research_room)
        self.assertEqual(report.output_mode, "research_dossier")
        self.assertFalse(config.needs_research_room)
        self.assertEqual(config.output_mode, "settings_update")
        self.assertFalse(status.needs_research_room)
        self.assertEqual(status.output_mode, "settings_panel")
        chat = decide_supervisor_intake("지금 보고서 작성은 한글 위주로 세팅된게 맞지?", settings)
        self.assertFalse(chat.needs_research_room)
        self.assertEqual(chat.output_mode, "supervisor_reply")

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
        self.assertIn("Supervisor", text)
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

        text = output.getvalue()
        self.assertIn("Supervisor", text)
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
        self.assertIn("Supervisor", text)
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
                    with patch("builtins.input", side_effect=["pearl 프로젝트 리서치 보고서 작성해봐", "n", "/quit"]):
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
        self.assertIn("보고서를 원하면", text)
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
                        side_effect=["3jane 보고서 들고와봐", "만들어보라는거잖아", "y", "/quit"],
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
                    with patch("builtins.input", side_effect=["3jane 관련 투자 보고서 만들어봐", "y", "/quit"]):
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

    def test_runtime_records_supervisor_intake_decision(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = decide_supervisor_intake("pearl 프로젝트 리서치 보고서 작성해봐").to_dict()
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
        self.assertIn("Now: Extracting source metadata", text)
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
        self.assertIn("Stopped: Failed: Codex CLI provider failed", text)

    def test_runtime_events_default_to_compact_stream(self) -> None:
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
        self.assertIn("Room > OPEN room_test", text)
        self.assertIn("Board > 2 wait/0 done", text)
        self.assertIn("Agent > RUN ingestion_agent", text)
        self.assertIn("Agent > DONE ingestion_agent", text)
        self.assertIn("time 1.2s", text)
        self.assertIn("llm 2", text)
        self.assertIn("calls / ~4.2k tokens", text)
        self.assertNotIn("JIMMORIA opens a Research Room", text)
        self.assertNotIn("Live agent board", text)

    def test_runtime_stream_keeps_input_dock_visible_during_room(self) -> None:
        output = StringIO()
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
        self.assertIn("Room running. Input returns when Supervisor finishes this room.", clean)
        self.assertIn("Now: ingestion_agent -> Extracting source metadata", clean)
        self.assertIn("Waiting: supervisor_agent", clean)
        self.assertIn("Live agent board - current work", clean)
        self.assertIn("STATE", clean)
        self.assertIn("CURRENT WORK", clean)
        self.assertIn("ingestion_agent", clean)
        self.assertIn("Now: Extracting source metadata", clean)
        self.assertIn("> working...", clean)
        self.assertIn("\033[12A\033[12M", output.getvalue())
        self.assertIn("\033[?25l", output.getvalue())
        self.assertIn("\033[5m\033[38;2;255;92;212m...", output.getvalue())
        self.assertEqual(console.runtime_dock_lines, 12)

    def test_runtime_dock_shows_full_agent_board_for_research_room(self) -> None:
        output = StringIO()
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
        self.assertIn("Now: supervisor_agent -> Planning direction", clean)
        self.assertIn("Waiting: ingestion_agent, social_kol_agent, narrative_agent, discovery_agent +5", clean)
        self.assertIn("STATE", clean)
        self.assertIn("supervisor_agent", clean)
        self.assertIn("ingestion_agent", clean)
        self.assertIn("social_kol_agent", clean)
        self.assertIn("contract_onchain_agent", clean)
        self.assertIn("obsidian_curator_agent", clean)
        self.assertIn("Now: Planning direction", clean)
        self.assertIn("Waiting: Syncing vault notes", clean)
        self.assertEqual(console.runtime_dock_lines, 20)

    def test_runtime_dock_updates_agent_work_from_tool_events(self) -> None:
        output = StringIO()
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
        self.assertIn("Live agent board - current work", clean)
        self.assertIn("discovery_agent", clean)
        self.assertIn("Now: Tool running: web_search - pearl crypto project", clean)
        self.assertEqual(console.agent_state["discovery_agent"], "running")

    def test_runtime_stream_clears_input_dock_when_room_finishes(self) -> None:
        output = StringIO()
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

        self.assertIn("Room", output.getvalue())
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

    def test_run_summary_prints_full_report_for_completed_research(self) -> None:
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
            {"label": "3Jane site", "url": "https://www.3jane.xyz/"},
            {"label": "docs intro", "url": "https://docs.3jane.xyz/introduction"},
            {"label": "official X", "url": "https://x.com/3janexyz"},
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

        supervisor = registry.get("supervisor_agent")
        self.assertIsNotNone(supervisor)
        assert supervisor is not None
        self.assertEqual(supervisor.persona_name, "The Company President")
        self.assertIn("company_settings", supervisor.memory_scope.write)
        self.assertIn("skill_view", supervisor.tools.allow)
        self.assertIn("multi_tool_use.parallel", supervisor.tools.allow)

        product = registry.get("product_tech_agent")
        self.assertIsNotNone(product)
        assert product is not None
        self.assertIn("github_search_repos", product.tools.allow)
        self.assertIn("search_files", product.tools.allow)
        self.assertIn("browser_console", product.tools.allow)

        report = registry.get("report_agent")
        self.assertIsNotNone(report)
        assert report is not None
        self.assertEqual(report.output_schema.type, "project_intelligence_report")
        self.assertIn("Korean project intelligence report", report.mission.primary_goal)
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
        self.assertEqual(research.process_type, "controlled_p2p_with_parallel_evidence_checks")
        self.assertEqual(research.execution_strategy["current_phase"], 2)
        self.assertEqual(research.execution_strategy["current_mode"], "bounded_parallel_agent_group")
        self.assertIn("phase_2", research.execution_strategy)
        self.assertEqual(research.agent_ids[0], "supervisor_agent")
        self.assertEqual(research.agent_ids[-1], "obsidian_curator_agent")
        self.assertEqual(len(research.tasks), 13)
        self.assertEqual(len(research.agent_ids), 10)
        self.assertEqual(research.agent_ids[2], "social_kol_agent")
        self.assertIn("market_signal_intake", {task.phase for task in research.tasks})
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

        self.assertEqual(policy.active.phase, 2)
        self.assertEqual(policy.active.mode, "bounded_parallel_agent_group")
        self.assertEqual(policy.active.max_parallel, 4)
        self.assertEqual(len(policy.phases), 4)
        phase_two = [phase for phase in policy.phases if phase.phase == 2][0]
        self.assertEqual(phase_two.status, "active")
        self.assertEqual(phase_two.mode, "bounded_parallel_agent_group")
        self.assertEqual(phase_two.max_parallel, 4)
        self.assertEqual(phase_two.parallel_groups[0].group_id, "evidence_checks")
        self.assertIn("social_kol_agent", phase_two.parallel_groups[0].agents)

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
        self.assertEqual(room_created["concurrency"]["active_phase"]["phase"], 2)
        self.assertEqual(room_created["concurrency"]["active_phase"]["mode"], "bounded_parallel_agent_group")

    def test_runtime_runs_evidence_agents_as_parallel_group(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict("os.environ", {**_offline_no_secret_env(), "JIMMORIA_SKIP_EXTERNAL_SEARCH": "1"}, clear=False):
                runtime = ResearchRuntime()
                result = runtime.run_article_research(
                    title="Parallel Evidence Check",
                    content="3Jane crypto credit project report",
                    vault_dir=root / "vault",
                    reports_dir=root / "reports",
                    memory_path=root / "memory.json",
                )

            event_types = [event["type"] for event in runtime.event_log]
            group_start = [event for event in runtime.event_log if event["type"] == "parallel_group_start"][0]
            group_done = [event for event in runtime.event_log if event["type"] == "parallel_group_done"][0]

        self.assertEqual(result.room.status, "completed")
        self.assertIn("parallel_group_start", event_types)
        self.assertIn("parallel_group_done", event_types)
        self.assertEqual(group_start["group_id"], "evidence_checks")
        self.assertEqual(group_start["max_parallel"], 4)
        self.assertEqual(set(group_start["agents"]), {
            "social_kol_agent",
            "contract_onchain_agent",
            "product_tech_agent",
            "funding_token_agent",
        })
        self.assertEqual(group_done["group_id"], "evidence_checks")

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
                        "Pearl crypto project diligence request.",
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
                        "AI wallet automation with docs and points.",
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

    def test_grok_provider_can_be_selected_with_bearer_token(self) -> None:
        with patch.dict("os.environ", {"LLM_PROVIDER": "grok", "XAI_API_KEY": "xai-test"}, clear=True):
            provider = provider_from_env()

        self.assertEqual(provider.provider_name, "grok")
        self.assertTrue(getattr(provider, "is_configured", False))

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

    def test_model_setup_offline_choice_uses_screen_flow(self) -> None:
        output = StringIO()
        with TemporaryDirectory() as tmp:
            settings_path = str(Path(tmp) / "model_settings.json")
            with patch.dict("os.environ", {"JIMMORIA_MODEL_SETTINGS_PATH": settings_path}, clear=True):
                with patch("builtins.input", return_value="4"):
                    with redirect_stdout(output):
                        configure_model_panel()
                self.assertEqual(os.environ["LLM_PROVIDER"], "offline")
                settings = json.loads(Path(settings_path).read_text(encoding="utf-8"))
                self.assertEqual(settings["LLM_PROVIDER"], "offline")

        text = output.getvalue()
        self.assertIn("[Model Setup]", text)
        self.assertIn("[Offline diagnostic fallback]", text)

    def test_model_setup_grok_choice_saves_provider_without_raw_token(self) -> None:
        output = StringIO()
        with TemporaryDirectory() as tmp:
            settings_path = str(Path(tmp) / "model_settings.json")
            with patch.dict(
                "os.environ",
                {"JIMMORIA_MODEL_SETTINGS_PATH": settings_path, "GROK_OAUTH_TOKEN": "session-secret"},
                clear=True,
            ):
                with patch("builtins.input", side_effect=["3", "3", ""]):
                    with redirect_stdout(output):
                        configure_model_panel()
                self.assertEqual(os.environ["LLM_PROVIDER"], "grok")
                settings = json.loads(Path(settings_path).read_text(encoding="utf-8"))
                self.assertEqual(settings["LLM_PROVIDER"], "grok")
                self.assertNotIn("GROK_OAUTH_TOKEN", settings)

        text = output.getvalue()
        self.assertIn("[Grok / xAI]", text)
        self.assertIn("Supported models are fixed to the Grok/xAI model list.", text)

    def test_model_setup_grok_oauth_choice_uses_hermes_session_without_raw_token(self) -> None:
        output = StringIO()
        with TemporaryDirectory() as tmp:
            settings_path = str(Path(tmp) / "model_settings.json")
            with patch.dict("os.environ", {"JIMMORIA_MODEL_SETTINGS_PATH": settings_path}, clear=True):
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

    def test_codex_setup_can_use_default_model_routes_without_model_names(self) -> None:
        output = StringIO()
        with TemporaryDirectory() as tmp:
            settings_path = str(Path(tmp) / "model_settings.json")
            env = {
                "JIMMORIA_MODEL_SETTINGS_PATH": settings_path,
                "CODEX_MODEL_FAST": "bad-manual-value",
            }
            with patch.dict("os.environ", env, clear=True):
                with patch("builtins.input", side_effect=["1", "", ""]):
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

        self.assertEqual(router["provider"], "codex_or_grok")
        self.assertIn("gpt-5.5", router["supported_models"]["codex"])
        self.assertIn("grok-4.3", router["supported_models"]["grok"])
        self.assertIn("codex_sdk", router["runtime_order"])
        self.assertIn("xai_oauth", router["runtime_order"])
        self.assertIn("grok", router["runtime_order"])
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
