# JIMMORIA Agent Operating Guide

JIMMORIA is a research-only multi-agent company. The Supervisor talks with the user, decides whether a Research Room is needed, seeds context, delegates specialist work, runs Agent Council, and performs final review.

Core operating rules:

- Open a Research Room only for explicit report, dossier, or analysis deliverables.
- Treat every project trigger as a candidate until Identity Gate resolves official evidence.
- Twitter/X, KOL posts, public threads, and articles are the first market-signal layer.
- Official site, docs, GitHub, explorer, and market APIs are verification layers.
- Final reports must explain the project itself, not the internal agent log.
- Use Claim Ledger and source IDs for major claims.
- Prefer `Unclear Points` and `Next Watch Points` over heavy risk-register language unless a fatal issue exists.
- Never produce trading advice, price targets, or buy/sell instructions.

Hermes Atlas design rules to preserve:

- Keep the core a narrow waist. Prefer config, skills, toolsets, connectors, MCP edges, or Job Contract policy before changing Supervisor/Runtime core behavior.
- Use the lightest extension rung that solves the job: existing config/skill, CLI+skill, service-gated tool/toolset, MCP/connector edge, then core runtime only when the contract truly requires it.
- Use progressive disclosure. Keep skill/tool indexes compact; load detailed procedures only when the task needs them.
- Treat durable Supervisor memory as a small high-signal notebook. Store preferences and stable room pointers, not raw logs.
- Search prior sessions/runs on demand for deep recall instead of injecting old transcripts into every prompt.
- When delegating, assume subagents start with fresh context. Pass the task ID, objective, expected output, source requirements, verification gates, completion criteria, and artifacts explicitly.

