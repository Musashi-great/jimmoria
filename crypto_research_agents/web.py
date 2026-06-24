from __future__ import annotations

import argparse
import json
import re
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from crypto_research_agents import APP_NAME, __version__
from crypto_research_agents.core.agent_spec import AgentSpecRegistry
from crypto_research_agents.core.capabilities import collect_capabilities
from crypto_research_agents.core.process_spec import ProcessSpecRegistry
from crypto_research_agents.runtime import COUNCIL_AGENTS, DEFAULT_AGENTS
from crypto_research_agents.storage.paths import default_project_path, resolve_project_path
from crypto_research_agents.storage.run_store import list_run_summaries, normalize_event_log


AGENT_WORK = {
    "supervisor_agent": "Plan, delegate, route, and final-review the company output.",
    "ingestion_agent": "Store source material and extract entities, keywords, and metadata.",
    "social_kol_agent": "Collect X/KOL/article market signals first, then verify candidate social identity.",
    "narrative_agent": "Map the project and social signals into market narratives and thesis categories.",
    "discovery_agent": "Resolve named projects from social-first, web, GitHub, and market evidence.",
    "contract_onchain_agent": "Verify chain, token, contract, DEX, and explorer evidence.",
    "product_tech_agent": "Inspect website, docs, GitHub, and product readiness.",
    "funding_token_agent": "Review investors, points, airdrops, and token opportunity hints.",
    "report_agent": "Turn agent findings into a human-readable dossier.",
    "obsidian_curator_agent": "Sync sources, projects, narratives, and reports into the vault.",
}

WORKFLOW_NODES = [
    {
        "id": "user",
        "label": "User Brief",
        "description": "You chat with Hermes Agent and decide whether a Research Room should open.",
    },
    {
        "id": "supervisor",
        "label": "Hermes CEO",
        "description": "Classifies intent, creates the plan, delegates tasks, and owns final quality.",
    },
    {
        "id": "room",
        "label": "Research Room",
        "description": "Runtime container for goals, agents, shared memory, tools, logs, and artifacts.",
    },
    {
        "id": "agents",
        "label": "Specialist Agents",
        "description": "Ingestion, social-first market signal intake, narrative, discovery, on-chain, product, funding, report, and vault work.",
    },
    {
        "id": "agent_council",
        "label": "Agent Council",
        "description": "Specialists compare findings before report writing and surface uncertainty.",
    },
    {
        "id": "final_review",
        "label": "Hermes Final Review",
        "description": "Hermes Agent reviews evidence quality and delivery mode before the user receives output.",
    },
    {
        "id": "delivery",
        "label": "Report + Vault",
        "description": "Markdown report, run replay logs, and Obsidian-style notes are saved locally.",
    },
]


def build_overview_payload(
    *,
    process_dir: str | Path = "config/processes",
    agent_spec_dir: str | Path = "config/agents",
    memory_path: str | Path = "data/memory.json",
    runs_dir: str | Path = "data/runs",
    vault_dir: str | Path = "vault",
    reports_dir: str | Path = "reports",
) -> dict[str, Any]:
    process_registry = ProcessSpecRegistry.load_dir(process_dir)
    agent_registry = AgentSpecRegistry.load_dir(agent_spec_dir)
    project_process = process_registry.get("project_research_room")
    source_process = process_registry.get("source_ingestion_room")
    capabilities = collect_capabilities(
        agent_spec_dir=agent_spec_dir,
        memory_path=memory_path,
        runs_dir=runs_dir,
        vault_dir=vault_dir,
        reports_dir=reports_dir,
    )

    return {
        "app": APP_NAME,
        "version": __version__,
        "mode": "Web Research HQ",
        "workflow": WORKFLOW_NODES,
        "processes": [
            _process_card(project_process),
            _process_card(source_process),
        ],
        "agents": [
            {
                "id": agent_id,
                "name": agent_registry.get(agent_id).name if agent_registry.get(agent_id) else agent_id,
                "persona": agent_registry.get(agent_id).persona_name if agent_registry.get(agent_id) else "",
                "work": AGENT_WORK.get(agent_id, "Research worker"),
                "council_member": agent_id in COUNCIL_AGENTS,
            }
            for agent_id in DEFAULT_AGENTS
        ],
        "infrastructure": [
            {"label": "ModelGateway", "description": "Routes each agent call to fast, reasoning, or writing model lanes."},
            {"label": "ToolGateway", "description": "Checks tool permissions, calls connectors, and records audit logs."},
            {"label": "SharedMemory", "description": "Stores sources, projects, findings, and entity links across runs."},
            {"label": "CollaborationBus", "description": "Records requests, responses, handoffs, and updates between agents."},
            {
                "label": "Run Store",
                "description": "Persists room snapshots with sequenced events, resume cursors, fork checkpoints, and audit logs.",
            },
        ],
        "capabilities": [item.to_dict() for item in capabilities],
    }


