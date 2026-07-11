## Routing Plan
Generated: 2026-07-08 · Updated: 2026-07-10 (all specs shipped+deployed; batch complete)

- ~~.specs/01-unit-hint-display.md~~ — MERGED + LIVE 2026-07-09 (codex-generated, Tier A committed the verified diff after codex exited without committing; Opus-reviewed clean).
- ~~.specs/02-load-ratchet.md~~ — MERGED + LIVE 2026-07-09 (Tier A direct after dispatch-layer failure; Opus-reviewed).
- ~~.specs/03-ht-load-override-fix.md~~ — MERGED + LIVE 2026-07-09 (Tier A direct after dispatch-layer failure; Opus-reviewed, HIGH finding fixed pre-merge).
- ~~.specs/04-ramp-sets.md~~ — MERGED + LIVE 2026-07-10 (codex-generated; Opus-reviewed clean, one non-blocking Low noted).
- ~~.specs/05-finisher-emom.md~~ — ABANDONED as a single dispatch (branch renamed `abandoned/task-05-finisher-emom`): 2× gemini timeout (zero output) + 1× codex partial (correct but incomplete, 3 files, no commit) — too large for one generation pass. Decomposed into 05a/05b/05c.
- ~~.specs/05a-finisher-schema.md~~ — MERGED 2026-07-10 (codex-generated, Tier A committed after codex again exited uncommitted; Opus-reviewed clean).
- ~~.specs/05b-finisher-progression-rule.md~~ — MERGED 2026-07-10 (codex-generated, Tier A committed after codex again exited uncommitted; Opus-reviewed clean, two non-blocking Low notes: window=1-only test coverage, minor _ladder_step re-derivation).
- ~~.specs/05c-finisher-generation.md~~ — MERGED + LIVE 2026-07-10 (two codex dispatches: initial + a fix for a caught-by-tests `_serialize_session` signature mismatch, both left uncommitted, both committed by Tier A after verification; Opus-reviewed clean, two non-blocking Low notes: legacy-session degradation, rest-day signature imprecision — both cosmetic under current design).

All 8 specs in this batch (01-03 from the prior session, 04/05a/05b/05c from this one) are MERGED and LIVE as of 2026-07-10. Deploy required an extra step beyond the CLAUDE.md Deploy Gate's restart procedure: migrations 025/026 only added schema, so a new one-off idempotent seed script (`ironlog/generation/live_seed_ramp_and_finishers.py`) was written, tested against a DB copy, and run against production (human-gated per Class 2, explicit confirmation obtained) to actually populate the ramp-eligible flags and finisher rows.

## New spec (2026-07-10, post-deploy incident)
- .specs/06-fallback-replay-slot-identity.md → codex, worktree wt-06, depends on: none. Bounded (1 file + tests): codex per "bounded 1-3 files" guidance. Fixes a real bug found live: `last_valid_selections` zips current skeleton slots against a prior session's exercises by raw position, silently defeating any exercise-order or meso-rotation change once a deviation signal routes generation through the fallback/replay path. Confirmed on D4 post-reorder-deploy; self-resolved there only because confirming an open note happened to clear the deviation signal — the underlying bug is general and latent on any day.

(Meadows Row's +1.25lbs note was applied directly via the existing LOAD-override mechanism — no new code needed, same as the Hip Thrust case. Not a spec item.)

Notes for future batches:
- **Recurring pattern this session (6 occurrences across specs 04/05a/05b/05c, both providers): a worker produces correct, fully-tested work but exits without running `git commit`.** AGENT.md/GEMINI.md were already strengthened mid-session ("finished = committed" as a hard criterion) but the recurrence continued afterward — this may be a turn/token budget cutting workers off right after their test run, not a prompt-compliance issue. Worth investigating the underlying dispatch mechanism (consensus-mcp) rather than the instruction text.
- **Combined/large specs should be decomposed into single-dispatch-sized sub-specs up front**, not after a failed attempt — spec 05's original combined form cost 3 failed dispatch attempts before decomposition fixed it. When a spec touches more than ~3-4 files across schema + engine + API layers, split it at those natural seams before the first dispatch.
- **Schema migrations and their seed data are not the same deploy step.** A migration file only changes structure; if a feature also needs new rows (not just new columns), that's a separate, explicitly-gated data action — don't assume "the migration ran" means "the feature is live." This cost one round of a smoke-check catching stale data mid-deploy.
- Spec 01 has an out-of-repo client follow-on (IronLog-V2-Client render change) — not yet spec'd.
