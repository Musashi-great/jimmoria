from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from crypto_research_agents import APP_NAME, __version__
from crypto_research_agents.connectors import register_default_connectors
from crypto_research_agents.console import JimmoriaConsole
from crypto_research_agents.runtime import ResearchRuntime
from crypto_research_agents.runtime import DEFAULT_AGENTS
from crypto_research_agents.core.capabilities import collect_capabilities
from crypto_research_agents.core.company_settings import (
    CompanySettings,
    company_settings_path_for,
    load_company_settings,
    save_company_settings,
)
from crypto_research_agents.core.supervisor_intake import decide_supervisor_intake
from crypto_research_agents.core.supervisor_intake import (
    build_company_instruction_reply,
    build_company_status_reply,
    build_supervisor_dispatch_reply,
)
from crypto_research_agents.core.supervisor_chat import generate_supervisor_chat_reply
from crypto_research_agents.core.tool_gateway import PolicyEngine, ToolGateway
from crypto_research_agents.core.playbook import ResearchPlaybookRegistry
from crypto_research_agents.core.profile import WorkerProfileRegistry
from crypto_research_agents.core.scheduler import CronRegistry, create_local_job
from crypto_research_agents.core.workflow_executor import WorkflowExecutor
from crypto_research_agents.core.workflow_loader import (
    WorkflowSpecRegistry,
    load_workflow_spec,
    workflow_spec_to_json,
    workflow_summary,
)
from crypto_research_agents.storage.artifact_store import ArtifactStore
from crypto_research_agents.storage.json_store import load_memory
from crypto_research_agents.storage.paths import default_project_path, resolve_project_path
from crypto_research_agents.storage.run_store import list_run_summaries, load_run_file
from crypto_research_agents.storage.session_store import search_sessions
from crypto_research_agents.tools.registry import load_tool_registry


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
    chat_parser.add_argument(
        "--verbose-board",
        action="store_true",
        help="Reserved for fuller agent-board logging in future visual modes.",
    )

    hq_parser = subparsers.add_parser("hq", help="Alias for the chat-like JIMMORIA HQ console.")
    add_run_args(hq_parser)
    hq_parser.add_argument(
        "--skip-model-setup",
        action="store_true",
        help="Skip the startup model setup panel.",
    )
    hq_parser.add_argument(
        "--verbose-board",
        action="store_true",
        help="Reserved for fuller agent-board logging in future visual modes.",
    )

    research_parser = subparsers.add_parser("research", help="Run the full article/project research loop.")
    add_source_args(research_parser, title_required=False)
    add_run_args(research_parser)
    research_parser.add_argument("--workflow", help="Archive this run with a workflow definition.")
    research_parser.add_argument("--json", action="store_true", help="Print a JSON result.")

    article_parser = subparsers.add_parser("article", help="Alias for research.")
    add_source_args(article_parser, title_required=True)
    add_run_args(article_parser)
    article_parser.add_argument("--workflow", help="Archive this run with a workflow definition.")
    article_parser.add_argument("--json", action="store_true", help="Print a JSON result.")

    add_source_parser = subparsers.add_parser("add-source", help="Ingest a source and write an Obsidian Source Note.")
    add_source_args(add_source_parser, title_required=False)
    add_run_args(add_source_parser)

    runs_parser = subparsers.add_parser("runs", help="List previous research runs.")
    add_inspect_args(runs_parser)

    rooms_parser = subparsers.add_parser("rooms", help="Show the multi-room workload board.")
    rooms_parser.add_argument("--limit", type=int, default=8)
    add_inspect_args(rooms_parser)

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

    workflow_parser = subparsers.add_parser("workflow", help="Inspect and run YAML company workflows.")
    workflow_subparsers = workflow_parser.add_subparsers(dest="workflow_command")

    workflow_list_parser = workflow_subparsers.add_parser("list", help="List available workflows.")
    workflow_list_parser.add_argument("--workflow-dir", default=default_project_path("config/workflows"))

    workflow_show_parser = workflow_subparsers.add_parser("show", help="Show a workflow JSON view.")
    workflow_show_parser.add_argument("workflow_id")
    workflow_show_parser.add_argument("--workflow-dir", default=default_project_path("config/workflows"))

    workflow_run_parser = workflow_subparsers.add_parser("run", help="Run a workflow-backed research room.")
    workflow_run_parser.add_argument("workflow_id")
    workflow_run_parser.add_argument("--workflow-dir", default=default_project_path("config/workflows"))
    add_source_args(workflow_run_parser, title_required=False)
    add_run_args(workflow_run_parser)
    workflow_run_parser.add_argument("--json", action="store_true", help="Print a JSON result.")

    workflow_events_parser = workflow_subparsers.add_parser("events", help="Show workflow event JSONL for a run.")
    workflow_events_parser.add_argument("run_id")
    workflow_events_parser.add_argument("--tail", action="store_true")
    workflow_events_parser.add_argument("--limit", type=int, default=30)
    workflow_events_parser.add_argument("--runs-dir", default=default_project_path("data/runs"))

    tools_parser = subparsers.add_parser("tools", help="Inspect JIMMORIA tool registry and toolsets.")
    tools_subparsers = tools_parser.add_subparsers(dest="tools_command")
    tools_list_parser = tools_subparsers.add_parser("list", help="List registered tools.")
    tools_list_parser.add_argument("--toolset", help="Limit output to one toolset.")
    tools_list_parser.add_argument("--json", action="store_true")

    cron_parser = subparsers.add_parser("cron", help="Inspect and run scheduled research jobs.")
    cron_subparsers = cron_parser.add_subparsers(dest="cron_command")
    cron_subparsers.add_parser("list", help="List scheduled jobs.")
    cron_subparsers.add_parser("status", help="Show scheduler status.")
    cron_run_parser = cron_subparsers.add_parser("run", help="Run a scheduled job once.")
    cron_run_parser.add_argument("job_id")
    cron_run_parser.add_argument("--signal", help="Optional JSON signal payload.")
    cron_run_parser.add_argument("--json", action="store_true")
    cron_create_parser = cron_subparsers.add_parser("create", help="Create a local scheduled job definition.")
    cron_create_parser.add_argument("job_id")
    cron_create_parser.add_argument("--schedule", required=True)
    cron_create_parser.add_argument("--workflow", required=True)
    cron_create_parser.add_argument("--output", default="local")
    cron_create_parser.add_argument("--profile", default="researcher")

    profile_parser = subparsers.add_parser("profile", help="Inspect worker profiles.")
    profile_subparsers = profile_parser.add_subparsers(dest="profile_command")
    profile_subparsers.add_parser("list", help="List worker profiles.")

    playbook_parser = subparsers.add_parser("playbook", help="Inspect research playbooks.")
    playbook_subparsers = playbook_parser.add_subparsers(dest="playbook_command")
    playbook_subparsers.add_parser("list", help="List research playbooks.")

    sessions_parser = subparsers.add_parser("sessions", help="Search archived research sessions.")
    sessions_subparsers = sessions_parser.add_subparsers(dest="sessions_command")
    sessions_search_parser = sessions_subparsers.add_parser("search", help="Search sessions by project, ticker, contract, or URL.")
    sessions_search_parser.add_argument("query")
    sessions_search_parser.add_argument("--runs-dir", default=default_project_path("data/runs"))
    sessions_search_parser.add_argument("--json", action="store_true")

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

    if args.command == "workflow":
        workflow_command(args)
        return

    if args.command == "tools":
        tools_command(args)
        return

    if args.command == "cron":
        cron_command(args)
        return

    if args.command == "profile":
        profile_command(args)
        return

    if args.command == "playbook":
        playbook_command(args)
        return

    if args.command == "sessions":
        sessions_command(args)
        return

    if args.command in {"runs", "rooms", "status", "messages", "events", "show-report"}:
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
            workflow_id = getattr(args, "workflow", None)
            if workflow_id:
                artifact_dir = archive_workflow_for_result(
                    workflow_id=workflow_id,
                    result=result,
                    runtime=runtime,
                    memory_path=args.memory,
                    workflow_dir=default_project_path("config/workflows"),
                    context={
                        "sources": result.room.source_inputs,
                        "findings": result.room.shared_findings,
                    },
                )
                if getattr(args, "json", False):
                    print(
                        json.dumps(
                            {
                                "workflow_id": workflow_id,
                                "room_id": result.room.room_id,
                                "status": result.room.status,
                                "artifact_dir": str(artifact_dir),
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
                    return

    print_run_result(result)


def add_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--vault", default=default_project_path("vault"), help="Obsidian-style vault output directory.")
    parser.add_argument("--reports", default=default_project_path("reports"), help="Report output directory.")
    parser.add_argument("--memory", default=default_project_path("data/memory.json"), help="Shared memory JSON path.")


def default_chat_args() -> argparse.Namespace:
    return argparse.Namespace(
        command="chat",
        vault=default_project_path("vault"),
        reports=default_project_path("reports"),
        memory=default_project_path("data/memory.json"),
        skip_model_setup=False,
        verbose_board=False,
    )


def add_inspect_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--runs-dir", default=default_project_path("data/runs"), help="Run snapshot directory.")


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
    policy = PolicyEngine()
    policy.allow("cli", "fetch_url")
    gateway = ToolGateway(policy)
    register_default_connectors(gateway)
    result = gateway.call("cli", "fetch_url", url=url)
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    if result.get("status") != "success":
        raise SystemExit(f"Failed to fetch URL: {result.get('message')}")
    return str(data.get("text") or "")


def infer_title(content: str, url: str | None) -> str:
    for line in content.splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned[:80]
    if url:
        return url.rstrip("/").split("/")[-1] or url
    return "Untitled Research Source"


def workflow_command(args: argparse.Namespace) -> None:
    if not args.workflow_command:
        raise SystemExit("Provide a workflow command: list, show, run, or events.")

    if args.workflow_command == "list":
        registry = WorkflowSpecRegistry.load_dir(args.workflow_dir)
        if not registry.specs:
            print("No workflows found.")
            return
        for spec in registry.specs.values():
            summary = workflow_summary(spec)
            print(
                f"{summary['workflow_id']} | nodes={summary['nodes']} "
                f"edges={summary['edges']} dynamic={summary['dynamic_edges']} | {summary['description']}"
            )
        return

    if args.workflow_command == "show":
        spec = load_workflow_spec(args.workflow_id, args.workflow_dir)
        print(workflow_spec_to_json(spec))
        return

    if args.workflow_command == "events":
        events_path = Path(args.runs_dir) / args.run_id / "events.jsonl"
        if not events_path.exists():
            fallback = Path(args.runs_dir) / args.run_id / "events.json"
            if not fallback.exists():
                raise SystemExit(f"No workflow events found for run: {args.run_id}")
            events = json.loads(fallback.read_text(encoding="utf-8"))
            selected = events[-args.limit :] if args.tail else events[: args.limit]
            for event in selected:
                print(json.dumps(event, ensure_ascii=False))
            return
        lines = events_path.read_text(encoding="utf-8").splitlines()
        selected_lines = lines[-args.limit :] if args.tail else lines[: args.limit]
        for line in selected_lines:
            print(line)
        return

    if args.workflow_command == "run":
        spec = load_workflow_spec(args.workflow_id, args.workflow_dir)
        title, content, url = read_source_input(args)
        runtime = ResearchRuntime(load_memory(args.memory))
        result = runtime.run_article_research(
            title=title,
            content=content,
            url=url,
            vault_dir=args.vault,
            reports_dir=args.reports,
            memory_path=args.memory,
        )
        artifact_dir = archive_workflow_for_result(
            workflow_id=spec.workflow_id,
            result=result,
            runtime=runtime,
            memory_path=args.memory,
            workflow_dir=args.workflow_dir,
            context=workflow_context_from_result(result),
        )
        payload = {
            "workflow_id": spec.workflow_id,
            "room_id": result.room.room_id,
            "status": result.room.status,
            "quality": result.room.project_card.get("research_quality", {}),
            "artifact_dir": str(artifact_dir),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"workflow_id: {payload['workflow_id']}")
            print(f"room_id: {payload['room_id']}")
            print(f"status: {payload['status']}")
            print(f"artifact_dir: {payload['artifact_dir']}")
        return

    raise SystemExit(f"Unknown workflow command: {args.workflow_command}")


def tools_command(args: argparse.Namespace) -> None:
    if not args.tools_command:
        raise SystemExit("Provide a tools command: list.")
    registry = load_tool_registry()
    if args.tools_command == "list":
        tool_ids: list[str]
        if args.toolset:
            toolset = registry.toolset(args.toolset)
            if toolset is None:
                raise SystemExit(f"Unknown toolset: {args.toolset}")
            tool_ids = toolset.tools
        else:
            tool_ids = sorted(registry.definitions)
        payload = []
        for tool_id in tool_ids:
            definition = registry.get(tool_id)
            if definition is None:
                continue
            availability = registry.availability(tool_id)
            payload.append({**definition.to_dict(), "availability": availability.status})
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return
        for item in payload:
            print(
                f"{item['tool_id']} | {item['mode']} | {item['implementation_status']} | "
                f"{item['availability']} | {item['description']}"
            )
        return
    raise SystemExit(f"Unknown tools command: {args.tools_command}")


def cron_command(args: argparse.Namespace) -> None:
    if not args.cron_command:
        raise SystemExit("Provide a cron command: list, status, run, or create.")
    registry = CronRegistry.load()
    if args.cron_command == "list":
        for job in registry.list_jobs():
            state = "enabled" if job.enabled else "disabled"
            print(f"{job.job_id} | {state} | {job.schedule} | {job.workflow_id} | {job.output}")
        return
    if args.cron_command == "status":
        print(f"jobs: {len(registry.jobs)}")
        print("no_signal_policy: silent by default")
        return
    if args.cron_command == "run":
        signal = None
        if args.signal:
            try:
                signal = json.loads(args.signal)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"--signal must be JSON: {exc}") from exc
        result = registry.run_job(args.job_id, signal=signal)
        if args.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            return
        if result.output:
            print(result.output)
        elif result.status != "no_signal":
            print(f"{result.job_id}: {result.status} | {result.detail}")
        return
    if args.cron_command == "create":
        path = create_local_job(
            job_id=args.job_id,
            schedule=args.schedule,
            workflow_id=args.workflow,
            output=args.output,
            profile=args.profile,
        )
        print(f"created: {args.job_id} -> {path}")
        return
    raise SystemExit(f"Unknown cron command: {args.cron_command}")


def profile_command(args: argparse.Namespace) -> None:
    if not args.profile_command:
        raise SystemExit("Provide a profile command: list.")
    registry = WorkerProfileRegistry.load()
    tool_registry = load_tool_registry()
    if args.profile_command == "list":
        for profile in registry.list_profiles():
            allowed_count = len(profile.allowed_tools(tool_registry))
            print(
                f"{profile.profile_id} | tools={allowed_count} | "
                f"output={profile.output_destination} | {profile.role}"
            )
        return
    raise SystemExit(f"Unknown profile command: {args.profile_command}")


def playbook_command(args: argparse.Namespace) -> None:
    if not args.playbook_command:
        raise SystemExit("Provide a playbook command: list.")
    registry = ResearchPlaybookRegistry.load_dir()
    if args.playbook_command == "list":
        for playbook in sorted(registry.playbooks.values(), key=lambda item: item.playbook_id):
            print(f"{playbook.playbook_id} | {playbook.title}")
        return
    raise SystemExit(f"Unknown playbook command: {args.playbook_command}")


def sessions_command(args: argparse.Namespace) -> None:
    if not args.sessions_command:
        raise SystemExit("Provide a sessions command: search.")
    if args.sessions_command == "search":
        results = search_sessions(args.query, runs_dir=args.runs_dir)
        if args.json:
            print(json.dumps([item.to_dict() for item in results], ensure_ascii=False, indent=2))
            return
        if not results:
            print("No sessions found.")
            return
        for item in results:
            print(f"{item.room_id} | {item.matched_file} | {item.snippet}")
        return
    raise SystemExit(f"Unknown sessions command: {args.sessions_command}")


def workflow_context_from_result(result: object) -> dict[str, object]:
    room = result.room
    memory = result.memory
    candidates = []
    for project in memory.projects.values():
        if set(project.sources).intersection(room.source_inputs):
            candidates.append(
                {
                    "project": project.name,
                    "website": project.website,
                    "chain": project.chain,
                    "token_status": project.token_status,
                    "candidate_origin": project.metadata.get("candidate_origin"),
                }
            )
    findings = [finding.to_dict() for finding in memory.get_room_findings(room.room_id)]
    return {
        "run_id": room.room_id,
        "sources": room.source_inputs,
        "findings": findings,
        "candidates": candidates,
        "quality": room.project_card.get("research_quality", {}),
    }


def archive_workflow_for_result(
    *,
    workflow_id: str,
    result: object,
    runtime: ResearchRuntime,
    memory_path: str | Path,
    workflow_dir: str | Path,
    context: dict[str, object] | None = None,
) -> Path:
    workflow = load_workflow_spec(workflow_id, workflow_dir)
    execution = WorkflowExecutor().execute(workflow, context or workflow_context_from_result(result))
    return ArtifactStore(Path(memory_path).parent / "runs").archive_workflow_run(
        result=result,
        workflow=workflow,
        workflow_trace=execution.trace,
        event_log=runtime.event_log,
        tool_audit_log=runtime.tool_gateway.audit_log,
        input_payload={
            "workflow_id": workflow_id,
            "room_id": result.room.room_id,
            "topic": result.room.topic,
            "context": context or workflow_context_from_result(result),
        },
    )


def inspect_command(args: argparse.Namespace) -> None:
    if args.command == "runs":
        summaries = list_run_summaries(args.runs_dir)
        if not summaries:
            print("No runs found.")
            return
        for item in summaries:
            print(f"{item['room_id']} | {item['status']} | {item['topic']} | report={item['report']}")
        return

    if args.command == "rooms":
        JimmoriaConsole(runs_dir=args.runs_dir).print_workboard(limit=args.limit)
        return

    if args.command == "status":
        room = load_run_file(args.room_id, "room.json", args.runs_dir)
        print_room_status(room)
        return

    if args.command == "messages":
        messages = load_run_file(args.room_id, "messages.json", args.runs_dir)
        assert isinstance(messages, list)
        print_message_rows(messages, limit=args.limit)
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
    auto_configure_codex_cli_if_logged_in()
    console = JimmoriaConsole(
        memory_path=args.memory,
        runs_dir=Path(args.memory).parent / "runs",
    )
    console.print_intro()
    if not args.skip_model_setup and sys.stdin.isatty() and not os.getenv("LLM_PROVIDER"):
        configure_model_panel(clear_before=False)
    console.print_help()
    last_room_id = ""
    supervisor_history: list[dict[str, str]] = []

    while True:
        try:
            line = console.read_chat_input().strip()
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
        console.print_supervisor_working("Reading the message, choosing the response shape, and routing the company.")
        settings_path = company_settings_path_for(args.memory)
        settings = load_company_settings(settings_path)
        intake_decision = decide_supervisor_intake(line, settings)

        if intake_decision.intent_type == "company_config":
            applied = apply_company_instruction(line, settings)
            save_company_settings(settings, settings_path)
            reply = build_company_instruction_reply(applied, settings, settings_path)
            console.print_supervisor_reply(reply)
            append_supervisor_history(supervisor_history, line, reply)
            continue

        if intake_decision.intent_type == "company_status":
            reply = build_company_status_reply(settings)
            console.print_supervisor_reply(reply)
            console.print_company_settings(settings, settings_path)
            append_supervisor_history(supervisor_history, line, reply)
            continue

        if intake_decision.intent_type == "supervisor_chat":
            reply = generate_supervisor_chat_reply(line, settings, intake_decision, history=supervisor_history)
            console.print_supervisor_reply(reply)
            append_supervisor_history(supervisor_history, line, reply)
            continue

        if intake_decision.intent_type == "report_retrieval":
            report = find_saved_report_for_request(
                line,
                runs_dir=Path(args.memory).parent / "runs",
                reports_dir=args.reports,
                last_room_id=last_room_id,
            )
            if report is None:
                reply = [
                    "저장된 보고서를 찾지 못했습니다.",
                    "프로젝트 이름이나 room_id를 조금 더 정확히 주거나, /runs로 목록을 먼저 확인해 주세요.",
                ]
                console.print_supervisor_reply(reply)
                append_supervisor_history(supervisor_history, line, reply)
                continue
            report_path, room_id, topic = report
            reply = [
                "찾았습니다. 새 Research Room은 열지 않고 저장된 보고서를 그대로 보여드리겠습니다.",
                f"Room: {room_id}",
                f"Topic: {topic}",
                f"Report: {report_path}",
            ]
            console.print_supervisor_reply(reply)
            console.block("Saved report", report_path.read_text(encoding="utf-8").splitlines())
            last_room_id = room_id
            console.last_room_id = last_room_id
            append_supervisor_history(supervisor_history, line, reply)
            continue

        title, content, url = chat_input_to_source(line)
        agent_count = 3 if intake_decision.intent_type == "source_ingestion" else len(DEFAULT_AGENTS)
        if not console.confirm_dispatch(
            intent_type=intake_decision.intent_type,
            title=title,
            agent_count=agent_count,
        ):
            reply = [
                "좋습니다. Research Room은 열지 않겠습니다.",
                "문장을 고쳐서 다시 지시하거나, 기존 보고서 조회라면 '보고서 보여줘'처럼 말해 주세요.",
            ]
            console.print_supervisor_reply(reply)
            append_supervisor_history(supervisor_history, line, reply)
            continue

        runtime = ResearchRuntime(load_memory(args.memory))
        runtime.event_handler = console.make_event_handler()
        reply = build_supervisor_dispatch_reply(intake_decision, agent_count)
        console.print_supervisor_reply(reply)
        append_supervisor_history(supervisor_history, line, reply)
        if intake_decision.intent_type == "source_ingestion":
            result = runtime.run_source_ingestion(
                title=title,
                content=content,
                url=url,
                vault_dir=args.vault,
                memory_path=args.memory,
                intake_decision=intake_decision.to_dict(),
            )
        else:
            result = runtime.run_article_research(
                title=title,
                content=content,
                url=url,
                vault_dir=args.vault,
                reports_dir=args.reports,
                memory_path=args.memory,
                intake_decision=intake_decision.to_dict(),
            )
        last_room_id = result.room.room_id
        console.last_room_id = last_room_id
        console.print_run_summary(result)


def append_supervisor_history(history: list[dict[str, str]], user_message: str, reply_lines: list[str]) -> None:
    history.append({"role": "user", "content": user_message})
    history.append({"role": "supervisor", "content": "\n".join(reply_lines)})
    del history[:-16]


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

    if command == "/board":
        console.print_agent_state()
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

    if command in {"/settings", "/company-settings"}:
        settings = load_company_settings(company_settings_path_for(args.memory))
        console.print_company_settings(settings, company_settings_path_for(args.memory))
        return False, last_room_id

    if command == "/last":
        console.print_latest_run_card(rest or None)
        return False, last_room_id

    if command == "/doctor":
        doctor_command(args)
        return False, last_room_id

    if command in {"/rooms", "/work", "/workboard"}:
        limit = int(rest) if rest.isdigit() else 8
        console.print_workboard(limit=limit)
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
        report = find_saved_report_for_request(
            room_id,
            runs_dir=Path(args.memory).parent / "runs",
            reports_dir=args.reports,
            last_room_id=last_room_id,
        )
        if report is None:
            print("This run or query does not have a matching report.")
            return False, room_id
        report_path, resolved_room_id, _topic = report
        print(report_path.read_text(encoding="utf-8"))
        return False, resolved_room_id

    if command == "/add":
        if not rest:
            print("Usage: /add <source text or URL>")
            return False, last_room_id
        title, content, url = chat_input_to_source(rest)
        runtime = ResearchRuntime(load_memory(args.memory))
        console.print_user_message(rest)
        console.print_supervisor_working("Saving this as a source-only task and assigning the archivist.")
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


def classify_chat_input(line: str) -> str:
    return decide_supervisor_intake(line).intent_type


def legacy_classify_chat_input(line: str) -> str:
    stripped = line.strip()
    lowered = stripped.lower()
    if not stripped:
        return "empty"
    if stripped.startswith(("http://", "https://")):
        return "research_request"

    strong_config_terms = [
        "설정",
        "변경",
        "바꿔",
        "고쳐",
        "수정",
        "반영",
        "업데이트",
        "앞으로",
        "항상",
        "기본",
        "그러지말고",
        "하지말고",
        "아닐경우",
        "경우엔",
        "보고서는",
        "리포트는",
        "레포트는",
        "한글로",
        "한국어",
        "영어단어",
        "슈퍼바이저",
        "supervisor",
        "사장",
        "외주",
        "역할",
        "로그",
        "테마",
        "색상",
        "보여주",
        "나오게",
        "작동하게",
    ]
    research_terms = [
        "조사",
        "리서치",
        "분석",
        "보고서",
        "리포트",
        "레포트",
        "research",
        "analyze",
        "analyse",
        "investigate",
        "report",
    ]
    explicit_research_actions = [
        "조사해",
        "조사 진행",
        "리서칭",
        "리서치해",
        "분석해",
        "보고서 만들어",
        "보고서를 만들어",
        "리포트 만들어",
        "레포트 만들어",
        "research",
        "analyze",
        "investigate",
    ]
    has_config = any(term in lowered for term in strong_config_terms) or any(term in stripped for term in strong_config_terms)
    has_research = any(term in lowered for term in research_terms) or any(term in stripped for term in research_terms)
    has_explicit_research_action = any(term in lowered for term in explicit_research_actions) or any(
        term in stripped for term in explicit_research_actions
    )

    if has_config and not has_explicit_research_action:
        return "company_config"
    if has_config and has_research and is_meta_instruction(stripped):
        return "company_config"
    if has_research:
        return "research_request"
    return "company_config"


def is_meta_instruction(text: str) -> bool:
    meta_terms = [
        "모든 부분",
        "그러지말고",
        "아닐경우",
        "설정 변경",
        "자체 반영",
        "역할",
        "광범위",
        "사장",
        "외주",
        "회사에다가",
    ]
    return any(term in text for term in meta_terms)


def apply_company_instruction(line: str, settings: CompanySettings) -> list[str]:
    applied: list[str] = []
    lowered = line.lower()

    if any(term in line for term in ["한글", "한국어", "한글로"]) or "korean" in lowered:
        settings.report_language = "ko"
        applied.append("Report language: Korean")
    if "영어단어" in line or "영어 단어" in line or "english term" in lowered:
        settings.allow_english_terms = True
        applied.append("English technical terms allowed")

    if any(term in line for term in ["슈퍼바이저", "사장", "대표", "CEO", "외주", "회사에다가", "광범위", "권한"]):
        settings.supervisor_mode = "company_ceo"
        settings.client_relationship = "outsourcing_client"
        _add_unique(settings.operating_principles, "Supervisor acts as company CEO: classify intent before opening a Research Room.")
        _add_unique(settings.operating_principles, "Treat the user as an outsourcing client giving company-level work orders.")
        _add_unique(settings.operating_principles, "Every plain chat input passes through Supervisor intake before any agent room is opened.")
        _add_unique(settings.supervisor_authority, "route_all_plain_chat_inputs")
        _add_unique(settings.supervisor_authority, "choose_response_shape_per_request")
        _add_unique(settings.supervisor_authority, "block_unnecessary_report_generation")
        applied.append("Supervisor mode: company CEO / outsourcing intake")

    if any(term in line for term in ["설정 변경", "자체 반영", "아닐경우", "그러지말고", "출력하는게 달라", "입력하는거에 따라서"]):
        settings.auto_apply_company_instructions = True
        _add_unique(settings.operating_principles, "Only open a Research Room for explicit research, analysis, or report requests.")
        _add_unique(settings.operating_principles, "Apply company configuration instructions directly instead of creating reports.")
        settings.intake_policy["company_config"] = "apply settings directly without report generation"
        settings.intake_policy["company_status"] = "show status/settings panel without report generation"
        settings.intake_policy["research_request"] = "open Research Room only for explicit research, analysis, or report requests"
        applied.append("Chat routing: settings instructions are applied directly")

    if not applied:
        _add_unique(settings.operating_principles, line.strip())
        applied.append("Saved as company operating instruction")

    _add_unique(settings.raw_instructions, line.strip())
    return applied


def _add_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def chat_input_to_source(line: str) -> tuple[str, str, str | None]:
    if line.startswith("http://") or line.startswith("https://"):
        content = fetch_url_text(line)
        return infer_title(content, line), content, line
    return infer_title(line, None), line, None


def find_saved_report_for_request(
    line: str,
    *,
    runs_dir: str | Path,
    reports_dir: str | Path,
    last_room_id: str = "",
) -> tuple[Path, str, str] | None:
    runs_root = resolve_project_path(runs_dir)
    reports_root = resolve_project_path(reports_dir)
    query = extract_report_lookup_query(line)
    candidates = list_run_summaries(runs_root)

    if query:
        for item in candidates:
            if str(item.get("room_id", "")) == query:
                report = _report_path_from_summary(item)
                if report and report.exists():
                    return report, str(item.get("room_id", "")), str(item.get("topic", ""))

    if last_room_id and not query:
        for item in candidates:
            if str(item.get("room_id", "")) == last_room_id:
                report = _report_path_from_summary(item)
                if report and report.exists():
                    return report, last_room_id, str(item.get("topic", ""))

    if query:
        query_terms = [term for term in query.lower().split() if term]
        for item in candidates:
            searchable = " ".join(
                [
                    str(item.get("room_id", "")),
                    str(item.get("topic", "")),
                    str(item.get("report", "")),
                ]
            ).lower()
            if all(term in searchable for term in query_terms):
                report = _report_path_from_summary(item)
                if report and report.exists():
                    return report, str(item.get("room_id", "")), str(item.get("topic", ""))

    reports = sorted(reports_root.glob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True) if reports_root.exists() else []
    if query:
        query_terms = [term for term in query.lower().split() if term]
        for path in reports:
            searchable = path.stem.lower()
            if all(term in searchable for term in query_terms):
                return path, "", path.stem
    if reports and not query:
        return reports[0], "", reports[0].stem
    return None


def extract_report_lookup_query(line: str) -> str:
    cleaned = line.strip()
    stopwords = [
        "보고서",
        "리포트",
        "레포트",
        "만든거",
        "만든 것",
        "만들었던",
        "만들어봐",
        "만들어",
        "작성한",
        "작성했던",
        "작성해봐",
        "작성해",
        "생성해봐",
        "생성해",
        "보내봐",
        "보내줘",
        "보여줘",
        "꺼내줘",
        "열어줘",
        "출력해줘",
        "들고와",
        "들고와봐",
        "들고 와",
        "들고 와봐",
        "가져와",
        "가져와봐",
        "가져 와",
        "가져 와봐",
        "가지고와",
        "가지고와봐",
        "가지고 와",
        "가지고 와봐",
        "갖고와",
        "갖고와봐",
        "갖고 와",
        "갖고 와봐",
        "불러와",
        "불러와봐",
        "불러 와",
        "불러 와봐",
        "찾아줘",
        "찾아봐",
        "내놔",
        "전체",
        "전부",
        "풀버전",
        "줘",
        "봐",
        "보고서",
        "리포트",
        "레포트",
        "만든거",
        "만든 것",
        "만들었던",
        "만들어봐",
        "만들어",
        "작성한",
        "작성했던",
        "작성해봐",
        "작성해",
        "생성해봐",
        "생성해",
        "보내봐",
        "보내줘",
        "보여줘",
        "꺼내줘",
        "열어줘",
        "출력해줘",
        "전체",
        "전부",
        "다",
        "풀버전",
        "report",
        "send",
        "show",
        "print",
        "view",
        "open",
        "full",
        "all",
    ]
    for word in stopwords:
        cleaned = cleaned.replace(word, " ")
    cleaned = " ".join(cleaned.replace("/", " ").split())
    return cleaned


def _report_path_from_summary(item: dict[str, object]) -> Path | None:
    report = str(item.get("report") or "")
    if not report:
        return None
    return resolve_project_path(report)


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
    memory_path = getattr(args, "memory", default_project_path("data/memory.json"))
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
    groups = {
        "Runtime": {
            "Runtime scaffold",
            "Agent specs/personas",
            "Shared memory JSON",
            "Run snapshots",
            "Report writer",
            "Obsidian vault writer",
        },
        "Models": {"LLM provider", "Codex OAuth token", "OpenAI API key"},
        "Operations": {
            "Tool registry",
            "Scheduled jobs",
            "Worker profiles",
            "Telegram delivery config",
            "Artifact directory",
        },
        "Live research tools": set(),
        "Overall": {"Overall"},
    }
    for capability in capabilities:
        if (
            capability.name not in groups["Runtime"]
            and capability.name not in groups["Models"]
            and capability.name not in groups["Operations"]
            and capability.name != "Overall"
        ):
            groups["Live research tools"].add(capability.name)

    for group_name, names in groups.items():
        group_items = [capability for capability in capabilities if capability.name in names]
        if not group_items:
            continue
        print(f"\n[{group_name}]")
        for capability in group_items:
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
    configured = os.getenv("JIMMORIA_MODEL_SETTINGS_PATH")
    if configured:
        return Path(configured)
    return resolve_project_path("data/model_settings.json")


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


def auto_configure_codex_cli_if_logged_in() -> None:
    if os.getenv("LLM_PROVIDER"):
        return
    if not codex_is_logged_in():
        return
    os.environ["LLM_PROVIDER"] = "codex_cli"
    save_model_settings()


def codex_is_logged_in() -> bool:
    status = codex_login_status().lower()
    return "logged in" in status or "authenticated" in status


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

        if event_type in {"tool_start", "tool_done", "tool_failed", "tool_denied", "tool_unconfigured"}:
            status = event_type.replace("tool_", "")
            print(
                f"[TOOL] {event.get('agent_id')} -> {event.get('tool_name')} "
                f"{status} | {event.get('summary', '')}"
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
            return

        if event_type == "room_failed":
            print_box(
                [
                    f"Failed: {event.get('room_id')}",
                    f"Status: {event.get('status')}",
                    f"Reason: {event.get('summary')}",
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
        text = message_summary(message)
        print(
            f"{message.get('created_at')} | {message.get('type')} | "
            f"{message.get('from_agent')} -> {message.get('to_agent')} | {text}"
        )


def message_summary(message: dict[str, object]) -> str:
    for field_name in ["task", "result", "payload", "context"]:
        value = message.get(field_name)
        if not isinstance(value, dict):
            continue
        for key in ["summary", "objective", "status", "message"]:
            text = value.get(key)
            if text:
                return str(text)
    notes = message.get("notes")
    if isinstance(notes, list) and notes:
        return str(notes[0])
    for key in ["summary", "status"]:
        text = message.get(key)
        if text:
            return str(text)
    return "(no summary)"


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
