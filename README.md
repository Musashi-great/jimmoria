# JIMMORIA

JIMMORIA is a multi-agent crypto research company that runs from a CLI and a local web dashboard.

It is not a trading bot. It does not place orders, sign wallets, predict prices, or manage assets. The system is built for research: source collection, early project discovery, KOL/social context, docs/GitHub checks, on-chain identity checks, funding/token hints, report writing, and Obsidian-style knowledge storage.

## Current Direction

The default research stack is social-signal first and public-web verified.

JIMMORIA now focuses on sources that can be reached through web-accessible, read-only research tools:

- X/Twitter recent search and KOL timeline checks, when an X bearer token is configured
- Public web fallback searches for X/Twitter posts, KOL opinions, threads, and articles
- Web search
- URL fetch and HTML parsing
- Official website crawling
- Docs crawling
- GitHub search, repository reading, and activity checks
- RSS/public feed monitoring
- DefiLlama protocol and TVL context
- Snapshot governance proposals
- Token metadata and specific DEX pair lookup through public metadata APIs
- RootData, when an API key is configured
- Explorer/RPC read-only checks, when API/RPC secrets are configured
- DEX Screener and CoinGecko public metadata

Telegram and Discord are intentionally not part of the default connector stack. They can still appear as official community links on a project website, but JIMMORIA will not require Telegram bots, Discord bots, or private chat channel readers for normal research.

## Quick Start

Install once:

```powershell
cd C:\jimmoria
python -m pip install -e ".[all]"
```

Start the company:

```powershell
jimmoria
```

The CLI runs as a chat-style Research HQ. During a Research Room, raw runtime events stay in the background while a fixed runtime dock shows the current work:

```text
--------------------------------------------------------------------------------+
| JIMMORIA HQ | Supervisor channel | provider: codex_cli | room: room_x | ...    |
| Room running. Input returns when Supervisor finishes this room.                 |
|--------------------------------------------------------------------------------|
| Live agent board - current work                                                |
| STATE  AGENT                        CURRENT WORK                               |
| RUN    discovery_agent              Now: Tool running: web_search - ...        |
| WAIT   product_tech_agent           Waiting: Checking docs/GitHub              |
| DONE   supervisor_agent             Finished: Research room initialized        |
|--------------------------------------------------------------------------------|
| > working...                                                                   |
+--------------------------------------------------------------------------------+
```

That dock is the lightweight TUI layer: it keeps the current room, provider, agent progress, and each agent's active job visible while detailed `Room >`, `Agent >`, `Tool >`, and `Output >` events run in the background and are saved under `data/runs/<room_id>/`. Set `JIMMORIA_EVENT_STYLE=stream` only when you want the raw live event stream on screen for debugging.

Open the local web dashboard:

```powershell
jimmoria web
```

Default URL:

```text
http://127.0.0.1:8787
```

Run a demo:

```powershell
jimmoria demo
```

## Model Setup

JIMMORIA can run Codex-only, Grok-only, or Codex+Grok hybrid routing. The recommended production setup is hybrid: Codex keeps orchestration/final writing stable, while Grok/xAI is used for social, narrative, and candidate-discovery work.

Recommended provider:

```text
Codex + Grok role routing
```

Install Codex SDK support:

```powershell
python -m pip install -e ".[codex]"
```

If Codex login already exists locally, JIMMORIA can reuse it. It stores provider/model preferences in `data/model_settings.json`, not raw tokens.

Optional Grok/xAI provider:

```powershell
hermes auth add xai-oauth
$env:LLM_PROVIDER = "xai_oauth"
jimmoria
```

This uses the Hermes xAI OAuth session stored in `~/.hermes/auth.json`. Hermes opens `accounts.x.ai`, stores the xAI OAuth tokens, refreshes them when needed, and JIMMORIA reuses that session without saving raw tokens.

API-key and explicit bearer-token sources are still accepted for fallback/operator workflows:

