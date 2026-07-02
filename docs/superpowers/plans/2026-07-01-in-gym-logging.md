# In-Gym Logging UX + Prescription Fidelity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make D1 faithfully loggable in the gym — correct rep/RPE/rest prescription (server) and correct capture UX: giant-set rotation, pre-filled weights/reps, and a rest timer (client) — then re-test on the phone.

**Architecture:** Server phase first (seed reconciliation + assembler fidelity), built-and-tested-stable, because the session-graph DTO is the crossing artifact the client renders. Then the client phase (capture sequencing + display + rest timer). The DTO already carries `rest_seconds` / `target_reps_low/high` / `target_rpe`; this plan *populates* them and adds one field (`unilateral`).

**Tech Stack:** Server Python/FastAPI/SQLModel (tests on myflix via ssh). Client Kotlin/Compose (gradlew on workstation, adb install to phone).

## Global Constraints

- NO `from __future__ import annotations` (server).
- BUILD-AND-TEST-ONLY: server tests on myflix `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'`; live DB reseedable pre-launch (NO backup until Phase-1 launch + real logging).
- Migration rule: single-statement-atomic or idempotent + parity keystone `tests/test_migrations.py::test_chain_matches_create_all`.
- Two-writer boundary: this chunk is read/display + seed data only — never write `current_load` (progression) or outcome fields.
- No double-progression: assembler uses `current_load` as-is; no top-of-range load bump.
- Baseline: server 277 tests (`feat/in-gym-logging` off main @ e7718ea). Client: workstation `gradlew :app:assembleDebug`; `SERVER_BASE_URL=http://192.168.1.7:8000` is local-uncommitted.

## The DTO crossing artifact (server produces, client consumes)

`GET /sessions/{id}` → `GroupOut{group_type, rounds, rest_seconds, exercises}` → `ExerciseOut{..., unilateral (NEW)}` → `PlannedSetOut{target_load, target_reps_low, target_reps_high, target_rpe, set_role}`. Server tasks 1–3 populate all of these; client tasks 4–6 render them. **Server phase must be green before client tasks start.**

## Tier → rest_seconds map (Fork-2 + doc, for Task 2)

| tier_label | rest_seconds |
|---|---|
| `T1`, `T1b` | 120 |
| `T2 GS`, `GS1`, `GS2` | 90 |
| `T3`, `T3 GS`, `T4 GS`, `GS3` | 60 |

## Rep reconciliation table (for Task 2 — seed each TE literally)

Singles → `rep_low=rep_high=n`; ranges → `rep_low/rep_high`. Full mapping in the spec (`docs/superpowers/specs/2026-07-01-in-gym-logging-design.md`, S1). The implementer reconciles against the actual seeded TEs by slot.

---

## Task 1 — `unilateral` field + migration (server)

**Files:**
- Modify: `ironlog/models/library.py` (Movement: add `unilateral`)
- Create: `deploy/migrations/012_add_movement_unilateral.sql`
- Test: `tests/test_migrations.py` (parity), `tests/test_library_unilateral.py` (new)

**Interfaces — Produces:** `Movement.unilateral: bool` (default False).

- [ ] **Step 1: Write the failing test** — `tests/test_library_unilateral.py`

```python
from ironlog.models.library import Movement


def test_movement_has_unilateral_defaulting_false():
    m = Movement(name="X [DB]", base_name="X")
    assert m.unilateral is False
```

- [ ] **Step 2: Run — expect FAIL** `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_library_unilateral.py -q'` (AttributeError).

- [ ] **Step 3: Add the field** to `Movement` in `ironlog/models/library.py` (beside `is_primary`):

```python
    unilateral: bool = Field(default=False, sa_column_kwargs={"server_default": text("0")})
```

(Import `text` from sqlalchemy if not present; mirror the `consecutive_failed_progressions` server_default pattern already in the repo.)