def list_web_runs(runs_dir: str | Path = "data/runs", *, limit: int = 50) -> list[dict[str, Any]]:
    runs = list_run_summaries(resolve_project_path(runs_dir))
    return runs[:limit]


def build_run_payload(
    room_id: str,
    *,
    runs_dir: str | Path = "data/runs",
) -> dict[str, Any]:
    safe_room_id = _safe_room_id(room_id)
    run_dir = resolve_project_path(runs_dir) / safe_room_id
    if not run_dir.exists():
        raise FileNotFoundError(run_dir)

    room = _load_json(run_dir / "room.json", {})
    events = normalize_event_log(_load_json(run_dir / "events.json", []))
    messages = _load_json(run_dir / "messages.json", [])
    tool_log = _load_json(run_dir / "tool_audit_log.json", [])
    llm_log = _load_json(run_dir / "llm_call_log.json", [])
    report_path = Path(str(room.get("output_paths", {}).get("report") or ""))
    report_exists = report_path.exists()
    report_preview = _read_text_preview(report_path) if report_exists else ""
    project_card = room.get("project_card") if isinstance(room.get("project_card"), dict) else {}
    runtime_metrics = project_card.get("runtime_metrics") if isinstance(project_card, dict) else {}

    return {
        "room": room,
        "events": events,
        "messages": messages,
        "tool_log": tool_log,
        "llm_log": llm_log,
        "agent_state": derive_agent_state(room, events),
        "counters": {
            "events": len(events),
            "messages": len(messages),
            "tool_calls": len(tool_log),
            "llm_calls": len(llm_log),
            "findings": len(room.get("shared_findings", [])),
        },
        "runtime_metrics": runtime_metrics if isinstance(runtime_metrics, dict) else {},
        "event_cursor": {
            "last_seq": max((int(event.get("seq", 0)) for event in events), default=0),
            "resume_hint": "Use events --after-seq <last_seq> to catch up without replaying the whole room.",
        },
        "artifacts": {
            "run_dir": str(run_dir),
            "report": str(report_path) if report_path else "",
            "report_exists": report_exists,
            "vault": str(room.get("output_paths", {}).get("obsidian_vault") or ""),
            "events": str(run_dir / "events.json"),
            "messages": str(run_dir / "messages.json"),
            "tool_audit_log": str(run_dir / "tool_audit_log.json"),
            "llm_call_log": str(run_dir / "llm_call_log.json"),
        },
        "report_preview": report_preview,
    }


def derive_agent_state(room: dict[str, Any], events: list[Any]) -> list[dict[str, str]]:
    agents = list(room.get("agents") or DEFAULT_AGENTS)
    state = {
        agent_id: {
            "agent_id": agent_id,
            "state": "WAIT",
            "task_type": "",
            "summary": AGENT_WORK.get(agent_id, "Waiting for work"),
        }
        for agent_id in agents
    }

    for event in events:
        if not isinstance(event, dict):
            continue
        agent_id = str(event.get("agent_id") or "")
        if not agent_id or agent_id not in state:
            continue
        event_type = str(event.get("type") or "")
        if event_type == "agent_start":
            state[agent_id].update(
                {
                    "state": "RUN",
                    "task_type": str(event.get("task_type") or ""),
                    "summary": AGENT_WORK.get(agent_id, "Running"),
                }
            )
        elif event_type == "agent_done":
            state[agent_id].update(
                {
                    "state": "DONE",
                    "task_type": str(event.get("task_type") or state[agent_id].get("task_type") or ""),
                    "summary": _compact(str(event.get("summary") or AGENT_WORK.get(agent_id, "Completed")), 140),
                }
            )
        elif event_type == "agent_failed":
            state[agent_id].update(
                {
                    "state": "FAIL",
                    "task_type": str(event.get("task_type") or state[agent_id].get("task_type") or ""),
                    "summary": _compact(str(event.get("error") or "Failed"), 140),
                }
            )
    return list(state.values())


