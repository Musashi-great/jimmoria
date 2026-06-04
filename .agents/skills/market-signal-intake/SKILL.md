---
name: market-signal-intake
description: Collect X/KOL/public-web signal as the first research layer.
owner_agents:
  - social_kol_agent
  - discovery_agent
---

# Market Signal Intake Skill

## Goal

Start with public market signal: who is talking about the project, what they claim, and whether the signal is official, KOL, article, or noisy search result.

## Steps

1. Build X/Twitter, public web, article, and KOL queries.
2. Prefer official X profile and project posts when available.
3. Extract who-said-what rows with speaker, claim, URL, date if available, and signal type.
4. Separate official posts, KOL opinions, media articles, and community chatter.
5. Hand signal rows to Discovery and Report without treating hype as truth.

## Output

- official_social_sources
- who_said_what
- kol_opinion_results
- article_results
- public_x_results
- social_unclear_points

## Guardrails

- Social signal is a trigger layer, not final proof.
- Do not invent KOL names or opinions.
- If X API secrets are missing, use public web fallback and label the limitation.