```powershell
$env:LLM_PROVIDER = "grok"
$env:XAI_API_KEY = "xai-..."
$env:GROK_OAUTH_TOKEN = "..."
$env:GROK_OAUTH_TOKEN_FILE = "C:\path\to\xai-token.txt"
$env:GROK_OAUTH_TOKEN_COMMAND = "op read op://vault/xai/token"
```

The xAI API uses an OpenAI-compatible endpoint at `https://api.x.ai/v1`. JIMMORIA does not save raw Grok/XAI tokens in `data/model_settings.json`; it only saves provider/model preferences, token file paths, or token commands. With `LLM_PROVIDER=xai_oauth`, Hermes OAuth is preferred over `XAI_API_KEY`. With `LLM_PROVIDER=grok`, API key/env sources are preferred and Hermes OAuth is used as a fallback.

Important: `xai_oauth` is a Grok credential mode. For role-based company routing, use `LLM_PROVIDER=codex_grok`; JIMMORIA will still use Hermes xAI OAuth for the Grok side.

Hybrid Codex + Grok mode:

```powershell
$env:LLM_PROVIDER = "codex_grok"
jimmoria
```

Default hybrid routing:

```text
Codex: supervisor, ingestion, contract/on-chain, product/tech, funding/token, report, Obsidian
Grok:  social/KOL, narrative, discovery
```

Optional overrides:

```powershell
$env:JIMMORIA_CODEX_PROVIDER = "codex_cli"   # or codex_sdk
$env:JIMMORIA_AGENT_PROVIDER_SOCIAL_KOL_AGENT = "grok"
$env:JIMMORIA_AGENT_PROVIDER_REPORT_AGENT = "codex"
```

Default model routing:

```text
Codex supervisor chat:        gpt-5.4-mini
Codex specialist reasoning:   gpt-5.5 + pro reasoning
Grok-only chat/reasoning:     grok-4.3 + high reasoning effort
Hybrid report synthesis:      Codex writing model + pro reasoning
```

For Codex CLI, JIMMORIA maps `pro` to the local Codex config value `model_reasoning_effort="xhigh"` when the installed `codex exec` supports `--config`.

## How The Company Works

The user talks to the Supervisor. The Supervisor acts as the company boss and orchestrator.

Typical flow:

```text
User request
-> Supervisor intake and confirmation
-> Research Room opens only for explicit report/dossier creation
-> Supervisor creates a plan and delegates tasks
-> Ingestion stores the request/source
-> Social/KOL agent collects X, KOL, public thread, and article market signals first
   - who said what, official/candidate X handles, timeline status, and article/KOL opinion sources
-> Narrative and Discovery resolve project identity from those signals
-> Product/docs/GitHub, token/chain, funding, and candidate-specific social checks verify the project
-> Agent Council summarizes agreement and risks
-> ReportAgent drafts the report
-> Supervisor performs final review
-> CLI/web output, report file, and Obsidian notes are saved
```

For ordinary conversation, configuration requests, source-only notes, or loose "research this" messages, the Supervisor answers directly and keeps the room closed. Ask for a report or dossier when you want the full multi-agent room.

Project-specific seed evidence is kept outside agent code in `config/project_profiles/`. These profiles can hold aliases, official site/X/docs, search seeds, address registry hints, funding context, and article notes. Agents can use them as starting evidence, but the report still labels what is confirmed, partial, or unverified.

## Core Agents

```text
supervisor_agent          Plans, routes, confirms, and final-reviews work
ingestion_agent           Stores sources and extracts metadata
social_kol_agent          First collects X/KOL/thread/article signals, then checks official social identity
narrative_agent           Maps market narratives and thesis categories
discovery_agent           Resolves candidates from social-first and public evidence
contract_onchain_agent    Verifies chain, token, contract, DEX/explorer identity
product_tech_agent        Checks website, docs, GitHub, and product readiness
funding_token_agent       Reviews investors, points, airdrop, and token hints
report_agent              Turns findings into a human-readable dossier
obsidian_curator_agent    Saves projects, sources, narratives, and reports
signal_triage_agent       Planned: routes monitor signals to archive/watchlist/Supervisor review
```

