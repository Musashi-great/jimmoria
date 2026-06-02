from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from crypto_research_agents import APP_NAME, __version__
from crypto_research_agents.console import JimmoriaConsole
from crypto_research_agents.runtime import ResearchRuntime
from crypto_research_agents.runtime import DEFAULT_AGENTS
from crypto_research_agents.core.capabilities import collect_capabilities
from crypto_research_agents.storage.json_store import load_memory
from crypto_research_agents.storage.run_store import list_run_summaries, load_run_file


DEMO_TEXT = """
An article argues that AI agents will increasingly operate wallet workflows for users.
The core thesis combines agent automation, intent routing, consumer crypto, points programs,
and pre-token projects building on testnet. It also mentions that docs and GitHub activity
can reveal early infrastructure before broad KOL attention appears on X or Telegram.
"""

MODEL_ROUTE_TIERS = [
    ("FAST", "fast / cheap"),
    ("REASONING", "reasoning"),
    ("WRITING", "writing"),
    ("STRONG", "strong fallback"),
]
MODEL_SETTING_ENV_NAMES = [
    "LLM_PROVIDER",
    "CODEX_CLI_MODEL_FAST",
    "CODEX_CLI_MODEL_REASONING",
    "CODEX_CLI_MODEL_WRITING",
    "CODEX_CLI_MODEL_STRONG",
    "CODEX_OAUTH_MODEL_FAST",
    "CODEX_OAUTH_MODEL_REASONING",
    "CODEX_OAUTH_MODEL_WRITING",
    "CODEX_OAUTH_MODEL_STRONG",
    "OPENAI_MODEL_FAST",
    "OPENAI_MODEL_REASONING",
    "OPENAI_MODEL_WRITING",
    "OPENAI_MODEL_STRONG",
]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} multi-agent crypto research CLI.")
    subparsers = parser.add_subparsers(dest="command")

    demo_parser = subparsers.add_parser("demo", help="Run a built-in full research demo.")
    add_run_args(demo_parser)

    chat_parser = subparsers.add_parser("chat", help="Start an interactive CLI research console.")
    add_run_args(chat_parser)
    chat_parser.add_argument(
        "--skip-model-setup",
        action="store_true",
        help="Skip the startup model setup panel.",
    )

    hq_parser = subparsers.add_parser("hq", help="Alias for the chat-like JIMMORIA HQ console.")
    add_run_args(hq_parser)
    hq_parser.add_argument(
        "--skip-model-setup",
        action="store_true",
        help="Skip the startup model setup panel.",
    )

    research_parser = subparsers.add_parser("research", help="Run the full article/project research loop.")
    add_source_args(research_parser, title_required=False)
    add_run_args(research_parser)

    article_parser = subparsers.add_parser("article", help="Alias for research.")
    add_source_args(article_parser, title_required=True)
    add_run_args(article_parser)

    add_source_parser = subparsers.add_parser("add-source", help="Ingest a source and write an Obsidian Source Note.")
    add_source_args(add_source_parser, title_required=False)
    add_run_args(add_source_parser)

    runs_parser = subparsers.add_parser("runs", help="List previous research runs.")
    add_inspect_args(runs_parser)

    status_parser = subparsers.add_parser("status", help="Show a run room.json snapshot.")
    status_parser.add_argument("room_id")
    add_inspect_args(status_parser)

    messages_parser = subparsers.add_parser("messages", help="Show collaboration messages for a run.")
    messages_parser.add_argument("room_id")
    messages_parser.add_argument("--limit", type=int, default=20)
    add_inspect_args(messages_parser)

    events_parser = subparsers.add_parser("events", help="Show UI/replay events for a run.")
    events_parser.add_argument("room_id")
    events_parser.add_argument("--limit", type=int, default=30)
    add_inspect_args(events_parser)

    report_parser = subparsers.add_parser("show-report", help="Print the saved report markdown for a run.")
    report_parser.add_argument("room_id")
    add_inspect_args(report_parser)

    doctor_parser = subparsers.add_parser("doctor", help="Show configured and placeholder capabilities.")
    add_run_args(doctor_parser)

    args = parser.parse_args(argv)
    if args.command is None:
        if sys.stdin.isatty():
            chat_command(default_chat_args())
            return
        parser.print_help()
        return

    if args.command == "doctor":
        doctor_command(args)
        return

    if args.command in {"runs", "status", "messages", "events", "show-report"}:
        inspect_command(args)
        return

    if args.command in {"chat", "hq"}:
        chat_command(args)
        return

    memory = load_memory(args.memory)
    runtime = ResearchRuntime(memory)

    if args.command == "demo":
        result = runtime.run_article_research(
            title="AI Wallet Automation Early Projects",
            content=DEMO_TEXT,
            url=None,
            vault_dir=args.vault,
            reports_dir=args.reports,
            memory_path=args.memory,
        )
    else:
        title, content, url = read_source_input(args)
        if args.command == "add-source":
            result = runtime.run_source_ingestion(
                title=title,
                content=content,
                url=url,
                vault_dir=args.vault,
                memory_path=args.memory,
            )
        else:
            result = runtime.run_article_research(
                title=title,
                content=content,
                url=url,
                vault_dir=args.vault,
                reports_dir=args.reports,
                memory_path=args.memory,
            )

    print_run_result(result)


