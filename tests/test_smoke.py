from pathlib import Path
from tempfile import TemporaryDirectory
import argparse
import json
import os
import re
import subprocess
import tomllib
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from crypto_research_agents.runtime import ResearchRuntime
from crypto_research_agents.agents.discovery import build_live_candidates, extract_project_query, should_live_discover
from crypto_research_agents.connectors import register_default_connectors
from crypto_research_agents.core.agent_spec import AgentSpecRegistry
from crypto_research_agents.cli import (
    apply_company_instruction,
    chat_command,
    classify_chat_input,
    configure_model_panel,
    main as cli_main,
    message_summary,
    print_banner,
)
from crypto_research_agents.console import JimmoriaConsole, print_jimmoria_logo
from crypto_research_agents.core.company_settings import CompanySettings
from crypto_research_agents.core.llm_provider import CodexCliProvider, LLMRequest, LLMResponse, OAuthTokenProvider, provider_from_env
from crypto_research_agents.core.memory import SharedMemory, SourceRecord
from crypto_research_agents.core.model_gateway import ModelGateway
from crypto_research_agents.core.supervisor_chat import generate_supervisor_chat_reply
from crypto_research_agents.core.supervisor_intake import decide_supervisor_intake
from crypto_research_agents.core.capabilities import collect_capabilities
from crypto_research_agents.core.tool_gateway import PolicyEngine, ToolGateway


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
        self.assertIn("ddgs>=9.14.0", pyproject["project"]["optional-dependencies"]["all"])
        self.assertIn("feedparser>=6.0.11", pyproject["project"]["optional-dependencies"]["all"])

    def test_chat_input_uses_boxed_prompt(self) -> None:
        output = StringIO()
        console = JimmoriaConsole()

        with patch("sys.stdin.isatty", return_value=True):
            with patch("builtins.input", return_value="/quit") as mocked_input:
                with redirect_stdout(output):
                    value = console.read_chat_input()

        self.assertEqual(value, "/quit")
        self.assertIn("+", output.getvalue())
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
        self.assertGreaterEqual(len(box_lines), 4)
        self.assertTrue(box_lines[0].startswith("+"))
        self.assertTrue(box_lines[1].endswith("|"))
        self.assertTrue(box_lines[2].endswith("|"))
        self.assertTrue(box_lines[3].startswith("+"))

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
        self.assertIn("Supervisor mode: company CEO / outsourcing intake", applied)

    def test_supervisor_intake_returns_output_modes(self) -> None:
        settings = CompanySettings(supervisor_mode="company_ceo")

        research = decide_supervisor_intake("pearl 프로젝트를 분석해봐", settings)
        config = decide_supervisor_intake("로그 출력 스타일을 바꿔봐", settings)
        status = decide_supervisor_intake("현재 회사 상태 보여줘", settings)

        self.assertTrue(research.needs_research_room)
        self.assertEqual(research.output_mode, "research_dossier")
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

    def test_runtime_records_supervisor_intake_decision(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = decide_supervisor_intake("pearl 프로젝트를 분석해봐").to_dict()
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

    def test_article_research_loop_writes_outputs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
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
            self.assertGreaterEqual(len(runtime.model_gateway.call_log), 3)
            self.assertGreaterEqual(len(result.bus.messages), 8)
            self.assertGreaterEqual(len(result.memory.get_room_findings(result.room.room_id)), 8)

            report = Path(result.room.output_paths["report"]).read_text(encoding="utf-8")
            self.assertIn("| Project | Origin | Source Backing |", report)
            self.assertIn("mvp_placeholder", report)
            self.assertIn("[MVP Placeholder]", report)
            self.assertIn("LLM provider: `offline_fallback`", report)
            self.assertIn("Live LLM: not configured", report)

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
            runtime = ResearchRuntime()
            result = runtime.run_article_research(
                title="AI Wallet Automation",
                content="AI agent wallet automation with points and testnet docs.",
                vault_dir=root / "vault",
                reports_dir=root / "reports",
                memory_path=root / "memory.json",
            )

            report = Path(result.room.output_paths["report"]).read_text(encoding="utf-8")
            self.assertIn("# 프로젝트 리서치 보고서", report)
            self.assertIn("## 2. 목표", report)
            self.assertIn("Report language: `ko`", report)

    def test_default_connectors_register_low_cost_research_stack(self) -> None:
        runtime = ResearchRuntime()

        self.assertIn("fetch_url", runtime.tool_gateway.registered_tools)
        self.assertIn("crawl_website", runtime.tool_gateway.registered_tools)
        self.assertIn("crawl_docs", runtime.tool_gateway.registered_tools)
        self.assertIn("web_search", runtime.tool_gateway.registered_tools)
        self.assertIn("github_search_repos", runtime.tool_gateway.registered_tools)
        self.assertIn("read_github_repo", runtime.tool_gateway.registered_tools)
        self.assertIn("dexscreener_search_pairs", runtime.tool_gateway.registered_tools)
        self.assertIn("coingecko_coin_metadata", runtime.tool_gateway.registered_tools)

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
        self.assertIn("oauth_tokens", social.memory_scope.no_access)
        self.assertEqual(social.persona_name, "The Signal Listener")
        self.assertIn("소셜/KOL", social.mission.primary_goal)

        supervisor = registry.get("supervisor_agent")
        self.assertIsNotNone(supervisor)
        assert supervisor is not None
        self.assertEqual(supervisor.persona_name, "The Company President")
        self.assertIn("company_settings", supervisor.memory_scope.write)

        product = registry.get("product_tech_agent")
        self.assertIsNotNone(product)
        assert product is not None
        self.assertIn("github_search_repos", product.tools.allow)

        funding = registry.get("funding_token_agent")
        self.assertIsNotNone(funding)
        assert funding is not None
        self.assertIn("rootdata_get_project", funding.tools.allow)

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

    def test_codex_oauth_provider_can_be_selected(self) -> None:
        with patch.dict(
            "os.environ",
            {"LLM_PROVIDER": "codex_oauth", "CODEX_OAUTH_TOKEN": "token-for-test"},
            clear=True,
        ):
            provider = provider_from_env()

        self.assertEqual(provider.provider_name, "codex_oauth")

    def test_codex_cli_provider_can_be_selected(self) -> None:
        with patch.dict("os.environ", {"LLM_PROVIDER": "codex_cli"}, clear=True):
            provider = provider_from_env()

        self.assertEqual(provider.provider_name, "codex_cli")

    def test_codex_cli_provider_uses_supported_exec_flags(self) -> None:
        help_text = """
Usage: codex exec [OPTIONS] [PROMPT]
  --ephemeral
  --skip-git-repo-check
  -s, --sandbox <SANDBOX_MODE>
  -o, --output-last-message <FILE>
  -m, --model <MODEL>
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
        )

        with patch("crypto_research_agents.core.llm_provider.subprocess.run", side_effect=fake_run):
            response = CodexCliProvider().complete(request)

        exec_command = commands[1]
        self.assertEqual(response.text, '{"summary": "ok"}')
        self.assertIn("--sandbox", exec_command)
        self.assertIn("--output-last-message", exec_command)
        self.assertIn("--model", exec_command)
        self.assertNotIn("--ask-for-approval", exec_command)
        self.assertEqual(exec_command[-1], "-")
        exec_kwargs = run_kwargs[1]
        self.assertIsInstance(exec_kwargs["input"], bytes)
        self.assertIn("pearl 크립토 프로젝트", exec_kwargs["input"].decode("utf-8"))
        self.assertIs(exec_kwargs["text"], False)

    def test_model_setup_offline_choice_uses_screen_flow(self) -> None:
        output = StringIO()
        with TemporaryDirectory() as tmp:
            settings_path = str(Path(tmp) / "model_settings.json")
            with patch.dict("os.environ", {"JIMMORIA_MODEL_SETTINGS_PATH": settings_path}, clear=True):
                with patch("builtins.input", return_value="3"):
                    with redirect_stdout(output):
                        configure_model_panel()
                self.assertEqual(os.environ["LLM_PROVIDER"], "offline")
                settings = json.loads(Path(settings_path).read_text(encoding="utf-8"))
                self.assertEqual(settings["LLM_PROVIDER"], "offline")

        text = output.getvalue()
        self.assertIn("[Model Setup]", text)
        self.assertIn("[Offline fallback]", text)

    def test_codex_setup_can_use_default_model_routes_without_model_names(self) -> None:
        output = StringIO()
        with TemporaryDirectory() as tmp:
            settings_path = str(Path(tmp) / "model_settings.json")
            env = {
                "JIMMORIA_MODEL_SETTINGS_PATH": settings_path,
                "CODEX_CLI_MODEL_FAST": "bad-manual-value",
            }
            with patch.dict("os.environ", env, clear=True):
                with patch("builtins.input", side_effect=["1", "", ""]):
                    with patch("crypto_research_agents.cli.codex_login_status", return_value="Logged in using ChatGPT"):
                        with redirect_stdout(output):
                            configure_model_panel()
                self.assertEqual(os.environ["LLM_PROVIDER"], "codex_cli")
                self.assertNotIn("CODEX_CLI_MODEL_FAST", os.environ)
                settings = json.loads(Path(settings_path).read_text(encoding="utf-8"))
                self.assertEqual(settings["LLM_PROVIDER"], "codex_cli")
                self.assertNotIn("CODEX_CLI_MODEL_FAST", settings)

        text = output.getvalue()
        self.assertIn("You do not need to know model names.", text)
        self.assertIn("Using provider default for every agent.", text)

    def test_chat_skips_startup_model_setup_when_provider_is_saved(self) -> None:
        output = StringIO()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings_path = root / "model_settings.json"
            settings_path.write_text('{"LLM_PROVIDER": "codex_cli"}', encoding="utf-8")
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
                    with patch("sys.stdin.isatty", return_value=True):
                        with patch("builtins.input", return_value="/quit"):
                            with patch("crypto_research_agents.cli.configure_model_panel") as setup_panel:
                                with redirect_stdout(output):
                                    chat_command(args)
                                self.assertFalse(setup_panel.called)
                self.assertEqual(os.environ["LLM_PROVIDER"], "codex_cli")
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
                self.assertEqual(settings["LLM_PROVIDER"], "codex_cli")

        self.assertIn("JIMMORIA v0.1.0", output.getvalue())

    def test_oauth_token_provider_reads_explicit_env(self) -> None:
        with patch.dict("os.environ", {"CODEX_OAUTH_TOKEN": "abc123"}, clear=True):
            token = OAuthTokenProvider().get_token()

        self.assertEqual(token, "abc123")

    def test_codex_model_env_has_priority(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "CODEX_CLI_MODEL_FAST": "codex-cli-fast",
                "CODEX_OAUTH_MODEL_FAST": "codex-oauth-fast",
                "OPENAI_MODEL_FAST": "openai-fast",
            },
            clear=True,
        ):
            gateway = ModelGateway(provider=None)

        self.assertEqual(gateway.default_model, "codex-cli-fast")

    def test_doctor_marks_live_connectors_as_placeholders(self) -> None:
        statuses = {item.name: item.status for item in collect_capabilities()}

        self.assertEqual(statuses["Runtime scaffold"], "configured")
        self.assertEqual(statuses["Agent specs/personas"], "configured")
        self.assertEqual(statuses["X/Twitter search"], "placeholder")
        self.assertEqual(statuses["RootData project directory"], "placeholder")
        self.assertEqual(statuses["Explorer contract lookup"], "placeholder")
        self.assertEqual(statuses["Docs crawler"], "configured")
        self.assertEqual(statuses["GitHub reader"], "configured")
        self.assertEqual(statuses["GitHub repo search"], "configured")
        self.assertEqual(statuses["CoinGecko metadata"], "configured")
        self.assertEqual(statuses["DEX Screener pair search"], "configured")
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

    def test_model_router_contains_supervisor_chat_route(self) -> None:
        router = json.loads(Path("config/models/model_router.yaml").read_text(encoding="utf-8"))

        self.assertEqual(router["routes"]["supervisor_chat"], "fast_model")

    def test_doctor_command_outputs_current_limitations(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = StringIO()
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
            self.assertIn("X/Twitter search: placeholder", text)
            self.assertIn("Core runtime and low-cost connectors run", text)

    def test_tool_audit_log_records_unconfigured_live_connectors(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
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

            self.assertIn(("x_search_posts", "unconfigured"), statuses)
            self.assertIn(("get_contract_address", "unconfigured"), statuses)
            self.assertIn(("crawl_docs", "missing_input"), statuses)
            self.assertIn(("check_airdrop_points", "unconfigured"), statuses)


if __name__ == "__main__":
    unittest.main()
