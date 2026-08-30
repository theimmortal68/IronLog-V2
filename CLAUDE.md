# CLAUDE.md — context for Claude Code

You are continuing an in-progress build. Read this first, then `docs/` for depth.
The design is deliberate and mostly settled — **prefer extending it over redesigning it.**

## What this is

IronLog V2: an adaptive strength-training engine. A thin mobile client logs workouts;
this server is the brain. The goal is to replace fixed templates with per-session
generation that adapts to real-time feedback, while a deterministic core guarantees it
never drifts from the user's periodization.

The user is a 47-year-old home-gym lifter in the final stages of a cut. Full domain
context lives in the specs; you rarely need it to write code, but when a decision
depends on it, `docs/` is the source of truth.

## Current state

**This table was stale for 2+ weeks (last updated 2026-06-24, HEAD was 15+ commits ahead) — corrected 2026-07-08. See `docs/build-plan.md` for the live, current punch-list; keep THAT updated, not just this table, since this table has drifted before.**

| Layer | Status |
|---|---|
| Data model — library/state + session/set-log | **done** (SQLModel, `ironlog/models/`) |
| Engine — e1RM, loading math, between-set autoregulation, tier logic | **done + tested** (`ironlog/engine/`) |
| Validator — deterministic hard-rule checks | **done + live** (`ironlog/engine/validator.py`, `tests/test_validator.py` + `test_ht_validator_config.py`) |
| Generation — LLM propose → validate → approve | **done + live** (`ironlog/engine/generation.py`), running on Gemini flash-lite behind a swappable proposer port |
| Library seed | **done** — full movement library + D1-D6 program reconciled to authoritative YAML, calibrated baselines seeded, live on server |
| Progression engine | **done + live** (2026-07-06 go-live) — `progression_rule` wired from YAML, advance→load bridge ratchets `current_load` on a clean top-of-range RPE-8 session |
| In-gym logging round-trip (client↔server) | **done + live**, athlete has trained real sessions on it (Day 1-2 feedback already being triaged) |
| Full test suite | **472 passing** (`.venv/bin/pytest -q` on myflix) |
| **Current focus** | Real-athlete feedback triage + client polish — see `docs/build-plan.md` "Queued", not a from-scratch build task |

## Commands

```bash
pip install -e ".[dev]"
python -m ironlog.seed                  # creates ironlog.db (idempotent; delete db to reseed)
pytest -q                               # all engine logic; keep this green
uvicorn ironlog.api.app:app --reload
```
Always run `pytest -q` after changing engine logic.

## Architecture invariants — DO NOT violate these

These are the spine of the design. Breaking one silently corrupts behavior.

1. **Rules dispose; the model proposes.** All load math, floors, caps, RPE envelopes,
   and frequencies are deterministic code. The LLM (in generation) only fills the
   *adaptive layer* — accessory selection, ordering, variant choice. It must NEVER
   compute a load, override a cap, or decide a frequency. The validator is 100%
   deterministic.
2. **Definition vs State.** Static facts about a lift live on `Movement`; anything that
   changes over time lives on `MovementState`. Do not add mutable state to `Movement`.
3. **Planned vs Logged.** `PlannedSet` (prescribed) and `SetLog` (performed) are
   separate on purpose — their delta is the training signal. Never collapse them.
4. **The capture fix (the reason V2 exists).** `SetLog.feedback_tap` is mandatory on
   working sets (enforce at the API layer). `is_warmup` is a real column. NEVER infer
   warmup status from an exercise name, and never make per-set feedback optional.
5. **Objective gating.** A movement's objective = `objective_override` or the phase
   default. Stall/weak-point logic fires ONLY when objective == PROGRESS. A *maintained*
   lift that goes flat is succeeding — do not add load or trigger weak-point work for it.
6. **Locked reference data.** Equipment floors, the HT band table, phase policies, caps
   (Landmine 25, Rev Hyper 180, Light Rev Hyper 90) are settled. Seed them; don't invent
   or "improve" them. If a number seems wrong, check `docs/` before changing it.

## Client contract — there is an external consumer

A separate **Android thin client** (repo `IronLog-V2-Client`, Kotlin/Compose) consumes
this server's HTTP API over the home LAN. Its DTOs mirror this server's JSON shapes, so
the API is a **shared contract**, not a private interface.

