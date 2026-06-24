# Progression Model — Schemes, Objectives & Phase Policy (V2 Engine Spec)

How the engine decides what to do with a movement's load and reps each session. Replaces the old single rule (linear, +load after two successful sessions) with a layered, phase-aware model.

---

## 0. Core abstraction

Three layers compose to produce a prescription:

```
PHASE POLICY  (global: RPE band, hard cap, is progression attempted, volume posture)
   └─ OBJECTIVE  (per movement: maintain | progress — phase sets the default, movement can override)
        └─ SCHEME  (per movement: how load/reps are structured and advanced)
```

Resolution order: the phase sets the default objective and the RPE envelope → a movement may override the objective (e.g. pull-ups progress during a cut) → the movement's scheme executes within that envelope.

---

## 1. Why (maintenance principles)

For the current goal — hold strength through the final stages of a cut — three principles drive the design:

- **Intensity preserves strength; volume drives fatigue.** Trim *sets*, not load. Keep occasional heavy touches; shed grinding volume.
- **Lower the average RPE, keep the ceiling.** RPE is a *cap*, not a target. Most work at 6–7.5; only occasional top sets reach 8.
- **In a deficit, flat is success.** Recovery is compromised, so holding strength *is* the win. The engine does not try to add load to a maintained lift, and does not treat a flat session as a problem.

---

## 2. Phase policies

| Phase | Default objective | Target RPE band | Hard cap | Load progression attempted? | Volume posture |
|---|---|---|---|---|---|
| Calibration | measure | 7 → 8 | 8 | no (measuring) | normal |
| CUT | maintain | 6 – 7.5 | 8 | no (except `progress`-tagged) | trimmed to maintenance |
| STAB | maintain | 6 – 7.5 | 8 | no (except `progress`-tagged) | maintenance (slightly above CUT) |
| REBUILD | progress | 7 – 9 (graduated) | 9 | yes | graduates up over ~12 wks, deload every 5 |

---

## 3. Objectives

- **maintain** — hold strength. Flat is success. Engine autoregulates within the band but does **not** add load, and does **not** fire stall / weak-point / rotation logic on a flat or slightly-down session.
- **progress** — actively advance (load, reps, or reduced assist). Standard stall detection is active.

---

## 4. Scheme toolkit

| Scheme | Structure | Advance trigger | Best fit |
|---|---|---|---|
| **Straight sets** | N sets, same load & reps | (calibration / simple anchors) | T1 measurement |
| **Double progression** | fixed rep *range*, add reps under cap | top of range on all sets at ≤ cap → +increment, reset to bottom | accessories (default) |
| **Top-set + back-off** | 1 top set RPE 7–8, then back-offs RPE 6–7 | (maintenance: none) / 2 consecutive top sets at target reps ≤ cap → +increment (REBUILD) | primaries in CUT/STAB |
| **Daily undulation** | heavier/low-rep day + lighter/high-rep day across the week | n/a (fatigue distribution) | variety, fatigue spread |
| **Wave loading** | rep waves at one load (e.g. 8/6/4) | n/a (rep-scheme variation) | variety without adding load |
| **Rep-ratio (assisted)** | mixed unassisted + assisted reps per set | hit unassisted target → shift one rep assisted→unassisted; all-unassisted → drop a tube; tubes gone → add load | pull-ups |

---

## 5. Active design — CUT / STAB

| Group | Objective | Scheme | RPE | Rep-scheme cadence | Notes |
|---|---|---|---|---|---|
| 4 barbell primaries (Bench, Back Squat, Standing OHP, RDL) | maintain | top-set + back-off | top set ≤ 8; back-offs 6–7 | hold 1–2 wks | volume trimmed; flat = success; no load add |
| Hip Thrust | maintain | composite (HT spec), held | 6 – 7.5 | hold | `+5` rule dormant; plates+band held under the 220 clamp |
| **Pull-ups** | **progress** | rep-ratio | as needed | tracked semi-anchor (held, readable) | the lone progress target — rides the cut's tailwind |
| Accessories / free layer | maintain | double-progression under cap | 6 – 7.5 | every-session novelty OK | autoregulate; attribution doesn't matter here |

Rotation rule: anything being *measured* (primaries, pull-ups) holds its rep scheme 1–2 weeks for a clean read; the free layer churns every session for variety. Top-set + back-off is itself a non-monotonous shape, so a two-week hold doesn't feel stale.

---

## 6. Stall / regression handling (phase-aware — critical)

- **objective = maintain:** a flat or slightly-down session is **expected, not a stall.** Do not add load, do not fire weak-point or rotation logic. Only a *sustained, meaningful* drop signals under-recovery → response is to **reduce volume / check readiness**, never to add stimulus.
- **objective = progress:** standard stall detection (flat e1RM over N sessions, or failed prescription) → graduated weak-point response (L1→L2→L3).

This is the guardrail that stops the engine from "fixing" a lift that is doing exactly what it should — holding — during the cut.

---

## 7. Phase transitions (what flips)

- **CUT → STAB** (gate: bodyweight 213 ± 2): policy nearly unchanged; volume nudges up; still maintain.
- **STAB → REBUILD** (multi-condition gate: RHR down + sleep good + no RPE creep + bodyweight stable 2+ wks + strength bounce-back + subjective markers normal): on flip — default objective → **progress**; RPE band and cap rise; the HT `+5` rule **wakes**; the barbell primaries resume active loading (the old two-consecutive-session bump is the REBUILD primary rule, reactivated); volume graduates over ~12 weeks, deload every 5.

---

## 8. Composition with other specs

- **HT composite spec** = the load *model* for the Hip Thrust row; this spec sets its *objective* (held in cut, progressing in REBUILD).
- **Calibration block** = establishes the measured baselines this model loads from.
- **Exercise library schema** = `scheme` and `objective` are per-movement fields; the phase policy is global engine state.

---

## Parameters to set (proposed defaults — confirm)

- **Maintenance "meaningful drop" threshold:** e1RM down >5% sustained over 3 sessions, *or* 2 consecutive failed prescriptions → treat as under-recovery (drop one set, check readiness). *(proposed)*
- **Trimmed primary volume:** 1 top set + 1–2 back-offs in CUT; +1 back-off in STAB. *(proposed)*
- **Top-set frequency:** every primary session carries its top set (it's the maintenance mechanism); back-off count is the fatigue lever. *(proposed)*
