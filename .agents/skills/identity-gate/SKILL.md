---
name: identity-gate
description: Resolve official project identity before candidate judgment.
owner_agents:
  - supervisor_agent
  - discovery_agent
  - contract_onchain_agent
---

# Identity Gate Skill

## Goal

Resolve what project the user is actually asking about before judging quality, token status, or narrative fit.

## Steps

1. Extract the project query, aliases, ticker, X handle, contract address, or site.
2. Search official site, official X, docs, GitHub, RootData, CoinGecko, DEX, and explorer evidence.
3. Separate official identity from generic platform pages and ticker collisions.
4. Check relaunch, name collision, unofficial CA, and unrelated project risk.
5. Create or update a candidate only when evidence is source-backed.

## Output

- project_name
- ticker_guess
- chain_guess
- official_site
- official_x
- docs
- github
- contract_or_token_identity
- candidate_origin
- evidence_urls
- unclear_points

## Guardrails

- CoinGecko, DEX, or explorer match alone is not official identity.
- GitHub org page is not official site if official domain exists.
- Unknown data stays unknown.