- [ ] **Step 4: Migration** `deploy/migrations/012_add_movement_unilateral.sql`:

```sql
ALTER TABLE movement ADD COLUMN unilateral BOOLEAN NOT NULL DEFAULT 0;
```

- [ ] **Step 5: Run parity + new test** `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_migrations.py tests/test_library_unilateral.py -q'` — expect PASS. If parity fails on type, align the SQL type (`BOOLEAN`/`INTEGER`) with what SQLModel emits.

- [ ] **Step 6: Full suite + commit**

```bash
ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q 2>&1 | tail -3'
git add ironlog/models/library.py deploy/migrations/012_add_movement_unilateral.sql tests/test_library_unilateral.py tests/test_migrations.py
git commit -m "feat(library): add Movement.unilateral field (migration 012)"
```

---

## Task 2 — Seed reconciliation (server data)

**Files:**
- Modify: `ironlog/seed.py` (MOVEMENTS: `unilateral`, `scheme` for Belt Squat/RDL), `ironlog/generation/program_seed.py` (TE `rep_low/rep_high`, `rpe_cap`; Tier `rest_seconds`)
- Create: `scripts/reconcile_phase1.py` (one-off: applies the reconciliation to the live DB), `deploy/migrations/013_phase1_reconciliation.sql` (idempotent, generated)
- Test: `tests/test_phase1_reconciliation.py`

**Interfaces — Consumes:** `Movement.unilateral` (Task 1). **Produces:** TEs with literal `rep_low/high`, Tiers with `rest_seconds`, Belt Squat/RDL `Movement.scheme=STRAIGHT`, RevHyper-Recovery TE `rpe_cap=6`, unilateral movements flagged.

- [ ] **Step 1: Write the failing test** — `tests/test_phase1_reconciliation.py` (runs against a freshly-seeded in-memory DB)

```python
from sqlmodel import Session, select
from ironlog.models.library import Movement
from ironlog.models.program import Tier, TierExercise
from ironlog.models.enums import Scheme
# fixture `seeded_db` = create_all + seed() + seed_phase1_program() (reuse existing conftest seeding)


def test_rep_targets_reconciled(seeded_db):
    tes = {te.slot_id: te for te in seeded_db.exec(select(TierExercise)).all()}
    assert (tes["d1_t1"].rep_low, tes["d1_t1"].rep_high) == (8, 8)        # Bench 3x8
    assert (tes["d1_t2b"].rep_low, tes["d1_t2b"].rep_high) == (10, 10)    # Incline DB 3x10
    assert (tes["d1_t2c"].rep_low, tes["d1_t2c"].rep_high) == (15, 15)    # Face-Up Knee (D1) 3x15


def test_tier_rests_seeded(seeded_db):
    by = {}
    for t in seeded_db.exec(select(Tier)).all():
        by.setdefault(t.tier_label, t.rest_seconds)
    assert by["T1"] == 120 and by["T2 GS"] == 90 and by["T3 GS"] == 60


def test_schemes_straight(seeded_db):
    names = {m.name: m for m in seeded_db.exec(select(Movement)).all()}
    assert names["Belt Squat [GHR + FT]"].scheme == Scheme.STRAIGHT
    assert names["RDL [PB]"].scheme == Scheme.STRAIGHT


def test_unilateral_flags(seeded_db):
    names = {m.name: m for m in seeded_db.exec(select(Movement)).all()}
    assert names["Meadows Row [OB + LM]"].unilateral is True
    assert names["Bench Press [PB]"].unilateral is False
```

- [ ] **Step 2: Run — expect FAIL** `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_phase1_reconciliation.py -q'`.