def add_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--vault", default="vault", help="Obsidian-style vault output directory.")
    parser.add_argument("--reports", default="reports", help="Report output directory.")
    parser.add_argument("--memory", default="data/memory.json", help="Shared memory JSON path.")


def default_chat_args() -> argparse.Namespace:
    return argparse.Namespace(
        command="chat",
        vault="vault",
        reports="reports",
        memory="data/memory.json",
        skip_model_setup=False,
    )


def add_inspect_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--runs-dir", default="data/runs", help="Run snapshot directory.")


def add_source_args(parser: argparse.ArgumentParser, *, title_required: bool) -> None:
    parser.add_argument("--title", required=title_required)
    parser.add_argument("--text")
    parser.add_argument("--file")
    parser.add_argument("--url")


def read_source_input(args: argparse.Namespace) -> tuple[str, str, str | None]:
    content = ""
    url = args.url
    if args.file:
        content = Path(args.file).read_text(encoding="utf-8")
    elif args.text:
        content = args.text
    elif args.url:
        content = fetch_url_text(args.url)
    else:
        raise SystemExit("Provide one of --url, --file, or --text.")

    title = args.title or infer_title(content, url)
    return title, content, url


def fetch_url_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": f"jimmoria-cli/{__version__}"})
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
    except URLError as exc:
        raise SystemExit(f"Failed to fetch URL: {exc}") from exc
    return raw.decode(charset, errors="replace")


def infer_title(content: str, url: str | None) -> str:
    for line in content.splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned[:80]
    if url:
        return url.rstrip("/").split("/")[-1] or url
    return "Untitled Research Source"


def inspect_command(args: argparse.Namespace) -> None:
    if args.command == "runs":
        summaries = list_run_summaries(args.runs_dir)
        if not summaries:
            print("No runs found.")
            return
        for item in summaries:
            print(f"{item['room_id']} | {item['status']} | {item['topic']} | report={item['report']}")
        return

    if args.command == "status":
        room = load_run_file(args.room_id, "room.json", args.runs_dir)
        print_room_status(room)
        return

    if args.command == "messages":
        messages = load_run_file(args.room_id, "messages.json", args.runs_dir)
        assert isinstance(messages, list)
        for message in messages[: args.limit]:
            print(
                f"{message.get('created_at')} | {message.get('type')} | "
                f"{message.get('from_agent')} -> {message.get('to_agent')} | "
                f"{message.get('task', {}).get('summary') or message.get('task', {}).get('objective')}"
            )
        return

    if args.command == "events":
        events = load_run_file(args.room_id, "events.json", args.runs_dir)
        assert isinstance(events, list)
        for event in events[: args.limit]:
            event_type = event.get("type", "")
            agent_id = event.get("agent_id", "")
            room_id = event.get("room_id", "")
            topic = event.get("topic", "")
            summary = event.get("summary", "")
            print(f"{event_type} | room={room_id} | agent={agent_id} | topic={topic} | {summary}")
        return

    if args.command == "show-report":
        room = load_run_file(args.room_id, "room.json", args.runs_dir)
        report_path = room.get("output_paths", {}).get("report")
        if not report_path:
            raise SystemExit("This run does not have a report output.")
        print(Path(report_path).read_text(encoding="utf-8"))
        return

    raise SystemExit(f"Unknown inspect command: {args.command}")