## Agent Skills And Hooks

Each agent now has explicit `skills` and runtime `hooks` in `config/agents/*.yaml`.

Skills are local playbooks in `config/skills/`. Each agent uses a structured skill policy:

```text
primary     core playbooks the agent owns
secondary   reusable sub-skills and repeated work patterns
disabled    capabilities this agent must not use
```

Hooks are runtime checkpoints that fire around agent execution, tool usage, and report writing. They appear as `agent_hook` events for future CLI/web replay.

```text
supervisor_agent          supervisor_orchestration, project_research, identity_gate
ingestion_agent           article_ingestion, source_evidence_intake
social_kol_agent          social_signal_intake, project_research
narrative_agent           narrative_mapping, project_research
discovery_agent           early_token_discovery, identity_gate, project_research
contract_onchain_agent    identity_gate, onchain_token_verification
product_tech_agent        product_tech_diligence, identity_gate
funding_token_agent       funding_token_diligence, identity_gate
report_agent              investment_report_synthesis, project_research, identity_gate
obsidian_curator_agent    obsidian_memory_sync
signal_triage_agent       signal_triage
```

Hook phases:

```text
before_run        prepare context and load the right playbook
before_tool_call  check permission, source scope, and read-only boundaries
after_tool_call   normalize evidence and write audit-friendly traces
before_report     run report-specific claim and source coverage checks
after_report      write artifact/evidence handoff and request final review
quality_gate      verify the agent output is usable for the final report
after_run         hand off findings to the next company step
```

## Tool Status

Works without secrets:

```text
web_search
fetch_url
parse_html
crawl_website
crawl_docs
github_search_repos
read_github_repo
github_get_repo_activity
rss_monitor_feed
defillama_protocol_search
defillama_tvl_snapshot
snapshot_get_proposals
coingecko_coin_metadata
dexscreener_search_pairs
get_token_metadata
get_dex_pair
check_airdrop_points
archive_source_snapshot
supervisor_office tools
local artifact/report/note writing
Hermes-style operator bridge: skill_view, read_file, browser_navigate, browser_console,
browser_snapshot, browser_click, search_files, execute_code, write_file, delegate_task,
cronjob, multi_tool_use.parallel
```

Optional secrets for stronger live research:

```powershell
$env:X_BEARER_TOKEN="..."
$env:ROOTDATA_API_KEY="..."
$env:ETHERSCAN_API_KEY="..."
$env:ETH_RPC_URL="..."
```

Not required by the current direction:

```text
TELEGRAM_BOT_TOKEN
DISCORD_BOT_TOKEN
```

Operator-style tools that remain intentionally blocked or external-only:

```text
terminal              registered for audit, arbitrary shell blocked for agents
send_message          registered for audit, external messaging disabled
browser_vision        future external vision connector
vision_analyze        future external vision connector
```

## Concurrency Roadmap

Current active phase:

```text
Phase 3: full_parallel_research_swarm
After the Supervisor seeds the shared source/candidate context, ingestion, Social/KOL,
Narrative, Discovery, Contract/On-chain, Product/Tech, and Funding/Token agents run
at the same time.
```

Planned:

```text
Phase 4: parallel_research_rooms
Investigate Candidate A, B, C in separate Research Rooms, then merge summaries.
```

Policy file:

```text
config/concurrency.yaml
```

## Useful Commands

```text
jimmoria                    Start chat-style Research HQ
jimmoria web                Start local web dashboard
jimmoria demo               Run demo research
jimmoria doctor             Show configured capabilities
jimmoria runs               List previous runs
jimmoria status <room_id>   Show room status
jimmoria messages <room_id> Show collaboration messages
jimmoria events <room_id>   Show replay events
jimmoria events <room_id> --after-seq 40
                            Show only events after a known cursor
jimmoria fork <room_id> --seq 40
                            Fork a saved room from an event checkpoint
jimmoria report <room_id>   Print saved report
```

Inside `jimmoria`:

