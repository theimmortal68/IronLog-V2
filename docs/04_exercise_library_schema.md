# Exercise-Library Schema (V2 Engine — Data Model Spec)

The data structure the Python server and validator run against. It encodes the four design specs:
HT composite-load, calibration block, progression model, and the verified movement library.

**Scope:** this covers the *library + reference + state* layer — the movements, their equipment, the band table, phase policy, and per-movement dynamic state. The *session / set-log / generation* layer (how a workout is assembled and logged) is the next design once this is locked. One deliberate split throughout: **definition** (static, the library) vs **state** (dynamic, what's true right now).

---

## 1. Enums

```
Region            : UPPER | LOWER | CORE | NONE
LiftCategory      : BENCH | BACK_SQUAT | FRONT_SQUAT | OHP | RDL | DEADLIFT |
                    ROW | HIP_THRUST | REV_HYPER | CG_PRESS | NONE
Status            : ACTIVE | INACTIVE | PREP          # dropped rows are not imported
ProgressionMode   : LADDER | COMPOSITE | ASSISTED | PROTOCOL | CONDITIONING | NONE
Scheme            : STRAIGHT | DOUBLE_PROGRESSION | TOPSET_BACKOFF |
                    UNDULATION | WAVE | REP_RATIO
Objective         : MAINTAIN | PROGRESS | MEASURE
AssistSubtype     : CONTINUOUS | REP_RATIO              # ASSISTED only
AssistUnit        : DEGREES | CABLE_LB | TUBE_COUNT | REP_COUNT
LoadUnit          : LB | LB_PER_HAND | CABLE_LB | DEGREES | TUBE | BODYWEIGHT | NONE
Phase             : CALIBRATION | CUT | STAB | REBUILD
CalibrationStatus : INHERITED | CALIBRATING | MEASURED
EquipPhase        : P1 | P2 | P3 | P4                   # when equipment comes online
```

---

## 2. `Movement` — the library (definition, static)

| Field | Type | Notes / source |
|---|---|---|
| id | PK | |
| name | text | e.g. "Back Squat [PB]" |
| base_name | text | tag/grip stripped — "Back Squat" (grouping) |
| region | Region | |
| lift_category | LiftCategory | |
| is_primary | bool | the 4 barbell anchors |
| is_tracked | bool | |
| status | Status | ACTIVE / INACTIVE (curls, future BW) / PREP (mobility) |
| load_equipment_id | FK→Equipment | the **load-bearing** item → governs floor/step |
| equipment_ids | FK[]→Equipment | full set (e.g. DB + Bench) |
| progression_mode | ProgressionMode | from verification "Progression Type" |
| assist_subtype | AssistSubtype? | ASSISTED only — CONTINUOUS (Nordics, rev-Nordic) / REP_RATIO (pull-up) |
| assist_unit | AssistUnit? | ASSISTED only |
| scheme | Scheme | default loading scheme |
| objective_override | Objective? | null = inherit phase default; set PROGRESS for pull-ups |
| increment_ladder | float[] | ordered steps, e.g. [10,5,2.5] / [5,2.5] / [2.5] |
| min_step | float | smallest hardware-reachable step |
| load_floor | float? | min loadable (null = n/a) |
| cap | float? | max load cap (Landmine 25, RevHyper 180, Light 90) |
| rpe_capped | bool | primaries true |
| rpe_cap_exempt | bool | Hip Thrust true |
| family | text? | groups variants sharing one baseline (grip rotations) |
| is_family_anchor | bool | the variant whose e1RM the family tracks |
| derived_from_id | FK→Movement? | seed first-load from another lift's e1RM (§5b) |
| start_ratio | float? | with derived_from — Front Squat 0.80 × Back Squat |
| band_eligible | bool | HT: uses a band pair (composite) |
| notes | text | |

---

## 3. `Equipment` — vocabulary + hard floors

| Field | Type | Notes |
|---|---|---|
| id | PK | |
| name | text | "Barbell — Double Black Diamond", "Dumbbells (MX100)", "Ares cable (single)", "Ares cable (dual)", "Hyper Pro belt attach", "GMWD HT", "Scout reverse hyper", "PureTorque Pro", "BMF Camber", "Kyoto EZ", "Kettlebell", "Pull-up tower", "Tubes" |
| load_floor | float | 45 / 30 / 35 / 10(per hand) / 10 / 20 / 0 |
| min_step | float | 2.5 / 5 |
| load_unit | LoadUnit | |
| available_phase | EquipPhase | when it joins the gym (VBS→P3, PCD→P4…) — expands the vocabulary on arrival |
| notes | text | |

Locked floors: barbell 45/2.5 · camber 30/2.5 · EZ 35/2.5 · DB 10-per-hand/2.5 · Ares single 10/2.5 · Ares dual 20/5 · belt-squat & rev-hyper plate-loaded floor 0 · KB 13.

---

## 4. `BandPair` — HT accommodating resistance (from HT spec)

| Field | Type | Notes |
|---|---|---|
| id | PK | |
| label | text | "#0 Orange" … "#5 Purple" |
| bottom_lb | float | calibrated/modeled bottom contribution |
| peak_lb | float | ≈ 2.1 × bottom (geometry) |
| calibration_status | enum | MODELED / MEASURED |
| inspection_date | date | wear-gate prompt (sudden-failure risk) |
| usable | bool | #5 false (bottom alone > clamp) |

Bottom clamp (220, bottom-total) and the stretch cap live in the HT spec; the validator reads both BandPair and the HT row to enforce them.

---

## 5. `PhasePolicy` — one row per phase (config)

| Field | Type | CUT/STAB | REBUILD |
|---|---|---|---|
| phase | Phase (PK) | | |
| default_objective | Objective | MAINTAIN | PROGRESS |
| rpe_band_low / high | float | 6 / 7.5 | 7 / 9 |
| hard_cap | float | 8 | 9 |
| top_set_rpe | float | 8 | 9 |
| progression_attempted | bool | false (except override) | true |
| volume_posture | text | trimmed (1 top + 1–2 backoff; +1 STAB) | graduates over 12 wks, deload/5 |
| meaningful_drop_pct / sessions | float/int | 5% / 3 | (standard stall) |

---

## 6. `EngineState` — global (dynamic, singleton)

```
current_phase : Phase
bodyweight    : float          # drives CUT→STAB gate (213±2)
gate_flags    : {rhr_down, sleep_ok, no_rpe_creep, bw_stable_2wk,
                 strength_bounce, subjective_ok}   # STAB→REBUILD
```

---

## 7. `MovementState` — per movement (dynamic)

| Field | Type | Notes |
|---|---|---|
| movement_id | FK→Movement | |
| calibration_status | CalibrationStatus | INHERITED → CALIBRATING → MEASURED |
| e1rm | float? | measured baseline |
| e1rm_updated_at | datetime | |
| current_load | float? | |
| current_increment_tier | int | index into increment_ladder (steps down on stall) |
| current_rep_scheme | text | held 1–2 wks for measured lifts |
| rep_scheme_locked_until | date | the hold window |
| consecutive_ceiling_sessions | int | the 2-session progression gate |
| — assisted — | | |
| assist_level | float | degrees / cable-lb / tube count / unassisted-rep count |
| — HT composite — | | |
| ht_plates | float | |
| ht_band_pair_id | FK→BandPair | |
| ht_felt_peak | float | calibration accrual anchor |

---

## 8. How objective + scheme resolve (the runtime contract)

```
policy   = PhasePolicy[EngineState.current_phase]
objective = Movement.objective_override ?? policy.default_objective
envelope  = (policy.rpe_band, policy.hard_cap, policy.top_set_rpe)
→ Movement.scheme executes within envelope, advancing only if
  (objective == PROGRESS or policy.progression_attempted)
→ stall/weak-point logic fires ONLY when objective == PROGRESS
  (maintained lift: flat = success; react only to meaningful_drop)
```

This single resolution is the whole behavioral core: pull-ups progress through the cut (override), the four primaries + HT hold (phase default), and REBUILD flips the default so barbells and the HT +5 wake without touching any movement row.

---

## 9. Worked rows (sanity check)

- **Back Squat [PB]** — primary, LADDER, scheme TOPSET_BACKOFF, ladder [10,5,2.5], floor 45, rpe_capped, family `back_squat` anchor.
- **Front Squat [PB]** — derived_from Back Squat, start_ratio 0.80; own scheme; not same-baseline.
- **Pendlay Row – Medium/Narrow/Wide** — family `pendlay_row`, Medium = anchor, others SAME_BASELINE.
- **Hip Thrust** — COMPOSITE, band_eligible, rpe_cap_exempt, objective holds in cut; state carries plates+band+felt_peak.
- **Nordic Curl** — ASSISTED / CONTINUOUS, assist_unit DEGREES.
- **Pull-up [TOWER+TUBES]** — ASSISTED / REP_RATIO, assist_unit REP_COUNT, objective_override PROGRESS, tracked.
- **Lateral Raise [FT]** — LADDER, scheme DOUBLE_PROGRESSION, ladder [2.5], floor 10 (light-load rep-progress note).

---

## Tier reset rule (resolved)

`current_increment_tier` steps **down** on stall (5→2.5). It resets **up** to the coarse rung only when capacity genuinely increased:
- **REBUILD entry** — surplus restores recovery (capacity step-change) → reset to tier 0.
- **Sustained breakthrough** — e1rm up ≥ one coarse increment since the last step-down → reset up one rung.
- **Routine deload** — **no reset**; resume at the landed tier (otherwise the lift re-stalls and sawtooths every block).

Implemented as: on REBUILD transition, set all `current_increment_tier = 0`; on e1rm update, if gain ≥ coarse step since last step-down, decrement the tier index by one.

## Open / next
- Session, SetLog (with the per-set RPE tap fixed), and generation tables — the next schema layer.
