## Routing Plan
Generated: 2026-09-16

- .specs/60-bootstrap-current-load-from-logged-performance.md → rung1 (codex, gpt-5.6-sol), worktree wt-60, depends on: none

Delegation ratio: 1/1 (100%)

Merge order: [60]

## Notes
- Single spec, single worktree — no decomposition needed (self-contained within
  `run_analysis.py`'s existing advance step + `advance.py`'s existing helper).
- Rung1 (not rung0) because this touches a shared progression/advancement invariant
  (`performed_floor_delta`, `pending_load_delta` staging) that other movements and modes
  (assisted, HT, bodyweight) also depend on — a wrong change here is easy to get subtly
  wrong (e.g. double-staging with `adv.earned_load_step`, or leaking into modes the
  existing guard already excludes), per Effort-Tier Routing's "conflicting worker
  results / difficult integration" escalation criteria, applied here to generation
  dispatch rather than orchestration.
- Review: default to Fable review (not the mechanical-exemption path) per the Review
  Gate — this is non-trivial logic touching a stated invariant (the "never regress below
  performed weight" ratchet), exactly the category the Review Gate calls out for
  mandatory review regardless of test-suite pass.
- No live/production action is part of this spec — the three already-affected movements
  (Kickstand RDL [PB], Lying Leg Curl [GHR + Ares], Matrix Machine Bulgarian Split Squat)
  were already hand-patched directly against the production DB on 2026-09-15/16 as an
  out-of-band stopgap; this spec only prevents recurrence for movements hit in the
  future.

## Dispatch history

- **2026-09-16, attempt 1** (`task/60`, later renamed `abandoned/task-60-v1-stale-marker-bug`):
  dispatched to rung1 (codex, gpt-5.6-sol). Worker delivered a clean, in-scope,
  fully-passing diff (853/853) implementing the v1 spec faithfully. **Fable review
  REJECTed it**: the spec itself had a gap — the staged bootstrap `pending_load_delta`
  had no consumption path (assembler refuses to touch needs-calibration movements at
  all), making the fix inert for its own target bug, AND a Critical stale-marker
  corruption path via `/wizard-resolve` (empirically reproduced by Fable: log 265 →
  stage marker → wizard-resolve sets 100 → next prescription = 365). This was a
  **spec defect, not a worker error** — re-dispatching the same spec would have
  reproduced the same result. Spec revised to v2 (see the spec file's own "REVISED v2"
  header) per user decisions: assembler consumes the staged bootstrap directly (not a
  new write path, not wizard-prefill-only), and `/wizard-resolve` clears
  `pending_load_delta` when it sets a load. Not logged as a capability escalation —
  same alias, corrected task specification (per CLAUDE.md's retry-logging rule).
- **2026-09-16, attempt 2** (`task/60` v2): dispatched to rung1 (codex, gpt-5.6-sol),
  same alias as attempt 1 — the failure was spec-shaped, not a signal the model
  couldn't handle the work.