```text
/models
/doctor
/company
/context
/runs
/status [room_id]
/messages [room_id]
/events [room_id]
/report [room_id]
/last
/help
/quit
```

## Report Output

When a Research Room passes the quality gate, JIMMORIA prints the full Markdown report in the CLI and also shows the saved report path.

The report is designed as a project-understanding and investment-memo style dossier, not an agent log or a source list. It should explain what the project is, what it is building, how the token/chain structure works, why the narrative matters, what public X/KOL/article sources actually say, who funded it, what risks remain, and what to verify next.

For high-signal projects, the report should be prose-rich enough to read like a research memo: conclusion first, project mechanics explained in plain language, social/KOL interpretation separated from official proof, token value-capture discussed as live-vs-roadmap, and risks written as counter-thesis rather than raw warnings.

For Korean research requests, JIMMORIA writes Korean-first reports. Links are displayed with short labels such as `The Block`, `3Jane site`, or `x.com/3janexyz/status`, while the Markdown target keeps the full URL.

Completed project reports follow this shape:

```text
1. Conclusion first
2. Project overview
3. Market narrative and why now
4. Product / protocol structure
5. Token / chain / value-capture
6. Team / funding / KOL
7. Risks and counter-thesis
8. Diligence questions
9. Confirmed content summary
```

In interactive chat, JIMMORIA keeps the final report and Vault notes, then cleans transient room data by default. The retained pointer is `data/report_index.json`, so a later request for the same project can still find and reference the previous report. Set `JIMMORIA_CHAT_RUN_RETENTION=debug` if you want to keep `data/runs/<room_id>/`, `memory.json`, evidence packets, and replay events for debugging or web replay.

Agent execution logs, council discussion, tool payloads, raw LLM output, specialist coverage, and AntSeed-style peer review are written under `data/runs/<room_id>/` only when debug retention is enabled. They are not part of the completed report body.

Completed reports now include a claim-level evidence ledger. This separates major claims such as identity, product, social/KOL, funding/team, token/on-chain, GitHub activity, and live metrics so a report is not marked strong merely because it collected many URLs.

If evidence is insufficient, the CLI prints a diagnostic preview instead of presenting it as a finished dossier.

Reprint any saved report:

```text
/report <room_id>
jimmoria report <room_id>
```

Set `JIMMORIA_REPORT_DISPLAY=preview` when you want completed rooms to show only a preview.

## AX-Inspired Runtime Controls

JIMMORIA does not depend on Google's AX runtime, but it now borrows the parts that fit this company structure:

- a single Supervisor/Runtime path acts as the controller for each room
- every runtime event gets a stable `seq`
- agent and room events include `duration_ms` plus LLM call/token usage
- `events.json` can be replayed from a cursor instead of reading the whole room again
- a saved room can be forked from a checkpoint for alternate follow-up work
- the web dashboard shows the latest event cursor as `last_seq`

Resume-style event catch-up:

```text
jimmoria events <room_id> --after-seq 40
```

Checkpoint fork:

```text
jimmoria fork <room_id> --seq 40
```

## Project Structure

```text
crypto_research_agents/
  cli.py                    CLI entrypoint and chat loop
  console.py                Terminal UI
  runtime.py                Research Room runtime
  agents/                   Specialist agents
  connectors/               Public web and optional secret-backed connectors
  core/                     Bus, memory, model gateway, tool gateway, policies
  storage/                  Run snapshots, reports, Obsidian notes
  web/                      Local dashboard

config/
  agents/                   Agent persona and policy specs
  tools/                    Tool registry
  toolsets.yaml             Agent toolset policy
  project_profiles/         Project-specific evidence seeds outside code
  processes/                Research Room process specs
  concurrency.yaml          Phase 1-4 execution policy

docs/
  jimmoria-project-structure.md

data/                       Local run data, ignored by git
reports/                    Generated reports, ignored by git
vault/                      Obsidian-style notes, ignored by git
```

## Tests

```powershell
python -m unittest discover -s tests -v
```
