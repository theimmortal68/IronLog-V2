# In-Gym Logging UX + Prescription Fidelity — Design

**Date:** 2026-07-01
**Repos:** server `~/projects/IronLog-V2` (assembler + seed); client `~/projects/IronLog-V2-Client` (capture UI)
**Status:** Approved design → spec for implementation planning

## Goal

Make D1 faithfully loggable in the gym — correct set sequencing, pre-filled weights/reps/RPE, and a working rest timer — then re-test on the phone. Found via real phone testing 2026-07-01.

## Scope line

| THIS chunk (display + logging UX + assembler fidelity) | NOT this chunk (separate later chunks) |
|---|---|
| App SHOWS the right targets | App COMPUTES load changes (progression engine: RPE-8 + special rules) |
| Assembler READS seeded values | Progression engine WRITES new values |
| No double-progression logic anywhere | HT band-composite loading |
| `current_load` used as-is | In-app generate/approve UI |
| Timer-only rest trigger | HR<110 gating / wearable integration |
| | Rich phased-pull-up logging widget |

The banked progression model (RPE-8 cap + per-exercise special rules) and the HT band-composite calibration are documented in memory for their future chunks.

## Data-shape note

The DTO fields largely exist (`ExerciseGroup.rest_seconds`, `PlannedSet.target_reps_low/high`, `target_rpe`). This chunk **populates + displays** them; it is not a contract change. A `unilateral` flag per exercise is the one addition surfaced to the client.

---

## Server (IronLog-V2)

### S1 — Seed reconciliation (data; live DB is freely reseedable pre-launch, no backup)

**Rep targets — reconcile every TE `rep_low/rep_high` literally from the locked week doc:**

| Doc pattern | Seed | Movements |
|---|---|---|
| `3×8` | 8/8 | Bench, Pendlay Row-Narrow, Hip Thrust, Pull-up assisted target, Ab Wheel |
| `3×10` | 10/10 | Incline DB Press |
| `3×12` | 12/12 | Cross-Body RF (D1), Lat Prayer, Seated Cable Row, Cross-Body LR (D1) |
| `3×15` | 15/15 | Face-Up Knee Raise (D1) |
| `3×4-6` | 4/6 | RDL |
| `3×5-8` | 5/8 | Pull-up primary (D4/D6), Dips |
| `3×8-10` | 8/10 | Meadows Row, ATG Split Squat, SA DB Row, Bulgarian Split Squat, Poliquin Step-up, T-Bar Row Wide |
| `3×8-12` | 8/12 | V-Bar Pushdown, Face-Up Knee Raise (D4) |
| `3×10-12` | 10/12 | Andreoni Cable Pullover, Prone DB Rear Delt Fly, DB Seal Row, Hyper Pro Calf Raise |
| `3×12-15` | 12/15 | Cable Tib Raise, Face Pull, Lateral Raise, Cross-Body RF (D6) |
| `3×15-20` | 15/20 | Reverse Hyper Recovery |
| `3×3-6` | 3/6 | Dragon Flag (progression via body position, not reps) |

The build reconciles against the *actual seeded TEs by slot*; the table is the deterministic source.

**Unilateral flag** — set `unilateral=true` (client shows "per side") for: Meadows Row, Bulgarian Split Squat, ATG Split Squat, Cross-Body RF, Cross-Body LR, SA DB Row, Poliquin Step-up, single-leg Reverse Hyper (meso), Staggered RDL. *(If no `unilateral` field exists on the model, add it — additive-nullable migration.)*

