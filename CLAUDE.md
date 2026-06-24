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

| Layer | Status |
|---|---|
| Data model — library/state + session/set-log | **done** (SQLModel, `ironlog/models/`) |
| Engine — e1RM, loading math, between-set autoregulation, tier logic | **done + tested** (`ironlog/engine/`, 18 tests) |
| FastAPI surface — example routes | **done** (`ironlog/api/app.py`) |
| Validator — deterministic hard-rule checks | **not started — this is next** |
| Generation — LLM propose → validate → approve | **specified** (`docs/06`), stub only |
| Library seed — full 130-movement import | only 5 example movements seeded |

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

## Conventions / gotchas

- Python ≥3.10, SQLModel, FastAPI, pytest.
- **`engine/` is pure logic** — no DB, no network, no LLM. That's why it's testable.
  Keep it that way; new deterministic logic goes here with tests.
- **Do NOT add `from __future__ import annotations`** to any file with `Relationship(...)`.
  It stringifies the types and SQLAlchemy can't resolve them (this already bit us once).
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

## Next tasks (recommended build order)

Build the deterministic pieces first; they're fully testable and keep the LLM out of
the picture as long as possible.

1. **Validator** (`engine/validator.py`, pure logic + tests). Given a proposed `Session`,
   check the hard rules from `docs/06` §4: equipment in the active-phase manifest,
   loads within floor/cap, RPE within the `PhasePolicy` envelope, primaries as STRAIGHT
   groups first, knee-modality frequency, 2:1 pull:push, giant-set concurrency (≤3),
   HT under its clamp. Return structured violations and distinguish **clamp** (fixable)
   from **reject** (structural). No LLM.
2. **WeeklyLedger** — track knee frequency, pull:push ratio, per-pattern volume across a
   week so the cross-session rules are enforceable (`docs/06` §5).
3. **Analysis hook** — post-session: update e1RM (`engine/e1rm`), `MovementState`
   (tier, consecutive_ceiling, calibration_status), evaluate phase gates on `EngineState`.
4. **Generation loop** (`engine/generation.py`) — deterministic skeleton → LLM proposal
   (Anthropic messages API, tool-grounded: `getMovementState`, `getWeeklyLedger`,
   `getManifest`, `getRecentSignatures`) → validator → human approval. See `docs/06`.
5. **Full library import** — load all 130 movements from `docs/exercise_verification.xlsx`
   into `seed.py` (only 5 examples are seeded now). Equipment, floor/step, cap, and
   progression mode are already columns in that sheet.

## How to verify your work

`python -m ironlog.seed && pytest -q` should pass. For API changes, smoke-test with
`fastapi.testclient.TestClient`. New deterministic logic is not done until it has tests.
