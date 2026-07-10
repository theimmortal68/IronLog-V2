# IronLog-V2 Build Plan (living punch-list)

Last updated 2026-07-09. Source of truth for the in-flight feature/bug work.

## ✅ Shipped + live
- **Progression engine** (K + K2): `progression_rule` wired from the YAML; advance→load bridge (`pending_load_delta`) — a clean top-of-range RPE-8 session ratchets `current_load` by the increment; fixed the tier-bump bug. Live on server.
- **Knee-raise** (C-server): retyped bodyweight/incline (`assist_level` degrees, INCLINE_REDUCTION). Live on server.
- **Capture** (client, installed): B logged-actuals + edit (bilateral); F weight carry-forward; J idempotent logging (dedups double-submits, preserves unilateral two-sided logs); band-color off-by-one fixed (Orange no longer shows as Red); resume-cursor on reload (background-kill no longer looks like lost data).
- **L — load ratchet** (merged, 2026-07-09): `performed_floor_delta` — a clean session performed heavier than the seeded baseline (e.g. Belt Squat 265 vs seeded 260) no longer regresses to the old baseline next session. Scoped to plain `current_load` movements; HT excluded (see next item). **Not yet deployed to the live server** (merged to `main`, no live reseed/restart run yet).
- **Note-apply LOAD override now affects Hip Thrust** (merged, 2026-07-09): the existing "apply a note" mechanism had zero effect on HT's plates (assembler discarded it once `ht_plates` was set). Fixed — a day-scoped "+N lbs" note now actually bumps the target day's HT plates, without compounding across regenerations. Answers the Day-2 "ready to go up 5lbs on Day 5" note. **Not yet deployed to the live server.**
- **[C-display] server half — unit_hint on ExerciseOut** (merged, 2026-07-09): `ExerciseOut.unit_hint` now surfaces "lb"/"assist"/None per movement (reusing `_UNIT_HINTS`/`load_field_for_mode`), so a client can render "20° assist" instead of "20 lb" for Nordic Curl/Reverse Nordic/Face-Up Incline Knee Raise. First live shakedown of the new codex-generation + Opus-review-gate pipeline — clean pass, no findings. **Client-side render is a separate follow-on spec, not started.** **Not yet deployed to the live server.**

## Day-2 feedback (2026-07-07) — reviewed + slotted

### Design / app
- **[C-display]** Assist/incline movements show a **lb** value instead of **degrees of assist**. Confirmed live: Nordic Curl shows "20 lbs" (note "still posting load as 20lbs instead of 20 degrees of assist"). **Server data is correct** (`assist_level=20°`); the session API hands the client a bare `target_load` float with no unit hint, so it renders "20 lb". FIX = **server**: surface `progression_mode`/`unit_hint` on `ExerciseOut`/`PlannedSetOut` (the `WizardMovement.unit_hint` pattern already exists, wire it into the session path) → **client**: render "20° assist" (degrees) for ASSISTED/incline movements. Applies to: **Nordic Curl, Reverse Nordic, Face-Up Incline Knee Raise**. Recurring visible bug → prioritize. **Server half SHIPPED 2026-07-09** (see "Shipped + live" above). Client-side degree render is a separate follow-on spec, not yet written.

### Exercise / programming — ✅ both shipped 2026-07-09 (see "Shipped + live" above)
- ~~**[L — load ratchet]**~~ DONE. Belt Squat 265×12 @ RPE8 (seeded 260) no longer regresses.
- ~~**[note-apply / H] Hip Thrust "+5 on Day 5"**~~ DONE — this turned out to be a plain consumption-side bug (the assembler ignored the override for HT), not an AI-reasoning gap. Fixed directly; no **H**-style reasoning was needed for this specific note.

## Queued
| Item | What | Size |
|---|---|---|
| **A** | warmup/ramp sets (heavy barbell only; 3-set 40/60/80%, reps 5/3/2 — design locked) | med |
| **I** | finishers (defs saved in `phase1-warmup-finisher-source.yaml`; d6 jump-rope has its own duration→rope progression) | med |
| **G** | autoregulated rest (hardest set in a giant set governs the duration) | med |
| **D+E** | background rest timer (foreground service: keeps counting + notification + sound when app unfocused) | med (client) |
| **H** | AI acts on programming notes (reorder / assist+reps / cross-day requests) — own design | large |

## Deferred / tiny
- Clean the single stray Bench duplicate setlog from Day 1 (negligible; 6 of the "7 dupes" were legitimate unilateral two-sided logs).
- Side-aware unilateral **edit** (currently unilateral logged cards are view-only): key `loggedSetActuals`/`editingSetId`/`existingLog` by `(plannedSetId, sideIndex)` + per-side cards.
- `build_weak_point_hints` still reads MovementState day-blind (stall-detection only, not loads).