**Base rest per tier** — seed `Tier.rest_seconds`: **T1 120, T2 90, T3 60, T4 60** (pull D2/D5/D6 tier rests from the doc; giant-set groups carry their tier's value).

**Scheme reconciliation** — flip the remaining `Movement.scheme` TOPSET_BACKOFF → STRAIGHT: **Belt Squat, RDL** (Bench already done). Result: no backoffs anywhere → the 148.5-class invalid-load bug is gone at the source; no rounding feature needed.

**RPE cap** — default `rpe_cap=8`. Seed `rpe_cap=6` for **Reverse Hyper Recovery** (light/recovery). **Hip Thrust** is rule-driven (not RPE-driven) — no display change this chunk; note for the progression engine (`progression_type=RULE_DRIVEN` is a future field). Belt Squat's real RPE 7 vs displayed 8 is accepted (not a display-layer problem).

Apply to the live DB (reseedable pre-launch) and the seed source.

### S2 — Assembler

- Honor `TE.rep_low/rep_high` per set (replace the hardcoded 8-12 / 3-5 bands) — Finding B.
- Propagate `Tier.rest_seconds` → `ExerciseGroup.rest_seconds`.
- Carry `rpe_cap` → the set's `target_rpe` for display (default 8; RevHyper-Recovery 6).
- Surface the `unilateral` flag per exercise in the session graph.
- Confirm nothing double-progresses: the assembler uses `current_load` as-is; no top-of-range load bump.

---

## Client (IronLog-V2-Client)

### C1 — Giant-set round-major sequencing (the core bug)

In the capture flatten step: for `GIANT_SET` groups, interleave by round (`for round: for exercise: that round's set`) → ex1→ex2→ex3, rest, repeat. `STRAIGHT` groups stay exercise-major. **A unilateral item's "set" = both sides completed** before advancing to the next exercise in the round (one logging unit covers L+R; do not advance to the next exercise mid-item).

### C2 — Auto-populate / capture display

- **Weight:** field pre-filled with `target_load` as an editable default (log = accept or adjust).
- **Reps:** pre-filled from `rep_low/high` — **fixed n/n → single number with the RPE target prominent** (the progression signal); **range → "low-high"**, lifter picks actual.
- **Unilateral:** labeled per-side.
- **Assisted (Pull-up, Nordic):** show `assist_level` + rep target.
- **Phased pull-up (D4/D6), minimal handling:** Set 1 = unassisted AMRAP (null/blank rep target — lifter enters count); Sets 2-3 = `{unassisted, assisted}` pair with the band `assist_level`. A richer phased-set widget is deferred.

### C3 — Rest timer

- Auto-start a **skippable** (and add-time) countdown on logging a set (straight) or a round's last item (giant).
- **T1 RPE-adaptive:** base 120 × {easy 0.75 / on-target 1.0 / hard 1.5} keyed to the set's `feedback_tap` → **90 / 120 / 180 s**.
- **T2/T3/T4 fixed:** 90 / 60 / 60 s. Giant-set round rest = the group's fixed value.
- **Timer-only trigger.** No HR integration — the lifter judges HR-readiness manually; HR<110 gating is a future wearable chunk.

---

## Verification

- Server pytest green (assembler reps/rest/scheme/unilateral; any migration parity).
- Client rebuild (workstation gradlew) + `adb -s 192.168.1.17:34509 install -r`.
- Regenerate D1 server-side; **phone re-test checklist:** Bench shows **3×8** (no 148.5); giant sets **rotate** ex→ex per round; weights/reps/RPE **pre-fill** (fixed as single + RPE, ranges as range, unilateral per-side); rest timer **fires** with correct durations (T1 adapts to the logged tap; T2-T4 fixed).

## Build order

S1 (seed) → S2 (assembler) → regen-check (seed + assembler agree) → C1 (giant-set) → C2 (auto-populate) → C3 (rest timer) → deploy (server + client APK) + regen D1 + phone re-test.

## Global constraints

- NO `from __future__ import annotations` (server).
- BUILD-AND-TEST-ONLY: server tests on myflix via ssh; live DB reseedable pre-launch (no backup needed until Phase-1 launch + real logging).
- Migration rule (single-statement-atomic or idempotent + parity) if the `unilateral` field needs a column.
- Two-repo: server built/tested on myflix; client built on workstation gradlew + adb install to 192.168.1.17:34509. `SERVER_BASE_URL=http://192.168.1.7:8000` is a local-uncommitted client change.
- Two-writer boundary preserved: this chunk reads/display + seed data; it does not write `current_load` (progression) or outcome fields.