- [ ] **Step 3: Reconcile the seed source.** In `ironlog/seed.py` MOVEMENTS: set `"unilateral": True` on Meadows Row, Bulgarian Split Squat, ATG Split Squat, Cross-Body Cable Rear Delt Fly, Cross-Body Cable Lateral Raise, Single-Arm DB Row, Poliquin Step-up, Staggered RDL; set `scheme=STRAIGHT` on Belt Squat + RDL. In `ironlog/generation/program_seed.py`: set each TE's `rep_low/rep_high` per the rep table; set `rpe_cap=6` on the D6 Reverse-Hyper-Recovery TE (Light Reverse Hyper); pass `rest_seconds` to each `_tier(...)` per the Tier→rest map. Wire the loader to persist `unilateral`.

- [ ] **Step 4: Run the reconciliation test — expect PASS.** `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_phase1_reconciliation.py -q'`.

- [ ] **Step 5: Apply to the live DB** (reseedable pre-launch, no backup). `scripts/reconcile_phase1.py` updates the live rows to match (idempotent UPDATEs: TE rep_low/high/rpe_cap, Tier rest_seconds, Movement scheme/unilateral) and also writes `deploy/migrations/013_phase1_reconciliation.sql` (guarded UPDATEs). Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/python scripts/reconcile_phase1.py'`.

- [ ] **Step 6: Parity + full suite + commit**

```bash
ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_migrations.py -q && .venv/bin/pytest -q 2>&1 | tail -3'
git add ironlog/seed.py ironlog/generation/program_seed.py scripts/reconcile_phase1.py deploy/migrations/013_phase1_reconciliation.sql tests/test_phase1_reconciliation.py
git commit -m "feat(seed): Phase-1 reconciliation — literal rep targets, tier rests, straight schemes, unilateral flags, RevHyper rpe_cap"
```

---

## Task 3 — Assembler fidelity (server)

**Files:**
- Modify: `ironlog/generation/assembler.py` (`_sets_for_scheme`, `_build_exercise`, group build), `ironlog/api/app.py` (session serialization: add `unilateral` to the exercise DTO)
- Test: `tests/test_assembler_fidelity.py`

**Interfaces — Consumes:** reconciled seed (Task 2). **Produces:** assembled sessions whose `PlannedSet.target_reps_low/high` = the TE's, `target_rpe` = TE `rpe_cap` (default 8), `ExerciseGroup.rest_seconds` = the Tier's, and per-exercise `unilateral` in the `/sessions/{id}` graph.

- [ ] **Step 1: Write the failing test** — `tests/test_assembler_fidelity.py`

```python
# fixture gen_db = seeded + reconciled. Assemble D1 and inspect.
def test_reps_from_tier_exercise(gen_db):
    from ironlog.generation.skeleton import lay_skeleton
    from ironlog.generation.loop import generate_session
    from ironlog.api.app import _make_proposer, _week_keyer
    sk = lay_skeleton("D1 Upper Push", gen_db)
    out = generate_session("D1 Upper Push", gen_db, _make_proposer(sk), _week_keyer)
    # Bench (d1_t1) is 3x8 → every working set target_reps_low==high==8
    sess = out.assembled.session
    # locate bench sets via the assembled graph; assert reps 8/8 and rest on the group
    # (assembler exposes groups/exercises/sets; adapt to the real accessor)
    assert out.exhausted is False


def test_rest_seconds_propagated(gen_db):
    ... # T1 group rest_seconds == 120; a T2 GS group == 90
```