def chat_command(args: argparse.Namespace) -> None:
    apply_saved_model_settings()
    console = JimmoriaConsole(
        memory_path=args.memory,
        runs_dir=Path(args.memory).parent / "runs",
    )
    console.print_intro()
    if not args.skip_model_setup and sys.stdin.isatty() and not os.getenv("LLM_PROVIDER"):
        configure_model_panel(clear_before=False)
    console.print_help()
    last_room_id = ""

    while True:
        try:
            line = input(f"\n{APP_NAME.lower()}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            return

        if not line:
            continue

        if line.startswith("/"):
            should_quit, last_room_id = handle_chat_command(line, args, last_room_id, console)
            if should_quit:
                return
            continue

        console.print_user_message(line)
        title, content, url = chat_input_to_source(line)
        runtime = ResearchRuntime(load_memory(args.memory))
        runtime.event_handler = console.make_event_handler()
        result = runtime.run_article_research(
            title=title,
            content=content,
            url=url,
            vault_dir=args.vault,
            reports_dir=args.reports,
            memory_path=args.memory,
        )
        last_room_id = result.room.room_id
        console.last_room_id = last_room_id
        console.print_run_summary(result)


def handle_chat_command(
    line: str,
    args: argparse.Namespace,
    last_room_id: str,
    console: JimmoriaConsole,
) -> tuple[bool, str]:
    command, _, rest = line.partition(" ")
    command = command.lower()
    rest = rest.strip()

    if command in {"/quit", "/exit", "/q"}:
        print("bye")
        return True, last_room_id

    if command == "/help":
        console.print_help()
        return False, last_room_id

    if command == "/models":
        configure_model_panel()
        return False, last_room_id

    if command in {"/agents", "/company"}:
        console.print_company(active_only=False)
        return False, last_room_id

    if command == "/context":
        console.print_context()
        return False, last_room_id

    if command == "/last":
        console.print_latest_run_card(rest or None)
        return False, last_room_id

    if command == "/doctor":
        doctor_command(args)
        return False, last_room_id

    if command == "/runs":
        for item in list_run_summaries(Path(args.memory).parent / "runs"):
            print(f"{item['room_id']} | {item['status']} | {item['topic']} | report={item['report']}")
        return False, last_room_id

    if command == "/status":
        room_id = rest or last_room_id
        if not room_id:
            print("No room_id yet. Run a query first or pass /status <room_id>.")
            return False, last_room_id
        room = load_run_file(room_id, "room.json", Path(args.memory).parent / "runs")
        print_room_status(room)
        return False, room_id

    if command == "/messages":
        room_id = rest or last_room_id
        if not room_id:
            print("No room_id yet. Run a query first or pass /messages <room_id>.")
            return False, last_room_id
        messages = load_run_file(room_id, "messages.json", Path(args.memory).parent / "runs")
        assert isinstance(messages, list)
        print_message_rows(messages, limit=20)
        return False, room_id

    if command == "/events":
        room_id = rest or last_room_id
        if not room_id:
            print("No room_id yet. Run a query first or pass /events <room_id>.")
            return False, last_room_id
        events = load_run_file(room_id, "events.json", Path(args.memory).parent / "runs")
        assert isinstance(events, list)
        for event in events[:30]:
            print(
                f"{event.get('type')} | room={event.get('room_id', '')} | "
                f"agent={event.get('agent_id', '')} | {event.get('summary', '')}"
            )
        return False, room_id

    if command == "/report":
        room_id = rest or last_room_id
        if not room_id:
            print("No room_id yet. Run a query first or pass /report <room_id>.")
            return False, last_room_id
        room = load_run_file(room_id, "room.json", Path(args.memory).parent / "runs")
        report_path = room.get("output_paths", {}).get("report")
        if not report_path:
            print("This run does not have a report.")
            return False, room_id
        print(Path(report_path).read_text(encoding="utf-8"))
        return False, room_id

    if command == "/add":
        if not rest:
            print("Usage: /add <source text or URL>")
            return False, last_room_id
        title, content, url = chat_input_to_source(rest)
        runtime = ResearchRuntime(load_memory(args.memory))
        console.print_user_message(rest)
        runtime.event_handler = console.make_event_handler()
        result = runtime.run_source_ingestion(
            title=title,
            content=content,
            url=url,
            vault_dir=args.vault,
            memory_path=args.memory,
        )
        last_room_id = result.room.room_id
        console.last_room_id = last_room_id
        console.print_run_summary(result)
        return False, last_room_id

    print("Unknown command. Type /help.")
    return False, last_room_id


def chat_input_to_source(line: str) -> tuple[str, str, str | None]:
    if line.startswith("http://") or line.startswith("https://"):
        content = fetch_url_text(line)
        return infer_title(content, line), content, line
    return infer_title(line, None), line, None


def print_banner() -> None:
    JimmoriaConsole().print_intro()


def print_chat_help() -> None:
    JimmoriaConsole().print_help()


def print_agent_bar(agent_ids: list[str]) -> None:
    print("")
    print("Agents:")
    for index, agent_id in enumerate(agent_ids, start=1):
        print(f"  [{index:02d}] {agent_id}  enabled")


def doctor_command(args: argparse.Namespace | None = None) -> None:
    memory_path = getattr(args, "memory", "data/memory.json")
    runs_dir = Path(memory_path).parent / "runs"
    vault_dir = getattr(args, "vault", "vault")
    reports_dir = getattr(args, "reports", "reports")
    capabilities = collect_capabilities(
        memory_path=memory_path,
        runs_dir=runs_dir,
        vault_dir=vault_dir,
        reports_dir=reports_dir,
    )
    print("")
    print("Capability status:")
    for capability in capabilities:
        marker = {
            "configured": "OK",
            "fallback": "FB",
            "placeholder": "--",
            "missing": "!!",
        }.get(capability.status, "??")
        print(f"  {marker} {capability.name}: {capability.status} | {capability.detail}")


def print_agent_table(*, active_only: bool = False) -> None:
    from crypto_research_agents.core.agent_spec import AgentSpecRegistry

    registry = AgentSpecRegistry.load_dir("config/agents")
    agent_ids = DEFAULT_AGENTS if active_only else sorted(registry.specs)
    print("")
    print("Agents:")
    for index, agent_id in enumerate(agent_ids, start=1):
        spec = registry.get(agent_id)
        if spec is None:
            print(f"  [{index:02d}] {agent_id} | enabled")
            continue
        status = "enabled" if agent_id in DEFAULT_AGENTS else "planned"
        persona = spec.persona_name or "-"
        one_liner = spec.identity.one_liner or spec.role.description
        print(f"  [{index:02d}] {agent_id} | {persona} | {status}")
        print(f"       {one_liner}")


def configure_model_panel(*, clear_before: bool = True) -> None:
    apply_saved_model_settings()
    if clear_before:
        clear_screen()
    print_screen(
        "Model Setup",
        [
            f"Current provider: {os.getenv('LLM_PROVIDER') or 'offline_fallback'}",
            "",
            "1. Codex OAuth / ChatGPT login code",
            "2. OpenAI API Key",
            "3. Offline fallback",
            "Enter. Keep current",
        ]
    )
    choice = input("Choose provider [1/2/3/Enter]: ").strip().lower()
    if not choice:
        clear_screen()
        print_current_model_config()
        return

    if choice in {"1", "codex", "codex_oauth"}:
        configure_codex_oauth()
        print_current_model_config()
        return

    if choice in {"2", "openai"}:
        configure_openai()
        print_current_model_config()
        return

    if choice in {"3", "offline", "fallback"}:
        configure_offline()
        print_current_model_config()
        return

    print("Unknown provider choice. Keeping current configuration.")
    clear_screen()
    print_current_model_config()


def configure_codex_oauth() -> None:
    clear_openai_session_env()
    os.environ["LLM_PROVIDER"] = "codex_cli"
    clear_codex_token_session_env()
    clear_screen()
    print_screen(
        "Codex OAuth",
        [
            "Recommended: sign in with the Codex device code flow.",
            "This opens the same style of flow where ChatGPT shows a code login page.",
            "",
            "1. Start Codex device login",
            "2. Paste bearer token manually",
            "3. Token file path",
            "4. Command that prints bearer token",
            "Enter. Use existing Codex login",
        ],
    )
    source_choice = input("Choose Codex auth method [1/2/3/4/Enter]: ").strip().lower()

    if source_choice == "1":
        run_codex_device_login()
        os.environ["LLM_PROVIDER"] = "codex_cli"
    elif source_choice == "2":
        os.environ["LLM_PROVIDER"] = "codex_oauth"
        token = getpass.getpass("CODEX_OAUTH_TOKEN: ").strip()
        if token:
            os.environ["CODEX_OAUTH_TOKEN"] = token
    elif source_choice == "3":
        os.environ["LLM_PROVIDER"] = "codex_oauth"
        token_file = input("CODEX_OAUTH_TOKEN_FILE path: ").strip()
        if token_file:
            os.environ["CODEX_OAUTH_TOKEN_FILE"] = token_file
    elif source_choice == "4":
        os.environ["LLM_PROVIDER"] = "codex_oauth"
        token_command = input("CODEX_OAUTH_TOKEN_COMMAND: ").strip()
        if token_command:
            os.environ["CODEX_OAUTH_TOKEN_COMMAND"] = token_command

    configure_model_routes(prefix="CODEX_CLI_MODEL" if os.getenv("LLM_PROVIDER") == "codex_cli" else "CODEX_OAUTH_MODEL")
    save_model_settings()


def configure_openai() -> None:
    clear_codex_token_session_env()
    os.environ["LLM_PROVIDER"] = "openai"
    clear_screen()
    print_screen("OpenAI API Key", ["Paste an API key for this terminal session."])
    api_key = getpass.getpass("OPENAI_API_KEY: ").strip()
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key
    configure_model_routes(prefix="OPENAI_MODEL")
    save_model_settings()


def configure_offline() -> None:
    os.environ["LLM_PROVIDER"] = "offline"
    clear_codex_token_session_env()
    clear_openai_session_env()
    clear_screen()
    print_screen("Offline fallback", ["Live LLM calls disabled for this session."])
    save_model_settings()


def configure_model_routes(prefix: str) -> None:
    clear_screen()
    print_screen(
        "Model Routes",
        [
            f"Provider: {os.getenv('LLM_PROVIDER') or 'offline_fallback'}",
            "You do not need to know model names.",
            "Provider default uses whatever your account and CLI config support.",
            "",
            "1. Use provider default for every agent (Recommended)",
            "2. Keep current routes",
            "3. Advanced: one custom model for every route",
            "4. Advanced: custom model per route",
            "Enter. Use provider default",
        ],
    )
    choice = input("Choose model route [1/2/3/4/Enter]: ").strip().lower()
    if choice in {"", "1", "default", "recommended"}:
        clear_model_route_env(prefix)
        clear_screen()
        print_screen("Model Routes", ["Using provider default for every agent."])
        return

    if choice in {"2", "keep"}:
        clear_screen()
        print_screen("Model Routes", ["Keeping current model routes."])
        return

    if choice in {"3", "same", "one"}:
        clear_screen()
        print_screen(
            "Custom Model",
            [
                "Only use this if you already know the exact model id.",
                "Press Enter to fall back to provider default.",
            ],
        )
        model = input("Model id for every route: ").strip()
        clear_model_route_env(prefix)
        if model:
            for tier, _label in MODEL_ROUTE_TIERS:
                os.environ[f"{prefix}_{tier}"] = model
        return

    if choice in {"4", "advanced"}:
        configure_model_routes_advanced(prefix)
        return

    clear_screen()
    print_screen("Model Routes", ["Unknown choice. Keeping current model routes."])


def configure_model_routes_advanced(prefix: str) -> None:
    clear_screen()
    print_screen(
        "Advanced Routes",
        [
            "Only use exact model ids you know are available.",
            "Press Enter on each route to keep its current value.",
        ],
    )
    for tier, label in MODEL_ROUTE_TIERS:
        env_name = f"{prefix}_{tier}"
        value = input_with_default(f"{env_name} ({label})", os.getenv(env_name, ""))
        if value:
            os.environ[env_name] = value


def clear_model_route_env(prefix: str) -> None:
    for tier, _label in MODEL_ROUTE_TIERS:
        os.environ.pop(f"{prefix}_{tier}", None)


def input_with_default(label: str, default: str) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def clear_codex_token_session_env() -> None:
    for name in [
        "CODEX_OAUTH_TOKEN",
        "CODEX_OAUTH_TOKEN_FILE",
        "CODEX_OAUTH_TOKEN_COMMAND",
    ]:
        os.environ.pop(name, None)


def clear_openai_session_env() -> None:
    os.environ.pop("OPENAI_API_KEY", None)


def print_current_model_config() -> None:
    provider = os.getenv("LLM_PROVIDER") or "offline_fallback"
    if provider == "codex_cli":
        fast = os.getenv("CODEX_CLI_MODEL_FAST") or "<Codex default>"
        reasoning = os.getenv("CODEX_CLI_MODEL_REASONING") or os.getenv("CODEX_CLI_MODEL_STRONG") or "<Codex default>"
        writing = os.getenv("CODEX_CLI_MODEL_WRITING") or os.getenv("CODEX_CLI_MODEL_STRONG") or "<Codex default>"
        token_source = codex_login_status()
    elif provider == "codex_oauth":
        fast = os.getenv("CODEX_OAUTH_MODEL_FAST") or "<default>"
        reasoning = os.getenv("CODEX_OAUTH_MODEL_REASONING") or os.getenv("CODEX_OAUTH_MODEL_STRONG") or "<default>"
        writing = os.getenv("CODEX_OAUTH_MODEL_WRITING") or os.getenv("CODEX_OAUTH_MODEL_STRONG") or "<default>"
        token_source = configured_codex_token_source()
    elif provider == "openai":
        fast = os.getenv("OPENAI_MODEL_FAST") or "<default>"
        reasoning = os.getenv("OPENAI_MODEL_REASONING") or os.getenv("OPENAI_MODEL_STRONG") or "<default>"
        writing = os.getenv("OPENAI_MODEL_WRITING") or os.getenv("OPENAI_MODEL_STRONG") or "<default>"
        token_source = "OPENAI_API_KEY set" if os.getenv("OPENAI_API_KEY") else "OPENAI_API_KEY not set"
    else:
        fast = reasoning = writing = "offline_fallback"
        token_source = "not required"

    print_screen(
        "Model Config",
        [
            f"Provider: {provider}",
            f"Fast model: {fast}",
            f"Reasoning model: {reasoning}",
            f"Writing model: {writing}",
            f"Credential: {token_source}",
        ]
    )


def configured_codex_token_source() -> str:
    if os.getenv("CODEX_OAUTH_TOKEN"):
        return "CODEX_OAUTH_TOKEN set"
    if os.getenv("CODEX_OAUTH_TOKEN_FILE"):
        return f"file: {os.getenv('CODEX_OAUTH_TOKEN_FILE')}"
    if os.getenv("CODEX_OAUTH_TOKEN_COMMAND"):
        return "command configured"
    return "not set"


def model_settings_path() -> Path:
    return Path(os.getenv("JIMMORIA_MODEL_SETTINGS_PATH", "data/model_settings.json"))


def apply_saved_model_settings() -> None:
    path = model_settings_path()
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    for name in MODEL_SETTING_ENV_NAMES:
        value = data.get(name)
        if isinstance(value, str) and value and name not in os.environ:
            os.environ[name] = value


def save_model_settings() -> None:
    data = {
        name: os.environ[name]
        for name in MODEL_SETTING_ENV_NAMES
        if os.getenv(name)
    }
    path = model_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_codex_device_login() -> None:
    if shutil.which("codex") is None:
        print_screen(
            "Codex OAuth",
            [
                "Codex CLI was not found on PATH.",
                "Install or expose Codex first, then run /models again.",
            ],
        )
        return

    print("")
    print("Starting Codex device login. Follow the browser/code instructions shown by Codex.")
    subprocess.run(["codex", "login", "--device-auth"], check=False)


def codex_login_status() -> str:
    if shutil.which("codex") is None:
        return "Codex CLI not found"
    completed = subprocess.run(
        ["codex", "login", "status"],
        check=False,
        capture_output=True,
        text=True,
    )
    text = (completed.stdout or completed.stderr).strip()
    return text or "Codex login status unknown"


def clear_screen() -> None:
    if not sys.stdout.isatty():
        print("")
        return
    os.system("cls" if os.name == "nt" else "clear")


def print_screen(title: str, lines: list[str]) -> None:
    print("")
    print(f"[{title}]")
    for line in lines:
        if line:
            print(f"  {line}")
        else:
            print("")


def make_event_printer() -> object:
    state: dict[str, str] = {}

    def handle(event: dict[str, object]) -> None:
        event_type = str(event.get("type", ""))
        if event_type == "room_created":
            print("")
            print_box(
                [
                    f"Research Room: {event.get('room_id')}",
                    f"Topic: {event.get('topic')}",
                    f"Agents: {len(event.get('agents', []))}",
                ]
            )
            return

        if event_type == "agent_start":
            agent_id = str(event.get("agent_id"))
            state[agent_id] = "running"
            print(f"-> {agent_id} ({event.get('task_type')}) started")
            return

        if event_type == "agent_done":
            agent_id = str(event.get("agent_id"))
            state[agent_id] = "done"
            print(
                f"OK {agent_id} done | messages={event.get('messages')} "
                f"findings={event.get('findings')} | {event.get('summary')}"
            )
            return

        if event_type == "room_completed":
            print_box(
                [
                    f"Completed: {event.get('room_id')}",
                    f"Status: {event.get('status')}",
                    f"Messages: {event.get('messages')}",
                    f"Findings: {event.get('findings')}",
                ]
            )

    return handle


def print_box(lines: list[str]) -> None:
    width = max(len(line) for line in lines) + 4
    print("+" + "-" * width + "+")
    for line in lines:
        print("| " + line.ljust(width - 2) + " |")
    print("+" + "-" * width + "+")


def print_message_rows(messages: list[object], limit: int) -> None:
    for message in messages[:limit]:
        if not isinstance(message, dict):
            continue
        task = message.get("task") if isinstance(message.get("task"), dict) else {}
        assert isinstance(task, dict)
        text = task.get("summary") or task.get("objective") or ""
        print(
            f"{message.get('created_at')} | {message.get('type')} | "
            f"{message.get('from_agent')} -> {message.get('to_agent')} | {text}"
        )


def print_mapping(value: object, indent: int = 0) -> None:
    prefix = " " * indent
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                print(f"{prefix}{key}:")
                print_mapping(item, indent + 2)
            else:
                print(f"{prefix}{key}: {item}")
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict | list):
                print_mapping(item, indent + 2)
            else:
                print(f"{prefix}- {item}")
    else:
        print(f"{prefix}{value}")


def print_room_status(room: dict[str, object]) -> None:
    output_paths = room.get("output_paths") if isinstance(room.get("output_paths"), dict) else {}
    assert isinstance(output_paths, dict)
    print(f"room_id: {room.get('room_id', '')}")
    print(f"topic: {room.get('topic', '')}")
    print(f"status: {room.get('status', '')}")
    print(f"created_at: {room.get('created_at', '')}")
    print(f"sources: {len(room.get('source_inputs', []) or [])}")
    print(f"findings: {len(room.get('shared_findings', []) or [])}")
    print(f"report: {output_paths.get('report', '')}")
    print(f"vault: {output_paths.get('obsidian_vault', '')}")


def print_run_result(result: object) -> None:
    room = result.room
    memory = result.memory
    bus = result.bus
    report_path = room.output_paths.get("report", "")
    vault_path = room.output_paths.get("obsidian_vault", "")
    print(f"room_id: {room.room_id}")
    print(f"status: {room.status}")
    print(f"messages: {len(bus.messages)}")
    print(f"findings: {len(memory.get_room_findings(room.room_id))}")
    if report_path:
        print(f"report: {report_path}")
    if vault_path:
        print(f"vault: {vault_path}")


if __name__ == "__main__":
    main(sys.argv[1:])
