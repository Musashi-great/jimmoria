# Representative Web3 Project Diligence

This playbook adapts the representative research profile into JIMMORIA's public-web research company flow.

It is used when the client asks for a Web3 project/token report, candidate dossier, or token diligence packet.
The trigger must be treated only as a candidate. Do not classify, recommend, or reject before the identity gate.

## Input

```text
Project / X / CA / Site: <client supplied value>
```

The input can be a project name, official X profile, contract address, website, docs URL, app URL, or article.

## Tool Mapping

Original operator tools such as `skill_view`, `browser_navigate`, `browser_console`, `xurl`, shell API calls, GitHub/NPM/RPC/explorer calls, and file writes are mapped into JIMMORIA as follows:

| Operator intent | JIMMORIA implementation |
|---|---|
| Load early-token-discovery / xurl skills | Attach this playbook plus local research playbooks |
| Read representative profile | Use this playbook as the profile contract |
| X profile / official site / docs / app | `x_search_posts`, `x_build_kol_list`, `web_search`, `crawl_website`, `crawl_docs` |
| Browser text/link extraction | `crawl_website`, `crawl_docs`, `url_fetcher` connectors |
| DexScreener / GeckoTerminal | `dexscreener_search_pairs`, market metadata connectors |
| GoPlus token security | planned connector; include as missing when unavailable |
| GitHub / repo clone / repo keyword search | `github_search_repos`, `read_github_repo`, `github_get_repo_activity` |
| NPM package / SDK check | planned connector; Product/Tech must flag missing if SDK evidence is unavailable |
| RPC / explorer | `explorer_lookup`, RPC connector when configured |
| Base narrative radar | planned workflow hook for Base CA inputs |
| Candidate evidence packet write | `data/evidence_packets/<project>-<room_id>.md` |

Telegram and Discord are intentionally out of scope for the current public-web-only research stack.

## Investigation Order

1. Treat trigger as candidate only.
2. Identity Gate:
   - project name, ticker, chain, CA, official site, official X, docs, GitHub, DEX/explorer match
   - ticker collision, unofficial CA, relaunch history
3. Project-first explanation:
   - what the project does
   - product/app/docs/GitHub/live infra/API/SDK evidence
4. GitHub/product verification:
   - repo structure, recent commits, contracts, circuits, SDK, tests, docs, package releases
5. Live infra verification:
   - RPC, explorer, app, API, NPM, testnet/mainnet responses when available
6. On-chain/market background:
   - DEX, FDV, volume, holder, tax, mintable, honeypot, launchpad
   - keep LP/holder/liquidity short unless fatal
7. Social/KOL:
   - official posts, KOL thesis, builder/ecosystem mentions, controversy/clarification
8. Founder dossier:
   - names, GitHub, X, LinkedIn, school, prior employer, prior projects, funding
   - never invent founder identity from name collisions
9. Token value-capture:
   - why the token is needed
   - who pays
   - gas/fee/burn/buyback/staking/revenue connection
   - separate live evidence from roadmap claims
10. Risk separation:
   - identity risk
   - founder risk
   - product maturity risk
   - security/audit risk
   - token value-capture risk
   - social/shill risk
11. Score:
   - TOP / WATCH / OPERATOR / EXCLUDE
12. Evidence Packet:
   - Identity
   - What changed
   - Product / Operator Evidence
   - Founder Dossier
   - On-chain / Market
   - Social Signal
   - Risks
   - Scores
   - AntSeed Peer Review
   - Stance
13. Final report:
   - Korean by default for the representative profile
   - address the client as "대표님" in the conclusion or operator notes when conversationally appropriate
   - conclusion first
   - focus on product, narrative, who said it, what is confirmed, unresolved issues, and stance
   - no hype, buy/sell language, targets, or investment advice
   - contract/LP/holder detail stays background unless fatal

## Stance Rules

- `TOP`: identity, product/operator evidence, token value-capture, founder/team evidence, and social/KOL confirmation are all strong and repeatable.
- `WATCH`: source-backed identity and product/narrative evidence exist, but founder, live KOL, token value-capture, or security evidence still needs follow-up.
- `OPERATOR`: product/infrastructure appears real, but token thesis is weak, indirect, or not required.
- `EXCLUDE`: identity collision, unofficial CA, no product, fatal token/security risk, or shill-only evidence.

## Output Contract

The report must explain the project, not the agent workflow.

The audit trail can mention where logs are stored, but final report body should prioritize:

```text
Conclusion -> Identity -> Product -> Narrative -> Social/KOL -> Founder -> Token value-capture -> Risks -> Score/Stance -> Sources
```

