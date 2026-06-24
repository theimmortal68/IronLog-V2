# IronLog V2

An adaptive strength-training engine. A thin client logs workouts; this server holds
the brain — the movement library, the loading rules, and (eventually) an agent that
generates each session from real-time feedback instead of repeating a fixed template.

This repo is the **server**, built in layers. The data model and the deterministic
engine logic are implemented and tested; the LLM-driven session generator is specified
and scaffolded but not yet built.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"        # installs the package + pytest/httpx

python -m ironlog.seed          # creates ironlog.db with the locked reference data
pytest -q                       # 18 tests, all deterministic engine logic
uvicorn ironlog.api.app:app --reload   # API at http://127.0.0.1:8000/docs
```

## Layout

```
ironlog/
  db.py                 SQLite engine + create_db_and_tables()
  seed.py               loads the locked reference data + example movements
  models/
    enums.py            every controlled vocabulary
    library.py          DEFINITION + STATE tables (Movement, Equipment, BandPair,
                        PhasePolicy, EngineState, MovementState)
    session.py          Session, ExerciseGroup, PlannedExercise, PlannedSet,
                        SetLog, ExerciseSurvey, Note, StickingPointTaxonomy
  engine/               pure, deterministic, unit-tested logic (no LLM, no DB)
    e1rm.py             e1RM from a submaximal set + tap
    loading.py          round-to-achievable load, cap clamp, ladder step
    autoregulate.py     between-set loop: tap -> next-set load
    progression.py      objective resolution, tier step-down/reset
    generation.py       between-session brain — INTERFACE ONLY (see docs/06)
  api/
    app.py              FastAPI surface (movements, phase policy, autoregulate, ...)
tests/                  pytest suite for the engine logic
docs/                   the six design specs + the verified movement sheet
```

## What's built vs. specified

| Layer | Status |
|---|---|
| Data model (library + state + session/set-log) | **implemented** (SQLModel) |
| e1RM, loading math, between-set autoregulation, tier logic | **implemented + tested** |
| FastAPI surface (example routes) | **implemented** |
| Validator (deterministic hard-rule checks) | next to build |
| Session generation (LLM propose → validate → approve) | **specified**, scaffolded stub |

## Design principles encoded here

- **Definition vs state.** What a lift *is* (its ladder, floor, scheme) is separate
  from what's true now (current load, calibration status, tier). The library stays
  stable while per-session numbers churn.
- **Planned vs logged.** Every set exists twice — prescribed and performed — and the
  delta is the signal that drives autoregulation and progression.
- **The capture fix.** `SetLog.feedback_tap` is the per-set signal (mandatory on
  working sets at the API layer) and `is_warmup` is a real column — never inferred
  from an exercise name, which is what broke V1.
- **Rules dispose, the model proposes.** Load math, floors, caps, and frequencies are
  deterministic code; the LLM only fills the adaptive layer and never touches what the
  rules already own.

## Specs (docs/)

1. HT composite-load — the plates+band hip-thrust model and the 220 bottom clamp
2. Calibration block — the two-week entry that turns inherited e1RMs into measured ones
3. Progression model — schemes, objectives, and phase policy (maintain vs progress)
4. Exercise-library schema — the library/state data model
5. Session / set-log schema — the capture layer and the per-set fix
6. Generation algorithm — the constrained session generator (the capstone)

`docs/exercise_verification.xlsx` is the verified 130-movement library these specs
were built from.

## Next

1. The **validator** — deterministic hard-rule checks (floors, caps, RPE envelope,
   knee frequency, pull:push, equipment manifest). Pure logic, fully testable.
2. The **session generation** loop on top of it.
