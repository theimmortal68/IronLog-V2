# Task 10 Completion Report: Orchestrator + Conditional Gate + Endpoints

**Objective:** Wire the generate_session orchestrator (§3A conditional gate), add the run_analysis idempotency guard, and add three spine endpoints (log / generate / approve). Named gates b, e, f.

**Status:** COMPLETE — 206 passed (201 prior + 5 new). All three named gates confirmed green.

---

## Files Changed

| File | Change |
|------|--------|
| `ironlog/generation/loop.py` | Added `generate_session` with §3A conditional gate; added module-level imports for all consumed interfaces |
| `ironlog/persistence/run_analysis.py` | Added `already_analyzed(session_id, db) -> bool`; added early-return idempotency guard at top of `run_analysis` |
| `ironlog/api/app.py` | Added `POST /sessions/{id}/log`, `POST /generate`, `POST /sessions/{id}/approve` endpoints; added `_candidates` dict for candidate storage; added request/response models |
| `tests/conftest.py` | Added `logged_session_id` fixture (Pull-up PROGRESS lift, COMPLETED session, tapped working set); added `stalled_session_db` fixture (Pendlay Row consecutive_failed=2) |
| `tests/test_generation_loop.py` | New file — 5 tests covering gates b/e/f + end-to-end |

---

## Named Gate Confirmations

### GATE b — Analysis Idempotency
`test_analysis_idempotency_no_duplicate_history` PASSES.

Implementation: `already_analyzed(session_id, db)` checks for an `E1rmHistory` row for the session_id. Added guard at top of `run_analysis` (after the `.one()` so session-not-found still raises), returns empty `AnalysisResult()` if already analyzed. Re-running run_analysis on the same session_id does NOT append a duplicate E1rmHistory row; `n1 == n2 == 1`.

### GATE e — Two-Writer Boundary
`test_two_writer_boundary` PASSES.

`run_analysis` (and `apply_analysis`) never writes `current_load`. Before == after on all MovementState.current_load across the run_analysis call. The `logged_session_id` fixture seeds `current_load=0.0` for Pull-up; after run_analysis it remains 0.0. `commit_session` is the sole writer.

### GATE f — Conditional Invocation
Both `test_conditional_invocation_quiet_week_no_llm_call` and `test_conditional_invocation_signal_present_calls_llm` PASS.

**Quiet path (gen_db):** No MovementState stalls, no open Notes, no novelty_owed entries → `should_invoke_llm` returns False → `_CountingProposer.calls == 0`; program emitted deterministically.

**Signal path (stalled_session_db):** `consecutive_failed_progressions=2` on Pendlay Row - Narrow [OB] (d1_t2a, tier_role="semi") → `detect_stall` fires (failed_stalled=True) → movement_id in `weak_point_hints` → `slot_has_deviation_signal` True for d1_t2a → `should_invoke_llm` True → proposer called exactly once.

---

## §3A Conditional Gate Wiring (generate_session)

```
lay_skeleton(day_role, db)              → Skeleton
resolve_context(day_role, sk, db, wk)  → GenerationContext
should_invoke_llm(sk, ctx)?
  NO (quiet week / meso-1):
    assemble(program_selections(sk), sk, ctx, db)  → AssembledSession
    apply_clamps(...)                               [safety clamps]
    return RepairOutcome(assembled, attempts=0, exhausted=False)
    # proposer NEVER called (GATE f)
  YES (signal present):
    build_context_payload(ctx, sk)      → payload dict
    propose_validate_repair(proposer, payload, sk, ctx, db) → RepairOutcome
    if exhausted: fallback_session(sk, ctx, db)
    return outcome
```

---

## Idempotency Guard Detail

```python
def already_analyzed(session_id: int, db: DBSession) -> bool:
    return db.exec(
        select(E1rmHistory).where(E1rmHistory.session_id == session_id)
    ).first() is not None
```

Placed in `run_analysis` AFTER `workout = db.exec(...).one()` so session-not-found still raises. Returns empty `AnalysisResult()` as the no-op result.

Edge case documented: sessions with no qualifying anchor sets produce no E1rmHistory rows → already_analyzed returns False → run_analysis re-executes → harmless (nothing to double-write).

---

## Endpoints

| Route | Behavior |
|-------|----------|
| `POST /sessions/{session_id}/log` | Guard via `already_analyzed`; if False calls `run_analysis`; returns `{session_id, already_analyzed, message}` |
| `POST /generate` | Accepts `{day_role}`; calls `generate_session` with `StubProposer` (LLM adapter is Task 11); stores candidate in `_candidates[uuid]`; returns `{candidate_id, day_role, exhausted, attempts, scope}` where `scope="main-work-only; warmups/finishers/Z2 per program doc, not yet in-app"` |
| `POST /sessions/{candidate_id}/approve` | Looks up candidate from `_candidates`; calls `commit_session` (sole current_load writer); returns `{session_id}` |

Regenerate = call `/generate` again (new candidate_id, old one stays in `_candidates` until timeout or restart).

---

## Reconciliations vs Brief

