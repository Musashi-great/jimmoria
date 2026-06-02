# JIMMORIA

Controlled P2P multi-agent MVP for a crypto research-only company.

JIMMORIA is meant to feel like a research company you can chat with from the
terminal. You type a research request, then the Supervisor opens a room and the
specialized agents work in public: ingestion, narrative mapping, discovery,
social/KOL checks, contract/on-chain checks, product/docs checks, funding/token
checks, report writing, and Obsidian sync.

The first screen shows a color JIMMORIA hero panel and a short description. The
agent roster is hidden by default; use `/company` when you want to inspect every
agent. Model/auth setup screens clear between steps so each choice opens a fresh
panel instead of appending more text below the old one.

The first loop supports:

```text
article/source input
-> research room
-> ingestion
-> narrative mapping
-> candidate discovery
-> social / contract / product / funding checks
-> report generation
-> Obsidian-style markdown notes
```

Install the CLI once from the project folder:

```powershell
cd C:\jimmoria
python -m pip install -e .
```

After that, start the company with:

```powershell
jimmoria
```

Run the demo:

```powershell
jimmoria demo
```

Start the interactive research console:

```powershell
jimmoria
```

When chat starts, it opens a model setup panel:

```text
1. Codex OAuth / ChatGPT login code
2. OpenAI API Key
3. Offline fallback
```

Choose `Codex OAuth / ChatGPT login code` to run the Codex device login flow.
Codex shows the browser/code login flow, then JIMMORIA uses the local Codex CLI
ChatGPT login session for model calls. Manual bearer-token entry is still
available as a fallback. Tokens are not written to config files.

After Codex login succeeds once, you normally do not need to log in again unless
you sign out, change machines, or the Codex session expires. JIMMORIA stores only
non-secret model/provider preferences in `data/model_settings.json`; it does not
store bearer tokens.

If a provider is already saved, `jimmoria` skips the startup model setup panel
and goes straight into chat. Use `/models` anytime to change provider or model
routes.

Model routing is selectable. Choose the recommended `Use provider default for
every agent` route if you do not know model ids. JIMMORIA will let Codex use the
default model your Codex CLI account/config supports.

Inside chat mode:

```text
Type any research question/source text to open a Research Room.

/add <text-or-url>       Ingest source only
/models                  Configure LLM provider/models
/doctor                  Show configured vs placeholder capabilities
/company                 Show active and planned agents
/runs                    Show previous runs
/status [room_id]        Show latest or selected room status
/messages [room_id]      Show collaboration history
/events [room_id]        Show saved UI/replay events
/report [room_id]        Print saved report
/last                    Show the latest run card
/help                    Show help
/quit                    Exit
```

Run a full research loop:

```powershell
jimmoria research --title "AI wallet thesis" --file .\source.txt
jimmoria research --title "AI wallet thesis" --url "https://example.com/article"
```

Ingest a source only:

```powershell
jimmoria add-source --title "Source note" --text "AI wallet automation..."
```

Inspect runs:

```powershell
jimmoria runs
jimmoria doctor
jimmoria status <room_id>
jimmoria messages <room_id> --limit 10
jimmoria events <room_id> --limit 30
jimmoria show-report <room_id>
```

Use a live LLM provider:

```powershell
$env:LLM_PROVIDER="openai"
$env:OPENAI_API_KEY="..."
$env:OPENAI_MODEL_FAST="your-fast-model"
$env:OPENAI_MODEL_REASONING="your-reasoning-model"
$env:OPENAI_MODEL_WRITING="your-writing-model"
jimmoria demo
```

Use the Codex CLI ChatGPT login session:

```powershell
codex login --device-auth
jimmoria
```

Or use a Codex OAuth bearer token manually:

```powershell
$env:LLM_PROVIDER="codex_oauth"
$env:CODEX_OAUTH_TOKEN="..."
$env:CODEX_OAUTH_MODEL_FAST="your-fast-model"
$env:CODEX_OAUTH_MODEL_REASONING="your-reasoning-model"
$env:CODEX_OAUTH_MODEL_WRITING="your-writing-model"
jimmoria
```

Or provide the token through a command:

```powershell
$env:LLM_PROVIDER="codex_oauth"
$env:CODEX_OAUTH_TOKEN_COMMAND="your-command-that-prints-a-bearer-token"
jimmoria demo
```

The manual bearer-token provider does not automatically read Codex internal auth
files. If you want the ChatGPT code-login flow, use `codex_cli` through
`codex login --device-auth`.

Once you pick Codex CLI in `/models`, JIMMORIA remembers `LLM_PROVIDER=codex_cli`
locally. The next startup can reuse the existing Codex login session.

If no live provider is configured, the runtime uses a deterministic offline fallback so the agent loop still runs.

Current MVP limitations:

```text
Works now:
- Research Room orchestration
- Supervisor + controlled P2P Agent Bus
- AgentSpec/persona loading
- LLM provider routing with offline fallback, OpenAI API key, Codex CLI ChatGPT login, or explicit Codex OAuth token source
- Markdown reports, run snapshots, tool audit logs, LLM call logs
- Obsidian-style local note writing

Not live yet:
- X/Twitter/KOL search
- Telegram/Discord channel reading
- Explorer/RPC contract lookup
- DEX pair/token metadata lookup
- Docs/GitHub/website crawling
- Funding, points, and airdrop checking

Those live research tools currently return `unconfigured` through ToolGateway.
Use `jimmoria doctor` to see this status before testing.
```

Useful outputs:

```text
reports/                 Markdown research reports
vault/                   Obsidian-style notes
data/memory.json         MVP shared memory snapshot
data/runs/<room_id>/     room.json, messages.json, tool_audit_log.json
                         llm_call_log.json, events.json
```

`events.json` is the visual-ready event stream for replaying how the company
worked on a request. A future terminal dashboard or web UI can render this file
as an agent timeline, graph, or room view.

The executable spec lives in:

```text
docs/crypto-research-company-v1.4-execution-spec.md
```

Agent identities are configured in:

```text
config/agents/*.yaml
```

Each AgentSpec includes persona fields:

```text
persona_name
identity
personality
mission
scope
must_follow
must_not
```

Use `/company` in chat mode to see each agent and persona.

Tool registry is configured in:

```text
config/tools/tool_registry.yaml
```

The registry separates required, recommended, and optional tools across X/KOL, Telegram/Discord/RSS, website/docs/GitHub, RootData, CoinGecko, DefiLlama, DEX Screener, Etherscan, Dune, The Graph, Snapshot, reporting, Obsidian, and safety gates.

Model routing defaults are documented in:

```text
config/models/model_router.yaml
```
