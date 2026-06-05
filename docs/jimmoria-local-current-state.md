# JIMMORIA Local Current State

Updated: 2026-06-05

This note is the local operator handoff after merging
`origin/codex/review-and-suggest-code-improvements` into `main`.

## What Changed

- `main` now includes the Codex review branch through commit `9027dca`.
- The default live model posture is `LLM_PROVIDER=codex_grok`.
- Codex handles supervisor chat, orchestration, ingestion, product/docs, on-chain, funding/token, report writing, final synthesis, and Obsidian sync.
- Grok/xAI handles social/KOL, narrative reasoning, and candidate discovery by default.
- If a Grok route fails in hybrid mode, JIMMORIA retries the same task through Codex by default and records the fallback in `llm_call_log.json`.
- Runtime room, agent, tool, and output events are saved under `data/runs/<room_id>/`.
- Windows PowerShell uses the live agent dashboard by default. The screen shows all active AI agent cards, brief current work, and cumulative LLM call/token usage while raw logs stay in `data/runs/<room_id>/`.
- Tool access is now registry-backed through `config/tools/tool_registry.yaml` and policy-backed through `config/toolsets.yaml`.
- Agents have explicit skills and hooks in `config/agents/*.yaml`, `config/skills/`, and `config/hooks/`.
- The web dashboard entry point is `jimmoria web`, with the default local URL `http://127.0.0.1:8787`.

## Local Config Inventory

Current local repo inventory:

- 14 agent config files in `config/agents/`
- 16 local skill config files in `config/skills/`
- 95 registered skill workflows in `config/skills/skill_registry.yaml`
- 70 registered tools in `config/tools/tool_registry.yaml`
- 22 tools in the minimum viable live stack
- 10 toolsets in `config/toolsets.yaml`
- 3 workflow manifests in `config/workflows/`

Primary local references:

- `README.md`: quick start, model setup, agent list, and operator commands
- `docs/jimmoria-project-structure.md`: architecture, routing, storage, and roadmap
- `docs/jimmoria-cli-ui-reference-notes.md`: runtime dock and CLI UX notes
- `config/models/model_router.yaml`: provider order, model defaults, routing, effort mapping
- `config/tools/tool_registry.yaml`: tool categories, tool metadata, safety status
- `config/toolsets.yaml`: which agent/toolset policies expose which tools

## Model Routing

Recommended production mode:

```powershell
$env:LLM_PROVIDER = "codex_grok"
jimmoria
```

Default hybrid split:

```text
Codex:
  supervisor_agent
  ingestion_agent
  contract_onchain_agent
  product_tech_agent
  funding_token_agent
  report_agent
  obsidian_curator_agent

Grok/xAI:
  discovery_agent
  narrative_agent
  social_kol_agent
```

Default model families:

```text
Codex fast chat:      gpt-5.4-mini
Codex reasoning:      gpt-5.5
Codex writing:        gpt-5.5
Grok chat/reasoning:  grok-4.3
Shared effort:        pro
```

`CODEX_REASONING_EFFORT=pro` maps to Codex CLI
`model_reasoning_effort="xhigh"` when supported. Grok maps the same effort to
xAI `reasoning.effort="high"` for the default `grok-4.3` route.

Single-agent provider overrides:

```powershell
$env:JIMMORIA_AGENT_PROVIDER_SOCIAL_KOL_AGENT = "grok"
$env:JIMMORIA_AGENT_PROVIDER_REPORT_AGENT = "codex"
```

Hybrid Grok fallback controls:

```powershell
$env:JIMMORIA_GROK_FALLBACK_TO_CODEX = "0"     # disable automatic Codex retry
$env:JIMMORIA_DISABLE_GROK_FALLBACK = "1"      # also disables automatic Codex retry
```

## Credential Rules

Codex sources:

- Codex SDK/local app-server login
- Codex CLI login through `codex exec`
- `OPENAI_API_KEY` or `CODEX_API_KEY` through the Codex API provider

Grok/xAI sources:

- Hermes xAI OAuth session from `hermes auth add xai-oauth`
- `XAI_API_KEY` or `GROK_API_KEY`
- `GROK_OAUTH_TOKEN` / `XAI_OAUTH_TOKEN`
- token file env vars
- token command env vars

Raw OAuth/API tokens are not persisted in JIMMORIA model settings. The settings
file stores provider choice, model preferences, token file paths, token commands,
base URLs, and API mode only.

Important distinction:

- `LLM_PROVIDER=xai_oauth` means Grok-only, preferring Hermes OAuth credentials.
- `LLM_PROVIDER=codex_grok` means role-based hybrid routing; the Grok side can still use Hermes OAuth.

## Tool And Safety Policy

The default connector posture is public-web verified and read-only:

- X/Twitter search and timeline tools when credentials exist
- public web search and URL fetch
- website/docs crawling
- GitHub search, repository reads, and activity checks
- RSS/public feed monitoring
- DefiLlama, Snapshot, DEX Screener, CoinGecko, and RootData context
- explorer/RPC read-only identity checks when credentials exist
- local report and Obsidian-style note writing

Blocked by default:

- wallet signing
- swaps
- approvals
- transfers
- private key reads
- seed phrase reads

Operator-style tools such as `terminal`, `browser_vision`, `vision_analyze`, and
`send_message` are registered for audit clarity but are blocked or require future
external connectors in agent runtime policy.

## Workflows

Current workflow manifests:

- `project_diligence_v1`: deeper diligence for one project, URL, or contract address
- `candidate_diligence_v1`: candidate-level diligence for one Web3 project
- `early_radar_v1`: broad candidate discovery and evidence-first triage

The main project diligence path is:

```text
Supervisor
-> Ingestion
-> Social and KOL
-> Product and Docs
-> Onchain and Market
-> Funding and Token
-> Risk Reviewer
-> Report Writer
-> Quality Reviewer
```

## Verification

Last local verification after the branch merge:

```text
python -m pytest
143 passed
```

There was one non-failing warning about `.pytest_cache` creation permissions on
Windows. It did not affect test results.

## Push State

`main` was pushed to `origin/main` after the branch merge.

```text
main...origin/main
working tree clean
```