(The implementer adapts the accessors to the real assembled structure — `out.assembled` holds `session` + `prospective_current_loads`; the group/exercise/set graph is built at approve, so assert via a helper that walks the assembler's intermediate structures, or assert on `/sessions/{id}` after an in-test approve.)

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement.** In `_sets_for_scheme`, accept `rep_low, rep_high, rpe_cap` (thread them from `_build_exercise`, which has the TierExercise) and use them for `target_reps_low/high` and `target_rpe` (fallback `rpe_cap or 8.0`) instead of the hardcoded 8-12/3-5. In the group build, copy `tier.rest_seconds` → `ExerciseGroup.rest_seconds`. In `ironlog/api/app.py` session serialization, add `unilateral=movement.unilateral` to the exercise output.

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Full suite + commit**

```bash
ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q 2>&1 | tail -3'
git add ironlog/generation/assembler.py ironlog/api/app.py tests/test_assembler_fidelity.py
git commit -m "feat(gen): assembler honors seeded reps/rpe_cap + propagates tier rest + surfaces unilateral"
```

**→ Server phase complete. Regen-check (Tier A) before client tasks:** regenerate D1 server-side and confirm the `/sessions/{id}` graph shows reps 8/8 for Bench, rest_seconds 120/90/60 per tier, `unilateral` flags, no 148.5.

---

## Task 4 — Giant-set round-major sequencing (client)

**Files:**
- Modify: `app/src/main/java/com/jauschua/ironlogv2/ui/screens/capture/CaptureViewModel.kt`
- Modify: `app/src/main/java/com/jauschua/ironlogv2/data/api/dto/CaptureModels.kt` (add `unilateral` to `ExerciseOut`)
- Test: the existing capture VM test file (extend with round-major + unilateral-unit cases)

**Interfaces — Consumes:** `GroupOut{group_type, rounds}`, `ExerciseOut{unilateral, planned_sets}`. **Produces:** `flattenedPrescription` ordered round-major for GIANT_SET.

- [ ] **Step 1: Write the failing test** — a giant-set group (3 exercises × 3 sets) flattens to round-major order: `[e1s1, e2s1, e3s1, e1s2, e2s2, e3s2, e1s3, e2s3, e3s3]` (by `PlannedSetOut.id`), and a STRAIGHT group stays exercise-major. Add a case: a unilateral exercise's set is one cursor unit (both sides logged before the cursor advances to the next exercise).

- [ ] **Step 2: Run — expect FAIL** (current flatten is exercise-major).

- [ ] **Step 3: Implement.** Replace the flatten (currently `groups.flatMap { g -> g.exercises.flatMap { e -> e.planned_sets } }`) with per-group logic:

```kotlin
fun flatten(groups: List<GroupOut>): List<PlannedSetOut> = groups.flatMap { g ->
    if (g.group_type == "GIANT_SET") {
        // round-major: for each round index, one set from each exercise
        (0 until g.rounds).flatMap { r ->
            g.exercises.mapNotNull { e -> e.planned_sets.getOrNull(r) }
        }
    } else {
        g.exercises.flatMap { e -> e.planned_sets }   // STRAIGHT: exercise-major
    }
}
```

Add `val unilateral: Boolean = false` to `ExerciseOut`. Unilateral "set = both sides": keep the cursor on the current `PlannedSetOut` until both sides are logged (track a per-set side counter in the VM; the cursor advances only after side 2). Document that a unilateral exercise's `planned_sets[r]` is one cursor entry covering L+R.

- [ ] **Step 4: Run — expect PASS.** (`~/projects/IronLog-V2-Client/gradlew :app:testDebugUnitTest --tests '*Capture*'`)

- [ ] **Step 5: Commit** `git add ... && git commit -m "fix(capture): round-major giant-set sequencing + unilateral set-unit"`

---

## Task 5 — Auto-populate / display (client)

**Files:** Modify `CaptureScreen.kt` (+ VM if needed). Test: capture screen/VM test.

**Interfaces — Consumes:** `PlannedSetOut{target_load, target_reps_low, target_reps_high, target_rpe}`, `ExerciseOut{unilateral, assist_level?}`.

- [ ] **Step 1: Failing test** — the per-set input pre-fills weight = `target_load`; reps display = single number when `target_reps_low == target_reps_high`, else `"low-high"`; the `target_rpe` is shown; a unilateral exercise renders a "per side" label.

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement.** Weight field default-value = `target_load` (editable). Reps: `if (low == high) "$low" else "$low-$high"`, with `target_rpe` shown prominently (label "RPE $rpe"). Unilateral → "per side" affordance. Assisted (assist_level present) → show assist value + reps. Phased pull-up (D4/D6) minimal: Set 1 blank rep field (AMRAP), Sets 2-3 an `{unassisted, assisted}` pair input — no rich widget.

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit** `git commit -m "feat(capture): pre-fill weight/reps/RPE, fixed-vs-range display, unilateral + assisted"`

---

## Task 6 — Rest timer (client)

**Files:** Create `.../capture/RestTimer.kt` (duration logic + composable); modify `CaptureScreen.kt`/VM to trigger it. Test: `RestTimer` duration-mapping unit test.

**Interfaces — Consumes:** the just-logged set's `feedback_tap` (TOO_EASY/ON_TARGET/TOO_HARD), the group's `rest_seconds`, `tier_label`/tier context, giant-vs-straight.

- [ ] **Step 1: Failing test** — pure duration function:

```kotlin
// restSeconds(baseRest, tierLabel, tap, isGiantSet) -> Int
assertEquals(90,  restSeconds(120, "T1", Tap.TOO_EASY, false))   // T1 adaptive 0.75
assertEquals(120, restSeconds(120, "T1", Tap.ON_TARGET, false))  // 1.0
assertEquals(180, restSeconds(120, "T1", Tap.TOO_HARD, false))   // 1.5
assertEquals(90,  restSeconds(90,  "T2 GS", Tap.TOO_HARD, true)) // fixed (not T1) → base
assertEquals(60,  restSeconds(60,  "T3 GS", Tap.TOO_EASY, true)) // fixed
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement** `restSeconds`: if `tierLabel == "T1"` (or `T1b`), multiply base by {TOO_EASY 0.75, ON_TARGET 1.0, TOO_HARD 1.5} (round to int); else return base unchanged. Trigger: on set-log for STRAIGHT, on the round's last item for GIANT_SET; use the round's hardest tap only if you later make giant sets adaptive (they're fixed now, so tap is ignored for them). Composable: skippable countdown with +30s.

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit** `git commit -m "feat(capture): RPE-adaptive T1 rest timer + fixed T2-T4"`

---

## Task 7 — Verification + deploy (Tier A)

- [ ] Server pytest green on myflix.
- [ ] Client: `~/projects/IronLog-V2-Client/gradlew :app:assembleDebug` + `adb -s 192.168.1.17:34509 install -r app/build/outputs/apk/debug/app-debug.apk`.
- [ ] Regenerate D1 server-side (wipe stray test sessions, generate + approve).
- [ ] **Phone re-test checklist:** Bench shows **3×8** (no 148.5); giant sets **rotate** ex→ex per round; weight/reps/RPE **pre-fill** (fixed single + RPE, ranges as range, unilateral per-side); **rest timer** fires with correct durations (T1 adapts to the logged tap; T2-T4 fixed 90/60/60).

---

## Routing Plan

| Task | Worker | Repo |
|------|--------|------|
| 1 unilateral field + migration | Claude subagent | server |
| 2 seed reconciliation | Claude subagent | server |
| 3 assembler fidelity | Claude subagent | server |
| 4 giant-set sequencing | Claude subagent | client |
| 5 auto-populate/display | Claude subagent | client |
| 6 rest timer | Claude subagent | client |
| 7 verification + deploy | Tier A | both |

**Delegation ratio: 6/7 (~86%)** to subagents; Tier A does verification + deploy + the regen-check gate between server and client phases. Codex is read-only (can't apply/test) — Claude Code Agent subagents are the implementers.

## Notes
- **Server phase (1–3) fully green + regen-checked before client (4–6)** — the DTO is the crossing artifact.
- Migrations 012 (unilateral) + 013 (reconciliation) deploy to the live DB in-task (reseedable pre-launch, no backup).
- Client `SERVER_BASE_URL=192.168.1.7:8000` stays local-uncommitted.
