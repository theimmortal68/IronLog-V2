# Session / Set-Log / Capture Layer (V2 Engine — Data Model Spec)

The second half of the data model: how a workout is **prescribed**, **performed**, and **captured**. Sits on top of the library/state layer and feeds the analysis that updates `MovementState` and `EngineState`.

**The bug this fixes.** V1 wrote `rpe = null` on every set and only patched the *last* set if you happened to tap a wheel, and it inferred warmups by string-matching "Warmup" in the exercise name. Both made autoregulation impossible. Here the per-set signal is a **mandatory, typed field on every working set**, and `is_warmup` is a **real boolean**, not a name match.

**Core principle — Planned vs Logged.** Every set exists twice: as a `PlannedSet` (what the engine prescribed) and a `SetLog` (what actually happened). The **delta between them is the signal** — hit / exceeded / missed — and that delta is what drives autoregulation, progression, and stall detection. V1 had no prescription model to compare against; this split is what makes the engine possible.

---

## 1. Enums (new)

```
SessionStatus : PLANNED | IN_PROGRESS | COMPLETED | SKIPPED
GroupType     : STRAIGHT | GIANT_SET
SetRole       : RAMP | WARMUP | TOP | BACKOFF | WORKING | AMRAP
FeedbackTap   : TOO_EASY | ON_TARGET | TOO_HARD      # the per-set signal
NoteClass     : CONFIG_CHANGE | TRANSIENT_FLAG | PROGRAMMING_REQUEST | JOURNAL
```

---

## 2. `Session`

| Field | Type | Notes |
|---|---|---|
| id | PK | |
| date | date | |
| day_role | text | "Upper A" / "Lower A" / … (the split slot) |
| phase | Phase | snapshot of `EngineState.current_phase` at generation |
| status | SessionStatus | |
| generated_at | datetime | |
| approved_at | datetime? | human-in-loop approval (propose→validate→approve) |
| signature | json | feature vector (exercises, rep zones, techniques, order, volume) for the **no-two-identical** check |
| rationale | text? | why the agent built it this way (replayable) |
| notes | text? | |

---

## 3. `ExerciseGroup` — preserves the T1-then-giant-set structure

| Field | Type | Notes |
|---|---|---|
| id | PK | |
| session_id | FK→Session | |
| order_index | int | |
| group_type | GroupType | STRAIGHT (T1 primary, one movement) / GIANT_SET (3 movements) |
| rounds | int | giant sets = 3 |
| rest_seconds | int | effective per-exercise rest 2.5–3 min holds inside giant sets |
| label | text? | |

Primaries run as `STRAIGHT` groups **first** (the locked structural rule), then `GIANT_SET` groups (3×3).

---

## 4. `PlannedExercise` — a movement slot in a group

| Field | Type | Notes |
|---|---|---|
| id | PK | |
| group_id | FK→ExerciseGroup | |
| movement_id | FK→Movement | |
| order_index | int | position within the group |
| scheme | Scheme | resolved for this session |
| objective | Objective | resolved (phase default or movement override) |

The prescription itself lives in child `PlannedSet` rows, because structure varies by scheme (top-set+backoff vs double-progression vs rep-ratio) and that's cleaner than cramming it into one row.

---

## 5. `PlannedSet` — what was prescribed

| Field | Type | Notes |
|---|---|---|
| id | PK | |
| planned_exercise_id | FK | |
| set_index | int | |
| set_role | SetRole | RAMP/WARMUP not progressed; TOP/BACKOFF/WORKING are |
| **is_warmup** | bool | **real flag** (fix) — warmups auto-generated from working load |
| target_load | float? | |
| target_reps_low / high | int | range for double-progression; equal for fixed |
| target_rpe | float? | within the phase envelope |
| — assisted — | | |
| target_unassisted_reps | int? | rep-ratio scheme |
| target_assisted_reps | int? | |
| — HT composite — | | |
| target_plates | float? | |
| band_pair_id | FK→BandPair? | |
| target_felt_peak | float? | |

---

## 6. `SetLog` — what happened (the capture, the fix)

| Field | Type | Notes |
|---|---|---|
| id | PK | |
| planned_set_id | FK? | null if an unplanned/extra set |
| session_id | FK→Session | denormalized for query |
| movement_id | FK→Movement | denormalized |
| set_index | int | |
| performed_at | datetime | |
| actual_load | float? | |
| actual_reps | int? | |
| **feedback_tap** | FeedbackTap | **NOT NULL on every working set** — capture cannot close without it |
| rpe_numeric | float? | optional finer grain; tap is primary |
| **is_warmup** | bool | real flag |
| — assisted — | | actual_unassisted_reps, actual_assisted_reps |
| — HT — | | actual_plates, band_pair_id, felt_peak |

