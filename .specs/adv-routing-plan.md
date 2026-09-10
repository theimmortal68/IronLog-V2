## Routing Plan — Microcycle/Mesocycle Advancement (Revision 9)
Generated: 2026-09-05

- .specs/adv-01-schema-and-enums.md → codex, worktree wt-adv-01, depends on: none
- .specs/adv-02-program-hash-utilities.md → codex, worktree wt-adv-02, depends on: none
- .specs/adv-03-reconciler-core.md → codex, worktree wt-adv-03, depends on: adv-01, adv-02
- .specs/adv-04-generation-wiring-and-api-surface.md → gemini, worktree wt-adv-04, depends on: adv-01, adv-03
- .specs/adv-05-plan-next-mesocycle-script.md → gemini, worktree wt-adv-05, depends on: adv-01, adv-02, adv-03
- .specs/adv-06-acknowledge-program-drift-script.md → gemini, worktree wt-adv-06, depends on: adv-01, adv-02, adv-03
- .specs/adv-07-cutover-bootstrap-script.md → codex, worktree wt-adv-07, depends on: adv-01, adv-02, adv-03, adv-04

Delegation ratio: 7/7 (100%)

Merge order:
1. adv-01 and adv-02 — parallel-safe (no file overlap: adv-01 touches models/migrations, adv-02 touches a new engine module).
2. adv-03 — sequential after adv-01 + adv-02 merge (needs both).
3. adv-04, adv-05, adv-06 — parallel-safe once adv-03 merges (adv-04 touches generation/assembler.py + generation/context.py + api/app.py + api/schemas_periodization.py; adv-05 and adv-06 each touch only their own new script file — no file overlap among the three).
4. adv-07 — sequential, after adv-04 merges (needs the binding/resolution logic adv-04 builds, in addition to adv-01/02/03). Production-critical: dry-run only in this batch; `--apply` against the live DB is an explicit separate step after this spec's merge, requiring human confirmation per project convention.

Rationale for file-overlap avoidance: adv-04 is the only spec touching `ironlog/api/app.py` in this batch (both the write path — `submit_session` — and the read paths — `get_current_plan`/`get_macrocycle`); earlier drafts of this decomposition had a separate "read API" spec that also touched `app.py`, which would have been a same-file collision under /verify-plan's mechanical check, so they were merged into one spec instead of sequenced.
