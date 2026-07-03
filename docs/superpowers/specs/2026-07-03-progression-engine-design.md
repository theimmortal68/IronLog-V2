# IronLog-V2 — Progression Engine — Design (build-ready)

**Purpose:** The deterministic load-advancement engine. After a session is logged, decide each movement's next prescription (load / assistance / incline / rep-target / body-position) per its assigned rule, and emit a stall signal when a plateau is detected. This is the "load progression" responsibility named in the Decision Architecture (§2) — the app currently *displays* targets but does not yet *advance* them.

**Assumed prior:** `ironlogdecisionarchitecture.md` (rules dispose / model proposes; hard rules are the filter; static-config-vs-live-state). This engine is pure deterministic logic — it never invokes the AI. Its only output that reaches the AI (later) is a stall signal.

**Provenance:** Consolidated from the base-program thread's progression-engine spec + this repo's grounding review (2026-07-02): the five design calls, the four reuse findings, and the Option-C write-boundary — all resolved and folded inline below (no open amendment block).

---

## 0. Scope

**In this chunk** — deterministic per-movement progression for all movements + stall detection:
- The 9 progression rules (§1), assigned per movement (§3), respecting equipment ceilings (§2).
- Per-`(movement, day)` independent state (§4).
- Stall detection reusing the existing detector, enriched with type/severity (§5).
- The Option-C write boundary (§6): the engine *decides* advancement; `commit_session` remains the sole *writer* of `current_load`; `run_analysis` owns bookkeeping.

