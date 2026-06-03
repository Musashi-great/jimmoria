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
- Phase 1 is sequential; Phase 2-4 parallelization is described in `config/concurrency.yaml`.