- Brief fixture note says "add to `tests/_gen_fixtures.py`" — fixtures added to `conftest.py` instead (the real auto-discovery location; `_gen_fixtures.py` is already a stub redirecting to conftest).
- `clamps_applied` in the quiet-week path counts actual clamps from `apply_clamps` (not hardcoded 0 as in brief) — more correct.
- Module-level imports in `loop.py` preferred over inline function-body imports (same behavior, cleaner).
- `_candidates` uses UUID strings as keys; the path param `{candidate_id}` in `/sessions/{candidate_id}/approve` carries the UUID (FastAPI accepts string path params).

---

## Pytest Summary

```
206 passed, 73 warnings in 2.23s
```

New tests (5): test_end_to_end_with_stub_produces_valid_candidate, test_conditional_invocation_quiet_week_no_llm_call, test_conditional_invocation_signal_present_calls_llm, test_analysis_idempotency_no_duplicate_history, test_two_writer_boundary.

All 201 prior tests still pass — no regressions.

---

## Commit

feat(gen): orchestrator + conditional gate + log/generate/approve endpoints; gates b + e + f

---

## Task 10 fix wave

### Changes

| File | Change |
|------|--------|
| `ironlog/models/session.py` | Added `analyzed_at: Optional[datetime] = None` to `Session` |
| `deploy/migrations/006_session_analyzed_at.sql` | `ALTER TABLE session ADD COLUMN analyzed_at DATETIME;` (single-statement atomic) |
| `ironlog/persistence/run_analysis.py` | `already_analyzed`: changed from E1rmHistory row check to `session.analyzed_at is not None`; `run_analysis`: stamps `workout.analyzed_at = now` + `db.add(workout)` before `apply_analysis` so the marker is committed in the same transaction |
| `ironlog/generation/loop.py` | `is_clean`: `attempts == 1` → `attempts <= 1` (covers quiet-week attempts=0 path) |
| `tests/test_run_analysis.py` | Added `test_anchor_less_idempotency_no_counter_double_advance` + `test_analyzed_at_set_on_first_run_anchor_present` |
| `tests/test_generation_loop.py` | Added `test_is_clean_quiet_path_attempts_zero`, `test_is_clean_first_try_clean_still_true`, `test_is_clean_exhausted_or_fallback_is_never_clean` |
| `tests/test_migrations.py` | Updated chain comment to include 006 |

### analyzed_at marker + migration 006

The old `already_analyzed` checked for an `E1rmHistory` row keyed on `session_id`. This left anchor-less sessions (warmup-only, no qualifying tapped working set) unguarded: no E1rmHistory row is written for them, so `already_analyzed` returned `False` after the first run, allowing `run_analysis` to re-execute freely.

The fix uses `session.analyzed_at` as a per-session boolean marker:
- Set by `run_analysis` in the same `db.commit()` as `apply_analysis` (atomic: marker + MovementState deltas + any E1rmHistory rows all committed together).
- `already_analyzed` now loads the session and returns `session.analyzed_at is not None`.
- Covers ALL sessions — anchor-present and anchor-less — as a true no-op gate on any subsequent call.

Migration `006_session_analyzed_at.sql` adds the nullable `analyzed_at DATETIME` column to existing `session` rows (NULL = not yet analyzed, matching the default).

### Anchor-less double-advance test (failed-before / passes-after)

`test_anchor_less_idempotency_no_counter_double_advance`:
- Seeds a session with `is_warmup=True` sets only → `_best_e1rm_set` returns None → no E1rmHistory, no counter delta.
- Reads counter values before first run (pre-seeded at `consecutive_ceiling_sessions=2`, `consecutive_failed_progressions=1`).
- Calls `run_analysis` once, reads counters after (`after_first`).
- Asserts `already_analyzed(session_id, db) is True` ← **this assertion failed before the fix** (old guard returned False because no E1rmHistory row).
- Calls `run_analysis` again, reads counters after (`after_second`).
- Asserts `after_first == after_second` (counter invariant — passes both ways since anchor-less analysis produces no delta, but robustly documents the guarantee).

**Before fix:** `already_analyzed` returns False → test fails at the primary assertion.
**After fix:** `analyzed_at` is stamped on first run → `already_analyzed` returns True → all assertions pass.

### is_clean fix

`generate_session`'s quiet-week path returns `RepairOutcome(attempts=0, clamps_applied=0, exhausted=False, assembled=<session>)`. The old check `attempts == 1` returned False, so a pristine deterministic emission was treated as unclean. Fixed to `attempts <= 1`.

`test_is_clean_quiet_path_attempts_zero` **failed before the fix** (`is_clean` returned False for attempts=0) and passes after.

### Migration parity result

`test_chain_matches_create_all` PASSES — the `create_all` schema (with `analyzed_at` on `Session`) matches the 000→006 migration chain.

### Pytest tail

```
211 passed, 80 warnings in 2.30s
```

Prior count: 206. New tests: 5 (anchor-less idempotency, analyzed_at anchor-present, is_clean quiet-path, is_clean first-try, is_clean exhausted/fallback). 0 red. 0 regressions.
