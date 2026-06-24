---
name: report-writing
description: Turn specialist evidence into a Korean-first project intelligence report.
owner_agents:
  - report_agent
  - supervisor_agent
---

# Report Writing Skill

## Goal

Write an owner-facing Korean-first project intelligence report that explains what the project is, why it matters, what is verified, what is unclear, and what to watch next.

## Structure

1. Conclusion and stance first.
2. Project identity.
3. Market signal and who said what.
4. Product/protocol mechanics.
5. Token/contract/value-capture.
6. Team/funding/founder dossier.
7. Unclear Points and Next Watch Points.
8. Score breakdown and stance.
9. Claim Ledger.
10. Source appendix.

## Output

- representative_verdict
- executive_summary
- project_report
- claim_ledger
- unclear_points
- next_watch_points
- evidence_packet

## Guardrails

- Do not write raw agent logs into the report body.
- Links support the interpretation; they do not replace content.
- Use `Unclear Points` instead of a heavy Risk Register unless a fatal issue exists.
- No hype, buy/sell language, price targets, or guaranteed-return phrasing.

