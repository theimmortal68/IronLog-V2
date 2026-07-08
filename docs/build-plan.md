# IronLog-V2 Build Plan (living punch-list)

Last updated 2026-07-07 (post Day-2). Source of truth for the in-flight feature/bug work.

## ✅ Shipped + live
- **Progression engine** (K + K2): `progression_rule` wired from the YAML; advance→load bridge (`pending_load_delta`) — a clean top-of-range RPE-8 session ratchets `current_load` by the increment; fixed the tier-bump bug. Live on server.
- **Knee-raise** (C-server): retyped bodyweight/incline (`assist_level` degrees, INCLINE_REDUCTION). Live on server.
- **Capture** (client, installed): B logged-actuals + edit (bilateral); F weight carry-forward; J idempotent logging (dedups double-submits, preserves unilateral two-sided logs); band-color off-by-one fixed (Orange no longer shows as Red); resume-cursor on reload (background-kill no longer looks like lost data).

## Day-2 feedback (2026-07-07) — reviewed + slotted

### Design / app
- **[C-display]** Assist/incline movements show a **lb** value instead of **degrees of assist**. Confirmed live: Nordic Curl shows "20 lbs" (note "still posting load as 20lbs instead of 20 degrees of assist"). **Server data is correct** (`assist_level=20°`); the session API hands the client a bare `target_load` float with no unit hint, so it renders "20 lb". FIX = **server**: surface `progression_mode`/`unit_hint` on `ExerciseOut`/`PlannedSetOut` (the `WizardMovement.unit_hint` pattern already exists, wire it into the session path) → **client**: render "20° assist" (degrees) for ASSISTED/incline movements. Applies to: **Nordic Curl, Reverse Nordic, Face-Up Incline Knee Raise**. Recurring visible bug → prioritize.

### Exercise / programming
- **[L — load ratchet]** Belt Squat note: *"was able to fit 265 by reorganizing my plates. Did 12 reps to hit RPE 8."* Seeded 260 (rep-ladder [8,10,12,15]). Athlete went **off-script heavier to 265** and hit 12 reps. The engine derives load from its baseline, so on submit it regresses to 260 and ignores the 265. **L = never prescribe below what you actually performed.** Belt Squat 265 is the concrete case. (Rep-ladder also advances on the 12-rep hit — separate from the weight jump.)
- **[note-apply / H]** Hip Thrust note: *"ready to go up 5lbs on Day 5."* A cross-day load-increase request for the **D5 HT** track (independent from D2's). Should flow through the note-apply loop (classify LOAD_INCREASE → review → Apply +5 to the D5 HT setup). **Verify** the note-apply flow handles a day-specific "+5 on Day 5" note; if it needs AI reasoning about *which* day, that's **H**.

## Queued
| Item | What | Size |
|---|---|---|
| **C-display** | assist/incline moves show degrees, not lb (server unit-hint + client render) — see Day-2 #3 | small |
| **L** | load ratchet — never prescribe below performed load — see Day-2 #1 | small-med (server) |
| **A** | warmup/ramp sets (heavy barbell only; 3-set 40/60/80%, reps 5/3/2 — design locked) | med |
| **I** | finishers (defs saved in `phase1-warmup-finisher-source.yaml`; d6 jump-rope has its own duration→rope progression) | med |
| **G** | autoregulated rest (hardest set in a giant set governs the duration) | med |
| **D+E** | background rest timer (foreground service: keeps counting + notification + sound when app unfocused) | med (client) |
| **H** | AI acts on programming notes (reorder / assist+reps / cross-day requests) — own design | large |

## Deferred / tiny
- Clean the single stray Bench duplicate setlog from Day 1 (negligible; 6 of the "7 dupes" were legitimate unilateral two-sided logs).
- Side-aware unilateral **edit** (currently unilateral logged cards are view-only): key `loggedSetActuals`/`editingSetId`/`existingLog` by `(plannedSetId, sideIndex)` + per-side cards.
- `build_weak_point_hints` still reads MovementState day-blind (stall-detection only, not loads).
