# JIMMORIA Product Direction

JIMMORIA is a read-only AI crypto research desk for early public signal detection, identity verification, thesis generation, and outcome-backed thesis memory.

JIMMORIA is not a trading bot. It does not sign wallets, place orders, approve transfers, forecast prices, or provide buy/sell instructions. Its durable moat is not the number of agents. Its moat is source-backed thesis memory and outcome-labeled research history.

## Product Modes

### Radar Mode

Radar Mode scans public, read-only sources for early project signals and routes each signal into a board state.

Primary sources:

- X/Twitter public search and KOL timeline checks when available
- RSS and public web search
- Official websites, docs, and GitHub repositories
- RootData, DEX Screener, CoinGecko, DefiLlama, explorers, and read-only RPC metadata

Excluded sources and actions:

- Private Telegram or Discord channels
- Wallet signing, swaps, approvals, transfers, or transaction simulation for trading
- Private keys, seed phrases, or private account data

Primary output: Radar Board, not a long report.

### Dossier Mode

Dossier Mode investigates one project, URL, GitHub repo, docs site, token, contract address, or candidate. It produces a Korean-first source-backed project dossier with identity verification, product/docs/GitHub/social/funding/token/on-chain evidence, thesis, counter-thesis, and a final research stance.

Allowed stance labels:

- `TOP`
- `WATCH`
- `OPERATOR`
- `EXCLUDE`

These are research stances, not buy/sell instructions.

### Thesis Memory Mode

Thesis Memory Mode creates, searches, reviews, and labels thesis cards over time. It compares current candidates with prior thesis cards and tracks whether the thesis strengthened, weakened, became invalid, or remained unresolved.

Useful command direction:

- `jimmoria thesis search <query>`
- `jimmoria thesis review <project>`
- `jimmoria thesis outcomes --due`

## Core Data Products

### Radar Board

The Radar Board separates early public signals into:

- `Research Room Recommended`
- `Watchlist`
- `Identity Conflict`
- `Already Late`
- `Archive`
- `Red Flags`

Every row must include evidence and source references. Unsourced judgment is disallowed.

### Thesis Card

A thesis card is the durable memory object. It captures the project, source layer, identity status, token status, evidence strength, stance, what must be true, counter-thesis, similar past theses, next check date, and outcome labels.

### Outcome Label

Outcome labels track whether the original research thesis aged well. JIMMORIA should not only track price. It should track source-backed changes in identity, docs, GitHub activity, product launch, social/KOL breadth, funding confirmation, token/on-chain status, and thesis validity.

## Safety Contract

JIMMORIA must keep these constraints visible in docs, config, and output templates:

- No trading
- No wallet signing
- No private keys
- No seed phrases
- No swaps, approvals, or transfers
- No investment advice
- No guaranteed price prediction
- Public read-only research only
- Source-backed output only

## Initial Implementation Boundary

The first implementation layer should stay small:

- Product direction document
- `early_radar_v2` workflow manifest
- Thesis Engine and Outcome Labeler agent configs
- Thesis card, signal, identity verification, and outcome label schemas
- Radar Board sample output
- README pointer

Large runtime rewrites, API integrations, private chat ingestion, trading features, wallet features, and price prediction models are out of scope.
