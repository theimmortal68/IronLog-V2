## Routing Plan
Generated: 2026-07-08 · Updated: 2026-07-09 (opencode retired; specs 02/03 shipped)

- .specs/01-unit-hint-display.md → codex, worktree wt-01, depends on: none — **HELD at HUMAN GATE** (public API surface: adds `unit_hint` to `ExerciseOut`). Dispatch on user authorization only. Review gate: Opus (Agent subagent, model: opus).
- ~~.specs/02-load-ratchet.md~~ — MERGED to main 2026-07-09 (Tier A direct after dispatch-layer failure; Opus-reviewed).
- ~~.specs/03-ht-load-override-fix.md~~ — MERGED to main 2026-07-09 (Tier A direct after dispatch-layer failure; Opus-reviewed, HIGH finding fixed pre-merge).

Delegation ratio (remaining work): 1/1 → codex (100%)
Merge order: wt-01 standalone.

Notes:
- Provider policy as of 2026-07-09: ALL code generation → codex (bounded 1-3 files) or gemini (broad/cross-module). opencode retired. Mandatory merge gate = Opus review.
- Spec 01 has an out-of-repo client follow-on (IronLog-V2-Client render change) — spec it after 01 merges and is live.