- If you change an endpoint path, request body, or response shape — or rename/remove/retype
  a field on `Movement`, `BandPair`, `PhasePolicy`, or the autoregulate request/response —
  that is a **breaking change for the client**. Call it out explicitly in your change
  summary so the client's `Models.kt` DTOs get updated in lockstep.
- Keep response field names **snake_case** and stable (the client deserializes without
  `@SerialName`). Adding new fields is safe (the client ignores unknown keys); renaming or
  removing is not.
- **Autoregulation is LADDER-only.** The client filters its autoregulate picker to
  `progression_mode == LADDER`. If you extend `next_set_load` to handle COMPOSITE (HT) or
  ASSISTED movements, say so — the client picker filter must change too.
- The server must run with `uvicorn ironlog.api.app:app --host 0.0.0.0` for the phone to
  reach it; the default (localhost) is invisible on the LAN.

## Conventions / gotchas

- Python ≥3.10, SQLModel, FastAPI, pytest.
- **`engine/` is pure logic** — no DB, no network, no LLM. That's why it's testable.
  Keep it that way; new deterministic logic goes here with tests.
- **Do NOT add `from __future__ import annotations`** to any file with `Relationship(...)`.
  It stringifies the types and SQLAlchemy can't resolve them (this already bit us once).
- **`ironlog.db` in this checkout is the SAME FILE as the live production DB** whenever this
  repo is opened via the NFS mount from a workstation (`/home/jstout/projects` ->
  `192.168.1.7:/mnt/appdata/projects`, confirmed via `findmnt`). There is no separate local
  test copy by default. **NEVER run `rm -f ironlog.db && python -m ironlog.seed`** (or any
  reseed) against this path to "verify a change" — it deletes and replaces the athlete's
  live session history in place, while `ironlogv2.service` may be actively serving requests
  against it. This happened for real on 2026-08-29 mid-workout (500 error, `attempt to write
  a readonly database`, then a full session-history wipe down to bare library tables) and
  required restoring from the nightly `backup-appdata` snapshot plus manually replaying the
  lost session through the live API. **To test seed/program changes safely: copy the file to
  a scratch path first** (`cp ironlog.db /tmp/test.db`, point a throwaway `create_engine()`
  at that instead), or run the test suite's own fixtures (they use in-memory/isolated DBs,
  not this file). **To ship an already-verified data/config change to production: write a
  `deploy/migrations/NNN_*.sql` file and run it directly against the live DB** (see
  `043_flat_2_5_increment_ladders.sql` for the pattern) — do not reseed to deploy.
- Lists on models (`increment_ladder`, `equipment_tags`) are JSON columns.
- Enums are `str, Enum` in `models/enums.py`. Add new vocabulary there, not as bare strings.
- When you change *behavior*, update the relevant spec in `docs/` in the same change —
  the specs are the source of truth, not just notes.

## Source of truth — docs/

1. `01_ht_composite_spec.md` — plates+band hip thrust, the 220 bottom clamp, stretch cap
2. `02_calibration_block_spec.md` — 2-week entry; inherited e1RMs → measured
3. `03_progression_model_spec.md` — schemes, objectives, phase policy (maintain vs progress)
4. `04_exercise_library_schema.md` — the library/state model
5. `05_session_setlog_schema.md` — the capture layer
6. `06_generation_algorithm_spec.md` — the generator (your main upcoming target)
- `exercise_verification.xlsx` — the verified 130-movement library to import

## Next tasks

**The deterministic-spine build order that used to live here (validator → WeeklyLedger →
analysis hook → generation loop → full library import) is DONE.** All five shipped and are
live on the server, tested (472 passing). Do not treat this as remaining work.

**The actual current punch-list lives in `docs/build-plan.md`** — read it, not this section,
for what's next. As of 2026-07-08 it's real-athlete feedback triage (a client display bug
showing lb instead of degrees for assist/incline movements; a load-ratchet gap where the
engine under-prescribes below what the athlete actually performed off-script; warmup/finisher/
rest-timer features; a larger "AI acts on programming notes" design item) — not new
deterministic-core work. Keep `docs/build-plan.md` current as things ship; this section
intentionally stays generic so it doesn't go stale the way the old numbered list did.

## How to verify your work

`python -m ironlog.seed && pytest -q` should pass. For API changes, smoke-test with
`fastapi.testclient.TestClient`. New deterministic logic is not done until it has tests.