**Deferred to follow-up chunks (explicitly OUT):**
- **Pull-up cross-day structural transitions** (the milestone table that mutates *other days'* protocols — 4→D4 T1 becomes unassisted-max, 6→drop sling on all D4, etc.). This is program-structure mutation + client UI work, a distinct subsystem. This chunk DOES track the pull-up's rolling-unassisted-max as state and does its own assistance-band reduction; it does NOT fire cross-day structural events.
- **Meso rotation** (e.g. RDL→Staggered RDL, bilateral→single-leg by meso). Already handled by generation/assembler via `MesoRotation`; the engine progresses whatever movement occupies the slot for the current meso. Not the engine's job.
- **HT band-composite loading** (accommodating resistance where peak > cap while bar < cap). Beta uses a single `current_load` for HT + rep-ladder-at-cap at 220; the `ht_plates`/`ht_band_pair_id`/`ht_felt_peak` scaffolding exists for the v0.7 band model.
- Movement swap logic / AI invocation / goal-aware deviation (later chunk — consumes the stall signal this chunk emits).
- Warmups / finishers / Z2 (v0.7).
- Dreadmill sled-push ceiling (a finisher; v0.7).

---

## 1. Rule vocabulary

Every movement is assigned exactly one rule; the engine looks it up and executes.

**1.1 RPE-8 standard (default).** Advance when all working sets hit `rep_high` at RPE ≤ 8 for `confirmation_window` sessions. **Confirmation window is keyed to TIER:** T1 primaries `N=1` (precise RPE — focused, straight sets, full rest); T2–T4 accessories `N=2` (noisier RPE in giant sets → double-confirm). `confirmation_window` is a per-rule config parameter (defaults T1=1, accessory=2), tunable without code. On advance: advance one tier in the increment ladder (see §3 / reuse §10), reset the confirmation streak. On non-advance (missed reps OR any set > RPE 8): unchanged, reset streak. Fixed-rep singles (`rep_low==rep_high`): "hit target" means exactly that count. Ranges: advance requires `rep_high` on all working sets; `rep_low` is not sufficient.

**1.2 Single-session progression (Cable V-Bar Pushdown only).** The ONE accessory override to N=1: Set 3 (last working set) hits `rep_high` at RPE ≤ 8 → advance next session. `confirmation_window=1`.

**1.3 Rule-driven fixed increment (Hip Thrust).** Advance the bar +5 lb every session a session is performed, RPE-EXEMPT (skip the RPE check entirely). Applies to HT D2-track and D5-track (independent, §4). At the 220 bar cap → transition to rep-ladder-at-cap (§1.6): bar stays 220, rep target ladders 8→10→12 (band-composite is v0.7).

**1.4 Incline reduction (Nordic Curl, Assisted Nordic light, Face-Up Incline Knee Raise).** Advance = decrease `assist_level` (degrees) by the movement's ladder step (§3), N=2. On non-advance unchanged.

**1.5 Assistance reduction (Reverse Nordic assisted, Pull-up assisted portion).** Same shape, progression variable is assistance (lb or band), N=2. Reverse Nordic: 20→15→10→5→BW-unassisted, then transitions to loaded (BW+plate) via RPE-8 standard on hitting BW. Pull-up: band-swap ladder (§3.7-lite below).

**1.6 Rep ladder (at equipment cap, or bodyweight).** `rep_target` ladders up per the movement ladder when clean for the tier's N; load is fixed (at the ceiling, or bodyweight). On reaching the ladder terminal → "maintenance" (load+reps stable, engine emits nominal/no-stall while maintenance met). Applies to: **capped** movements — Scout RH (180), Belt Squat (260), HT after bar cap (§1.3); and **bodyweight rep-progression** — Ab Wheel Rollout (+1 rep), Dips while bodyweight-only (until 3×12 @ RPE≤8, then transitions to RPE-8-standard weighted). A bodyweight rep-ladder has no cap — it just advances reps (and, for Dips, transitions to the loaded rule at its threshold).

**1.7 Body position progression (Dragon Flag).** Bodyweight; advance steps `current_body_position`: tuck → single_leg_extended → straddle → full, N=2.

**1.8 Skill progression — pull-up rolling max (TRACKING ONLY this chunk).** Track `unassisted_max_rolling` = rolling 3-session max of unassisted Set-1 rep count (skill-based, stochastic). Persist it. **The cross-day structural transitions it would trigger are DEFERRED (§0).** This chunk records the max + drives the pull-up's own assistance-band reduction (1.5); it does not mutate other days.

**1.9 Fixed load (no progression).** `current_load` never changes automatically (manual override only). Applies to Reverse Hyper Recovery (D6 GS3, locked 90 lb, RPE cap 6).

---

## 2. Equipment ceilings

Hard caps; the engine never recommends above them; at the cap the active rule transitions to rep-ladder-at-cap (§1.6) and the transition PERSISTS (`active_rule`), does not revert.

| Movement | Ceiling | At ceiling |
|---|---|---|
| Belt Squat | 260 lb (pin) | RPE-8 standard → rep-ladder-at-cap |
| Hip Thrust (bar) | 220 lb (GMWD flex) | rule-driven → rep-ladder-at-cap (bar 220, reps 8→10→12; band-composite v0.7) |
| Scout Reverse Hyper | 180 lb (spec 176) | rep-ladder-at-cap (already its default rule) |

"Approaching ceiling" (within one increment) → informational flag only, no behavior change.

---

## 3. Per-movement rules (Phase 1, D1–D6)

*(Domain source: the base-program Phase-1 week. Increments are the ladder's typical step — the engine advances the tier index, not a hardcoded number; see §10.)*

**D1 Upper A:** Bench Press — RPE-8 (5). Pendlay Row Narrow — RPE-8 (5). Incline DB Press — RPE-8 (5/hand). Face-Up Incline Knee Raise — incline reduction (5°; steepest→20→15→10→5→flat). Pull-up (assisted) — assistance reduction + rolling-max tracking (§1.8/1.5). Cross-Body Lateral Raise — RPE-8 (2.5/side). Lat Prayer — RPE-8 (5 total). Seated Cable Row — RPE-8 (5). Ab Wheel Rollout — bodyweight rep-progression (+1 rep). Cross-Body Rear Delt Fly — RPE-8 (2.5/side).

**D2 Lower A:** Belt Squat — rep-ladder-at-cap 260 (8→10→12→15 terminal). Hip Thrust (D2 track) — rule-driven +5 (indep). Assisted Nordic Curl — incline reduction (20→15→10→5→0). Scout Reverse Hyper — rep-ladder-at-cap 180 (15→18→20→22→25+). ATG Split Squat — RPE-8 unilateral (2.5/hand, both sides clear). Cable Tib Raise — RPE-8 (2.5).

**D4 Upper B / Pull:** Pull-up (D4 primary, assisted portion) — assistance reduction + rolling-max tracking. Meadows Row — RPE-8 unilateral (5). Andreoni Cable Pullover — RPE-8 (5). Face-Up Incline Knee Raise (D4, 5°) — incline reduction (5°→flat terminal, then reps). Prone DB Rear Delt Fly — RPE-8 (2.5/hand). Single-Arm DB Row — RPE-8 unilateral (5). Dragon Flag — body position progression.

**D5 Lower B:** RDL — RPE-8 (5, range 4-6). Hip Thrust (D5 track) — rule-driven +5 (indep from D2). Bulgarian Split Squat — RPE-8 unilateral (2.5/hand). Scout Reverse Hyper — rep-ladder-at-cap 180. Assisted Nordic (light) — incline reduction (indep state from D2, §4). Poliquin Step-up — RPE-8 unilateral (2.5/hand). Reverse Nordic (assisted) — assistance reduction (20→…→BW→loaded). Cable Tib Raise — RPE-8 (2.5, indep from D2). Hyper Pro Calf Raise — RPE-8 (5).

**D6 Weak Points:** Pull-up (D6, rolling-max tracker) — rolling-max tracking. Dips — RPE-8 (BW until 3×12 RPE≤8, then weighted +5). Hip Thrust (D6) — follows D5 track ×0.80 (derived, not independent). T-Bar Row Wide — RPE-8 (5). DB Seal Row — RPE-8 (2.5/hand). Lateral Raise (Ares) — RPE-8 (2.5 total). Face Pull — RPE-8 (5). Cable V-Bar Pushdown — single-session (§1.2). Reverse Hyper Recovery — fixed load (90, RPE cap 6). Cross-Body Rear Delt Fly (D6) — RPE-8 (2.5/side, indep from D1).

---

## 4. Independent load tracks

Same movement on multiple days = independent per-`(movement_id, day_id)` state (NOT per movement): Hip Thrust (D2/D5; D6 is derived D5×0.80), Scout Reverse Hyper (D2/D5), Cable Tib Raise (D2/D5), Cross-Body Rear Delt Fly (D1/D6), Nordic Curl (D2 strength / D5 light — independent incline states). See §6 for the composite-key migration.

---

## 5. Stall detection

**Reuse `ironlog/engine/stall.py` `detect_stall` for the CORE signal** (failed-counter + e1RM-trend). Its constants are the thresholds (`STALL_FAILED_THRESHOLD=2`, `STALL_WINDOW=3`, `STALL_MIN_SESSIONS=3`, `STALL_EPSILON_PCT=0.01`) — tune via config, don't define a second set. `detect_stall` returns a single failed+plateau boolean; the **type/severity taxonomy below is NEW code layered above it**, not something it already emits.

| Type | From | Signal |
|---|---|---|
| FAILED_PROGRESSION (low) | consecutive non-advance ≥ threshold | severity=low |
| FAILED_PROGRESSION (high) | extended non-advance | severity=high |
| PLATEAU (medium) | e1RM flat across window | severity=medium |
| PLATEAU (high) | e1RM flat across extended window | severity=high |
| REGRESSION | e1RM trend negative | severity=high |

**e1RM: reuse `ironlog/engine/e1rm.py` `estimate_e1rm(load, reps, target_rpe, tap)`** (the composed RPE-adjusted function — NOT `implied_rir` alone, that's an internal). Session e1RM = max across working sets; trend per the existing detector's window.

**Stall signal output** (persisted to `MovementState.stall_signal`, consumed later by the goal-aware layer): `movement_id, day_id, stall_type, severity, duration_sessions, current_load, e1rm_trend, limiting_muscle` (← `Movement.primary_muscle`). **Drop `is_swappable`** — no source field exists; it's a goal-aware-layer concern (that layer derives swappability from its own tags). On advance, the signal clears.

---

## 6. Data model — EXTEND `MovementState`, don't parallel it

The v0.5 `MovementState` (`ironlog/models/library.py:129`) already carries most of what's needed; **map onto existing fields, do NOT create duplicates:**

| Concept | Existing field |
|---|---|
| assistance / incline / rep-target (polymorphic) | `assist_level` (comment: "degrees / cable-lb / reps") — covers 1.4, 1.5, and rep-ladder |
| non-advance counter | `consecutive_failed_progressions` (PROGRESS-gated) |
| at-ceiling tracking | `consecutive_ceiling_sessions` (bool derivable) |
| load rounding / increment | `current_increment_tier` (index into `Movement.increment_ladder`) — solves the 148.5 problem structurally |
| HT band state (v0.7) | `ht_plates`, `ht_band_pair_id`, `ht_felt_peak` + `bandpair` FK (already modeled) |
| e1RM head | `e1rm`, `e1rm_updated_at` |

**Genuinely NEW fields (additive migration — single-statement-atomic or idempotent + parity keystone `test_chain_matches_create_all`):**
- `day_id` — composite key `(movement_id, day_id)` (§4). Migration: add column, backfill existing rows from originating session, change the unique constraint from `movement_id` to `(movement_id, day_id)`, update engine queries to key on the composite.
- `consecutive_advance_count` — the confirmation *streak* (distinct from the *failed* counter).
- `active_rule` — which rule is live (persists ceiling transitions, §2).
- `current_body_position` — Dragon Flag ladder.
- `stall_signal` — emitted signal object.
- `unassisted_max_rolling` — pull-up rolling 3-session max (§1.8, tracking only).

### Write boundary — Option C (Fork 7c preserved; the load-bearing invariant)

`commit_session` (`ironlog/generation/loop.py`) is and REMAINS the **sole writer of `current_load`**, persisting `prospective_current_loads` at **approval time** — so a discarded/regenerated session never advances a load. `run_analysis` writes performance **bookkeeping** (e1rm, tier, ceiling counters, the confirmation streak, `active_rule`, `stall_signal`) and **must never write `current_load`**. The progression engine is the sole **decider** of advancement (the rules that feed `prospective_current_loads`); it is **not** a new writer.

- **Analysis-time (extend `run_analysis`):** the earned bookkeeping — counters, streak, `active_rule` transitions, e1rm/tier, `stall_signal`. Facts about the logged session.
- **Decide (new engine):** the rule dispatch that reads that bookkeeping + logged performance and computes the prospective prescription (load/assist/incline/rep-target/body-position).
- **Persist (`commit_session`, unchanged):** writes the prospective prescription on approval.

**Enforcement guardrail (this replaces the naive "generation never writes current_load"):** assert **`run_analysis` never writes `current_load`** at the repository layer. Generation IS the writer, by design.

Per-session input the engine consumes (from `SetLog`): `movement_id, day_id, reps_performed(actual_reps), rpe/feedback_tap, weight(actual_load)` per working set.

---

## 7. Reuse anchors (extend, do NOT reinvent — verified to exist)

| Module/symbol | Reuse for | Do NOT |
|---|---|---|
| `ironlog/engine/e1rm.py` — `estimate_e1rm(load,reps,target_rpe,tap)` | RPE-adjusted e1RM for the trend arm | build a parallel e1RM |
| `ironlog/engine/stall.py` — `detect_stall` + its constants | core stall signal | reimplement / add a 2nd constant set |
| `ironlog/engine/progression.py` — `resolve_objective`, `should_attempt_progression`, `step_down_tier`, `maybe_reset_tier_on_breakthrough` | the increment-tier primitives the rules wire onto | treat as "the engine" — it's the primitives layer (42 lines) |
| `Movement.increment_ladder` + `MovementState.current_increment_tier` | advance the tier index → only-loadable values (rounding solved) | hardcode +5 / round raw loads |
| `ironlog/persistence/run_analysis.py` — `run_analysis` | the per-session evaluation seam to extend for bookkeeping | add a separate parallel hook |

The engine is largely: **rule dispatch + wire onto these primitives + emit the prospective prescription / bookkeeping.**

---

## 8. Test cases (engine unit tests)

RPE-8 T1 single-session advance (Bench 3×8 @≤8 → +tier). RPE-8 T2 two-session confirm (streak 1 → 2 → advance). Streak reset on any miss. Non-advance on RPE>8 and on missed reps. HT rule-driven +5 RPE-exempt (grind at RPE 9 still advances). HT ceiling clamp + rule transition to rep-ladder-at-cap at 220. Rep-ladder-at-cap (Belt Squat 260 → reps 8→10). Incline reduction (Nordic 20°→15° after 2 clean). **Unilateral both-sides-AND** (advance only when both sides clear — gotcha). Assistance reduction (Reverse Nordic 20→15; BW→loaded transition). Body position (Dragon Flag tuck→single_leg). Fixed-load no-progression (Reverse Hyper Recovery stays 90). Independent tracks (HT D2 advances, D5 unchanged). Maintenance terminal (Scout RH at 25 → no stall while met). Stall FAILED_PROGRESSION (low→high). Stall PLATEAU via e1RM trend. REGRESSION. Stall reset on advance. **Write-boundary: `run_analysis` never writes `current_load` (guardrail test); the engine's advancement flows through prospective→commit.**

---

## 9. Handoff / build notes

- Read alongside `ironlogdecisionarchitecture.md`.
- Confirmation window is a per-rule state-machine parameter (T1=1, accessory=2, V-Bar override=1); reset the streak on any non-advance (don't accumulate across gaps).
- Ceiling behavior is a state *transition* (`active_rule`), not just a clamp.
- RPE-exempt movements (HT) skip RPE validation entirely.
- Unilateral: AND both sides' clearance.
- The engine NEVER invokes AI — emit `stall_signal` and stop.
- Fallback invariant: any rule failure → keep `current_load` unchanged ("your program yesterday, verbatim"), log it.
- Migrations: additive-nullable, single-statement-atomic OR idempotent-guarded, + the parity keystone.
- Tests run on myflix; NO `from __future__ import annotations`.
