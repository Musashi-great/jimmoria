# JIMMORIA

JIMMORIA is a multi-agent crypto research company that runs from a CLI and a local web dashboard.

It is not a trading bot. It does not place orders, sign wallets, predict prices, or manage assets. The system is built for research: source collection, early project discovery, KOL/social context, docs/GitHub checks, on-chain identity checks, funding/token hints, report writing, and Obsidian-style knowledge storage.

## Current Direction

The default research stack is public-web first.

JIMMORIA now focuses on sources that can be reached through web-accessible, read-only research tools:

- Web search
- URL fetch and HTML parsing
- Official website crawling
- Docs crawling
- GitHub search, repository reading, and activity checks
- RSS/public feed monitoring
- DefiLlama protocol and TVL context
- Snapshot governance proposals
- Token metadata and specific DEX pair lookup through public metadata APIs
- X/Twitter public search and KOL timeline checks, when an X bearer token is configured
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

JIMMORIA is Codex-first.

Recommended provider:

```text
Codex SDK / local app-server
```

Install Codex SDK support:

```powershell
python -m pip install -e ".[codex]"
```

If Codex login already exists locally, JIMMORIA can reuse it. It stores provider/model preferences in `data/model_settings.json`, not raw tokens.

## How The Company Works

The user talks to the Supervisor. The Supervisor acts as the company boss and orchestrator.

Typical flow:

```text
User request
-> Supervisor intake and confirmation
-> Research Room opens only for explicit report/dossier creation
-> Supervisor creates a plan and delegates tasks
-> Specialist agents collect evidence
-> Agent Council summarizes agreement and risks
-> ReportAgent drafts the report
-> Supervisor performs final review
-> CLI/web output, report file, and Obsidian notes are saved
```

For ordinary conversation, configuration requests, source-only notes, or loose "research this" messages, the Supervisor answers directly and keeps the room closed. Ask for a report or dossier when you want the full multi-agent room.

## Core Agents

```text
supervisor_agent          Plans, routes, confirms, and final-reviews work
ingestion_agent           Stores sources and extracts metadata
narrative_agent           Maps market narratives and thesis categories
discovery_agent           Finds early project candidates from public evidence
social_kol_agent          Checks public web, X, KOL, and official social links
contract_onchain_agent    Verifies chain, token, contract, DEX/explorer identity
product_tech_agent        Checks website, docs, GitHub, and product readiness
funding_token_agent       Reviews investors, points, airdrop, and token hints
report_agent              Turns findings into a human-readable dossier
obsidian_curator_agent    Saves projects, sources, narratives, and reports
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

## Concurrency Roadmap

Current active phase:

```text
Phase 1: sequential_room
Run the whole Research Room sequentially until every agent path is stable.
```

Planned:

```text
Phase 2: parallel_evidence_checks
After Discovery, run Social / Contract / Product / Funding evidence checks together.

Phase 3: parallel_24h_monitoring
Run public web, X, GitHub, Docs, DEX, RSS, RootData monitor workers in parallel.

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

If evidence is insufficient, the CLI prints a diagnostic preview instead of presenting it as a finished dossier.

Reprint any saved report:

```text
/report <room_id>
jimmoria report <room_id>
```

Set `JIMMORIA_REPORT_DISPLAY=preview` when you want completed rooms to show only a preview.

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