def render_dashboard_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>JIMMORIA Web Research HQ</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #08060c;
      --panel: #11101a;
      --panel-2: #171323;
      --text: #f6eaff;
      --muted: #a997bf;
      --line: rgba(255, 86, 231, 0.42);
      --pink: #ff4fd8;
      --violet: #9d5cff;
      --blue: #6fd8ff;
      --green: #5df2a8;
      --orange: #ffb86b;
      --red: #ff647a;
      --shadow: rgba(255, 79, 216, 0.26);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background:
        radial-gradient(circle at 10% 0%, rgba(157, 92, 255, 0.18), transparent 30%),
        radial-gradient(circle at 90% 10%, rgba(255, 79, 216, 0.16), transparent 34%),
        var(--bg);
      color: var(--text);
      font: 14px/1.5 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    button, input, select { font: inherit; }
    .shell { width: min(1480px, calc(100vw - 32px)); margin: 0 auto; padding: 24px 0 44px; }
    .hero {
      border: 1px solid var(--line);
      background: linear-gradient(135deg, rgba(17,16,26,.95), rgba(23,19,35,.82));
      box-shadow: 0 28px 80px rgba(0,0,0,.42), 0 0 40px var(--shadow);
      padding: 24px;
      display: grid;
      grid-template-columns: 1.3fr .7fr;
      gap: 24px;
    }
    .brand {
      font-weight: 900;
      font-size: clamp(44px, 8vw, 118px);
      line-height: .86;
      letter-spacing: 0;
      color: var(--pink);
      text-shadow: 4px 4px 0 #542375, 10px 10px 24px rgba(255, 79, 216, .22);
    }
    .subtitle { margin-top: 18px; color: var(--muted); font-size: 18px; max-width: 760px; }
    .status-grid { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 12px; align-content: start; }
    .metric {
      border: 1px solid rgba(157,92,255,.34);
      background: rgba(255,255,255,.035);
      padding: 14px;
      min-height: 92px;
    }
    .metric b { display:block; color: var(--blue); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }
    .metric span { display:block; margin-top: 8px; font-size: 24px; font-weight: 750; }
    .layout { display: grid; grid-template-columns: 340px 1fr; gap: 18px; margin-top: 18px; }
    .panel {
      border: 1px solid rgba(255, 86, 231, .36);
      background: rgba(17,16,26,.82);
      box-shadow: 0 18px 50px rgba(0,0,0,.32);
    }
    .panel h2 {
      margin: 0;
      padding: 14px 16px;
      color: var(--pink);
      font-size: 13px;
      letter-spacing: .12em;
      text-transform: uppercase;
      border-bottom: 1px solid rgba(255,86,231,.24);
    }
    .panel-body { padding: 14px; }
    .runs { display: grid; gap: 10px; max-height: 70vh; overflow: auto; padding-right: 4px; }
    .run-card {
      width: 100%;
      text-align: left;
      border: 1px solid rgba(157,92,255,.28);
      background: var(--panel-2);
      color: var(--text);
      padding: 12px;
      cursor: pointer;
    }
    .run-card:hover, .run-card.active { border-color: var(--pink); box-shadow: 0 0 0 1px rgba(255,79,216,.18); }
    .run-topic { font-weight: 750; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .run-meta { margin-top: 7px; color: var(--muted); font-size: 12px; }
    .workflow { display: grid; grid-template-columns: repeat(7, minmax(120px,1fr)); gap: 10px; }
    .flow-card {
      border: 1px solid rgba(255,86,231,.34);
      background: linear-gradient(180deg, rgba(255,79,216,.08), rgba(157,92,255,.05));
      min-height: 132px;
      padding: 12px;
      position: relative;
    }
    .flow-card:after {
      content: "";
      position: absolute;
      right: -10px;
      top: 50%;
      width: 10px;
      height: 1px;
      background: rgba(255,86,231,.6);
    }
    .flow-card:last-child:after { display: none; }
    .flow-card b { color: var(--text); display:block; margin-bottom: 8px; }
    .flow-card p { margin: 0; color: var(--muted); font-size: 12px; }
    .detail-grid { display: grid; grid-template-columns: 1.1fr .9fr; gap: 18px; margin-top: 18px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 9px 8px; border-bottom: 1px solid rgba(255,255,255,.08); vertical-align: top; }
    th { color: var(--muted); font-size: 12px; text-align: left; font-weight: 600; }
    td { color: var(--text); }
    .state { font-weight: 800; }
    .state.RUN { color: var(--orange); }
    .state.DONE { color: var(--green); }
    .state.FAIL { color: var(--red); }
    .state.WAIT { color: var(--muted); }
    .pill {
      display: inline-flex;
      align-items: center;
      border: 1px solid rgba(157,92,255,.38);
      color: var(--muted);
      padding: 4px 8px;
      margin: 0 6px 6px 0;
      font-size: 12px;
      background: rgba(255,255,255,.035);
    }
    .log { display: grid; gap: 8px; max-height: 440px; overflow: auto; }
    .log-row {
      border-left: 3px solid var(--violet);
      background: rgba(255,255,255,.032);
      padding: 9px 10px;
    }
    .log-row .type { color: var(--pink); font-weight: 800; }
    .log-row .summary { color: var(--muted); margin-top: 3px; }
    pre {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      color: #f9f0ff;
      max-height: 520px;
      overflow: auto;
    }
    .empty { color: var(--muted); padding: 10px 0; }
    @media (max-width: 1100px) {
      .hero, .layout, .detail-grid { grid-template-columns: 1fr; }
      .workflow { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .flow-card:after { display: none; }
    }
    @media (max-width: 700px) {
      .shell { width: min(100vw - 18px, 1480px); padding-top: 10px; }
      .workflow, .status-grid { grid-template-columns: 1fr; }
      .brand { font-size: 44px; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div>
        <div class="brand">JIMMORIA</div>
        <div class="subtitle">Web Research HQ. Watch Hermes Agent, Research Room, specialist agents, Agent Council, final review, reports, and vault artifacts from one local dashboard.</div>
      </div>
      <div class="status-grid">
        <div class="metric"><b>Runtime</b><span id="runtimeStatus">loading</span></div>
        <div class="metric"><b>Runs</b><span id="runCount">0</span></div>
        <div class="metric"><b>Provider</b><span id="providerStatus">checking</span></div>
        <div class="metric"><b>Selected Room</b><span id="selectedRoom">none</span></div>
      </div>
    </section>

    <section class="layout">
      <aside class="panel">
        <h2>Research Rooms</h2>
        <div class="panel-body">
          <div class="runs" id="runs"></div>
        </div>
      </aside>
      <div>
        <section class="panel">
          <h2>Company Structure</h2>
          <div class="panel-body">
            <div class="workflow" id="workflow"><div class="empty">Loading workflow: Agent Council, Hermes Final Review.</div></div>
          </div>
        </section>

        <section class="detail-grid">
          <div class="panel">
            <h2>Live Agent Board</h2>
            <div class="panel-body">
              <table>
                <thead><tr><th>State</th><th>Agent</th><th>Current work</th></tr></thead>
                <tbody id="agentBoard"></tbody>
              </table>
            </div>
          </div>
          <div class="panel">
            <h2>Artifacts</h2>
            <div class="panel-body" id="artifacts"></div>
          </div>
        </section>

        <section class="detail-grid">
          <div class="panel">
            <h2>Event Stream</h2>
            <div class="panel-body"><div class="log" id="events"></div></div>
          </div>
          <div class="panel">
            <h2>Report Preview</h2>
            <div class="panel-body"><pre id="reportPreview">Select a run.</pre></div>
          </div>
        </section>
      </div>
    </section>
  </main>
  <script>
    const state = { overview: null, selected: null };
    const el = (id) => document.getElementById(id);

    function node(tag, className, text) {
      const item = document.createElement(tag);
      if (className) item.className = className;
      if (text !== undefined) item.textContent = text;
      return item;
    }

    function compact(value, size = 120) {
      const text = String(value || "");
      return text.length > size ? text.slice(0, size - 1) + "..." : text;
    }

    async function api(path) {
      const response = await fetch(path);
      if (!response.ok) throw new Error(await response.text());
      return response.json();
    }

    async function loadOverview() {
      state.overview = await api("/api/overview");
      el("runtimeStatus").textContent = state.overview.mode;
      const provider = state.overview.capabilities.find((item) => item.name === "LLM provider");
      el("providerStatus").textContent = provider ? provider.detail : "unknown";
      renderWorkflow(state.overview.workflow);
      renderEmptyBoard(state.overview.agents);
    }

    function renderWorkflow(items) {
      const target = el("workflow");
      target.innerHTML = "";
      for (const item of items) {
        const card = node("div", "flow-card");
        card.appendChild(node("b", "", item.label));
        card.appendChild(node("p", "", item.description));
        target.appendChild(card);
      }
    }

    function renderEmptyBoard(agents) {
      const target = el("agentBoard");
      target.innerHTML = "";
      for (const agent of agents) {
        const row = document.createElement("tr");
        row.appendChild(node("td", "state WAIT", "WAIT"));
        row.appendChild(node("td", "", agent.id));
        row.appendChild(node("td", "", agent.work));
        target.appendChild(row);
      }
    }

    async function loadRuns() {
      const runs = await api("/api/runs");
      el("runCount").textContent = runs.length;
      const target = el("runs");
      target.innerHTML = "";
      if (!runs.length) {
        target.appendChild(node("div", "empty", "No Research Rooms yet. Run jimmoria or jimmoria demo first."));
        return;
      }
      for (const run of runs) {
        const button = node("button", "run-card");
        button.dataset.roomId = run.room_id;
        button.appendChild(node("div", "run-topic", run.topic || run.room_id));
        button.appendChild(node("div", "run-meta", `${run.room_id} | ${run.status || "unknown"}`));
        button.addEventListener("click", () => selectRun(run.room_id));
        target.appendChild(button);
      }
      await selectRun(runs[0].room_id);
    }

    async function selectRun(roomId) {
      state.selected = await api(`/api/runs/${encodeURIComponent(roomId)}`);
      el("selectedRoom").textContent = roomId;
      for (const card of document.querySelectorAll(".run-card")) {
        card.classList.toggle("active", card.dataset.roomId === roomId);
      }
      renderBoard(state.selected.agent_state);
      renderArtifacts(state.selected.artifacts, state.selected.counters, state.selected.event_cursor);
      renderEvents(state.selected.events);
      el("reportPreview").textContent = state.selected.report_preview || "No report preview available.";
    }

    function renderBoard(items) {
      const target = el("agentBoard");
      target.innerHTML = "";
      for (const item of items) {
        const row = document.createElement("tr");
        row.appendChild(node("td", `state ${item.state}`, item.state));
        row.appendChild(node("td", "", item.agent_id));
        row.appendChild(node("td", "", item.summary || item.task_type || ""));
        target.appendChild(row);
      }
    }

    function renderArtifacts(artifacts, counters, eventCursor) {
      const target = el("artifacts");
      target.innerHTML = "";
      for (const [key, value] of Object.entries(artifacts)) {
        if (key === "report_exists") continue;
        target.appendChild(node("span", "pill", `${key}: ${compact(value, 80)}`));
      }
      target.appendChild(document.createElement("hr"));
      for (const [key, value] of Object.entries(counters)) {
        target.appendChild(node("span", "pill", `${key}: ${value}`));
      }
      if (eventCursor) {
        target.appendChild(node("span", "pill", `last_seq: ${eventCursor.last_seq || 0}`));
      }
    }

    function renderEvents(events) {
      const target = el("events");
      target.innerHTML = "";
      const rows = events.slice(-80);
      if (!rows.length) {
        target.appendChild(node("div", "empty", "No events for this room."));
        return;
      }
      for (const event of rows) {
        const row = node("div", "log-row");
        row.appendChild(node("div", "type", `#${event.seq || "?"} ${event.type || "event"} ${event.agent_id ? " / " + event.agent_id : ""}`));
        row.appendChild(node("div", "summary", compact(event.summary || event.topic || event.error || JSON.stringify(event), 220)));
        target.appendChild(row);
      }
    }

    async function boot() {
      try {
        await loadOverview();
        await loadRuns();
        setInterval(loadRuns, 6000);
      } catch (error) {
        el("runtimeStatus").textContent = "error";
        el("reportPreview").textContent = error.message;
      }
    }
    boot();
  </script>
</body>
</html>"""


def run_web_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    runs_dir: str | Path = "data/runs",
    reports_dir: str | Path = "reports",
    vault_dir: str | Path = "vault",
    memory_path: str | Path = "data/memory.json",
    open_browser: bool = True,
) -> None:
    handler = _handler_factory(
        runs_dir=resolve_project_path(runs_dir),
        reports_dir=resolve_project_path(reports_dir),
        vault_dir=resolve_project_path(vault_dir),
        memory_path=resolve_project_path(memory_path),
    )
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}"
    print(f"{APP_NAME} Web Research HQ running at {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} local web dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--runs-dir", default=default_project_path("data/runs"))
    parser.add_argument("--reports", default=default_project_path("reports"))
    parser.add_argument("--vault", default=default_project_path("vault"))
    parser.add_argument("--memory", default=default_project_path("data/memory.json"))
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically.")
    args = parser.parse_args(argv)
    run_web_server(
        host=args.host,
        port=args.port,
        runs_dir=args.runs_dir,
        reports_dir=args.reports,
        vault_dir=args.vault,
        memory_path=args.memory,
        open_browser=not args.no_browser,
    )


def _handler_factory(
    *,
    runs_dir: Path,
    reports_dir: Path,
    vault_dir: Path,
    memory_path: Path,
) -> type[BaseHTTPRequestHandler]:
    class JimmoriaWebHandler(BaseHTTPRequestHandler):
        server_version = "JIMMORIAWeb/0.1"

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            try:
                if path == "/":
                    self._write_html(render_dashboard_html())
                    return
                if path == "/health":
                    self._write_json({"status": "ok", "app": APP_NAME, "version": __version__})
                    return
                if path == "/api/overview":
                    self._write_json(
                        build_overview_payload(
                            memory_path=memory_path,
                            runs_dir=runs_dir,
                            vault_dir=vault_dir,
                            reports_dir=reports_dir,
                        )
                    )
                    return
                if path == "/api/runs":
                    self._write_json(list_web_runs(runs_dir))
                    return
                if path.startswith("/api/runs/"):
                    room_id = unquote(path.removeprefix("/api/runs/"))
                    self._write_json(build_run_payload(room_id, runs_dir=runs_dir))
                    return
                if path.startswith("/api/report/"):
                    room_id = unquote(path.removeprefix("/api/report/"))
                    payload = build_run_payload(room_id, runs_dir=runs_dir)
                    self._write_json({"room_id": room_id, "report": payload["report_preview"]})
                    return
                self.send_error(404, "Not found")
            except FileNotFoundError as exc:
                self.send_error(404, str(exc))
            except Exception as exc:  # pragma: no cover - HTTP safety net
                self.send_error(500, str(exc))

        def log_message(self, format: str, *args: object) -> None:
            return

        def _write_html(self, text: str) -> None:
            data = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _write_json(self, payload: Any) -> None:
            data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return JimmoriaWebHandler


def _process_card(process: Any) -> dict[str, Any]:
    if process is None:
        return {"id": "", "name": "", "type": "", "tasks": []}
    return {
        "id": process.process_id,
        "name": process.name,
        "type": process.process_type,
        "supervisor_mode": process.supervisor_mode,
        "goals": process.goals,
        "tasks": [
            {
                "task_id": task.task_id,
                "agent_id": task.agent_id,
                "phase": task.phase,
                "description": task.description,
                "expected_output": task.expected_output,
            }
            for task in process.tasks
        ],
    }


def _safe_room_id(room_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", room_id):
        raise FileNotFoundError(room_id)
    return room_id


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _read_text_preview(path: Path, *, limit: int = 8000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n... preview truncated ..."


def _compact(value: str, limit: int) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."


if __name__ == "__main__":
    main()
