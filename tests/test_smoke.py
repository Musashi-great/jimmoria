from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from crypto_research_agents.runtime import ResearchRuntime
from crypto_research_agents.core.agent_spec import AgentSpecRegistry
from crypto_research_agents.cli import configure_model_panel, main as cli_main, print_banner
from crypto_research_agents.core.llm_provider import OAuthTokenProvider, provider_from_env
from crypto_research_agents.core.model_gateway import ModelGateway
from crypto_research_agents.core.capabilities import collect_capabilities


class SmokeTest(unittest.TestCase):
    def test_cli_banner_uses_jimmoria_brand(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            print_banner()

        text = output.getvalue()
        self.assertIn("JIMMORIA v0.1.0", text)
        self.assertIn("Multi-agent crypto research company", text)
        self.assertNotIn("Company roster", text)

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
            self.assertGreaterEqual(len(runtime.model_gateway.call_log), 3)
            self.assertGreaterEqual(len(result.bus.messages), 8)
            self.assertGreaterEqual(len(result.memory.get_room_findings(result.room.room_id)), 8)

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

    def test_model_setup_offline_choice_uses_screen_flow(self) -> None:
        output = StringIO()
        with patch.dict("os.environ", {}, clear=True):
            with patch("builtins.input", return_value="3"):
                with redirect_stdout(output):
                    configure_model_panel()
            self.assertEqual(os.environ["LLM_PROVIDER"], "offline")

        text = output.getvalue()
        self.assertIn("[Model Setup]", text)
        self.assertIn("[Offline fallback]", text)

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
        self.assertEqual(statuses["GitHub reader"], "placeholder")
        self.assertEqual(statuses["Overall"], "placeholder")

    def test_tool_registry_contains_required_research_stack(self) -> None:
        registry = json.loads(Path("config/tools/tool_registry.yaml").read_text(encoding="utf-8"))

        self.assertIn("x_search_posts", registry["minimum_viable_live_stack"])
        self.assertIn("rootdata_search_projects", registry["minimum_viable_live_stack"])
        self.assertIn("claim_evidence_check", registry["safety"])
        self.assertEqual(registry["tool_meta"]["x_search_posts"]["priority"], "required")
        self.assertEqual(registry["tool_meta"]["rootdata_get_project"]["owner_agent"], "funding_token_agent")

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
            self.assertIn("MVP scaffold runs, but live research connectors are not connected yet.", text)

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
            self.assertIn(("crawl_docs", "unconfigured"), statuses)
            self.assertIn(("check_airdrop_points", "unconfigured"), statuses)


if __name__ == "__main__":
    unittest.main()