The `feedback_tap` is the coarse three-state signal (chosen over a numeric wheel because it's reliable to log mid-set and maps cleanly to a load adjustment). It is required on `WORKING`/`TOP`/`BACKOFF` sets; `RAMP`/`WARMUP` sets don't require it.

---

## 7. Three input streams (each routes differently)

**A. Per-set tap** → `SetLog.feedback_tap`. Drives autoregulation (next-set / next-session load) and RPE-cap compliance. Every working set.

**B. Post-exercise survey** → `ExerciseSurvey`. Captured **at exercise conclusion** (recall freshness), one tap. Routes to weak-point logic: failure *location* → muscular limiter; *technique* breakdown → cue/load; *asymmetry* → unilateral work.

| `ExerciseSurvey` | Type | |
|---|---|---|
| id | PK | |
| session_id, movement_id | FK | |
| performed_at | datetime | |
| sticking_point | code | from the per-lift taxonomy (§8) |
| asymmetry_flag | bool? | |
| technique_flag | bool? | |

**C. Freeform note** → `Note`. Anytime; classified server-side; config changes need a confirm tap before they alter the program.

| `Note` | Type | |
|---|---|---|
| id | PK | |
| session_id?, movement_id? | FK | scope |
| created_at | datetime | |
| text | text | |
| classification | NoteClass | CONFIG_CHANGE / TRANSIENT_FLAG / PROGRAMMING_REQUEST / JOURNAL |
| confirmed | bool | CONFIG_CHANGE requires confirm (re-baselines a movement) |
| applied | bool | whether it's been actioned |

---

## 8. `StickingPointTaxonomy` — per-lift survey options (seed)

Data-driven so it's editable. One row per (lift_category, option). Seed for the primaries:

| Lift | Options |
|---|---|
| BENCH | OFF_CHEST · MIDRANGE · LOCKOUT · ELBOWS_FLARED · LEFT_RIGHT · SOLID |
| BACK_SQUAT | OUT_OF_HOLE · MIDRANGE · **HIPS_SHOOT_UP** · KNEES_CAVE · LEFT_RIGHT · SOLID |
| OHP | OFF_SHOULDER · MIDRANGE · LOCKOUT · LOWER_BACK_ARCH · LEFT_RIGHT · SOLID |
| RDL | OFF_BOTTOM · MIDRANGE · LOCKOUT_HIPS · GRIP · BACK_ROUNDING · LEFT_RIGHT · SOLID |

`HIPS_SHOOT_UP` on squat is live telemetry for the erector-over-glute thesis — its frequency falling across the meso is direct evidence the glute work is closing the deficit.

---

## 9. Session lifecycle + analysis hook

```
PLANNED      generate from library + state + phase → compute signature →
             validate (RPE cap, floors/caps, equipment manifest, knee freq,
             pull:push, no-two-identical) → approve
  ↓
IN_PROGRESS  log SetLogs (mandatory tap) as performed
  ↓
COMPLETED    ExerciseSurveys captured; notes classified
  ↓
ANALYSIS     per-lift e1RM from working sets (tap → implied RIR → Epley);
             update MovementState (calibration_status, e1rm, tier,
             consecutive_ceiling); update EngineState gate flags;
             stall/weak-point ONLY for objective=PROGRESS lifts
```

Analysis is the algorithm from the progression-model spec; this layer just defines the data it reads and writes.

---

## 10. Generation interface (algorithm deferred)

Generation must *produce* a `Session` of `ExerciseGroup` → `PlannedExercise` → `PlannedSet` rows satisfying the hard constraints, and the validator checks that product. The **constraint-satisfaction algorithm itself** (how the agent selects movements, fills weekly requirements, enforces the signature distance) is the next design — this spec only fixes the shape of what it emits and what gets logged back.

---

## Resolved (locked)
- **Per-set input:** tap-only mandatory (`feedback_tap`); `rpe_numeric` optional and surfaced **only on primaries during calibration**. Keeps the coarse mid-set reliability that fixed the original bug.
- **Conditioning logging:** split. Z2 cardio gets a lightweight log (duration, avg HR from TICKR, incline, backward-walk done); loaded knee work (Nordic, tib, sissy) stays in the normal Session/SetLog since it's progressed.

## Open / next
- Generation algorithm — **done** (see generation spec).
