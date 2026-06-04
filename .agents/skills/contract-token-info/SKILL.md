---
name: contract-token-info
description: Verify token, contract, chain, and official address evidence.
owner_agents:
  - contract_onchain_agent
  - discovery_agent
---

# Contract Token Info Skill

## Goal

Separate official token/contract facts from market guesses, unofficial pairs, ticker collisions, and user-provided assumptions.

## Steps

1. Resolve chain guess from official docs/site/profile.
2. Search official address registry before explorer or DEX.
3. Check explorer, DEX pair, CoinGecko/GeckoTerminal, and token metadata.
4. Mark token status as live, pre-token, points/credit asset, roadmap, or unknown.
5. Keep LP/holder/liquidity data as background unless fatal.

## Output

- chain
- token_status
- official_contracts
- explorer_status
- dex_pair
- market_identity
- source_refs
- unclear_points

## Guardrails

- Never guess a contract address.
- Unofficial DEX pair does not prove official token.
- No trading, wallet signing, or transaction execution.

