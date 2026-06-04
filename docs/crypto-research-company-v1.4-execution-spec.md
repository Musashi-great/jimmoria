# Crypto Research Company v1.4 Execution Spec

Archived execution design note.

The current source of truth is:

```text
docs/jimmoria-project-structure.md
```

Current implementation policy:

- CLI and local web dashboard are the active interfaces.
- JIMMORIA is public-web first.
- Telegram Bot and Discord Bot are not part of the current default research stack.
- Supervisor decides whether a Research Room is needed before reports are created.
- Phase 2 is active: the front half remains ordered, then Social / Contract / Product / Funding evidence checks run as a bounded parallel group.
