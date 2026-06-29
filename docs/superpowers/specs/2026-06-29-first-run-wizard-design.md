# IronLog-V2 — First-Run Wizard Design (load configuration)

**Status:** design closed, awaiting spec-review gate → writing-plans
**Date:** 2026-06-29
**Parent context:** the next chunk to 1.0-beta. Generation (v0.6) + logging (PR #7/#3) are done; the structure-only seed is live (108 movements, program, MovementState empty). This chunk takes the seeded program from "structure with no trustworthy loads" to "configured + trainable" — the user-facing path that sets working loads, replacing the cancelled seed-all-with-guessed-loads.
**Out of scope:** HT composite loading (v0.7 — beta HT = `current_load` at the 220 bar cap), rep-range fidelity (v0.7 — known, the dry-run showed it bite), the multi-program catalog/switch *UI* (thin addition when program 2 exists), notes classify-apply, `set_role` hardening.

---

## 0. The spine (one mechanism, four forks)

The whole design is **one computation surfaced in three places**: `compute_load_trust(movement, as_of)` is used by (a) generation's load resolver, (b) the wizard-state endpoint, and (c) the wizard's completion gate. Because all three share the function, they cannot disagree — finishing the wizard *means* generation prescribes cleanly, by construction.

The governing discipline across every fork: **store the events, derive the verdicts; never store a verdict.** Every "state" (trust, active-program) is a computed read over facts, so nothing can drift.

---

## 1. Fork 1 — Load-trustworthiness is COMPUTED, not stored

The dry-run (Finding A) showed empty MovementState makes generation silently resolve to equipment **floors** (Bench → 45 = empty bar) — a fake-looking prescription, the silent-wrong class. Resolution: **don't prescribe a number you don't have; say you don't have it.**

- **`compute_load_trust(movement, as_of)` is derived each call** from facts that already exist — never a stored flag. It returns one of: `UNKNOWN` (no real load → needs-calibration, refuse to prescribe), `STALE` (real load but old → prescribe-with-confirm), `FRESH` (real load, recent → use as-is).
- **`resolve_start_load` change (the surgical edit):** the floor fallback (`assembler.py:60`, `return movement.load_floor … else 0.0`) becomes a **needs-calibration signal** when no real load exists — generation does NOT emit a fake floor.
- **Two trust axes kept separate (do NOT conflate):** the existing `MovementState.calibration_status` (`INHERITED`/`CALIBRATING`/`MEASURED`) means "is the e1rm *measurement* trusted" and stays wired to the analysis calibration-flip flow (`run_analysis`/`apply.py`/`calibration.py`) — **untouched by this chunk.** Load-trustworthiness ("does a real working load exist + is it recent") is a *different axis*, its own derived computation. A movement can have a fresh real load while its e1rm is still CALIBRATING — orthogonal.

**What counts as "a real load" — keyed off `progression_mode` (the derivable rule flagged for gate review):**
| progression_mode | the load field | needs-calibration when |
|---|---|---|
| LADDER | `current_load` | no `current_load` |
| COMPOSITE (HT) | `current_load` (bar load, ≤220 cap; plates+band composite deferred to v0.7) | no `current_load` |
| ASSISTED (Pull-up, Nordic) | `assist_level` (progress by reducing assistance) | no `assist_level` |
| PROTOCOL / CONDITIONING / NONE (bodyweight: Ab Wheel, Dips, Dragon Flag, Face-Up Knee Raise, Sissy) | none — no load to set | **never** (always trivially FRESH; the wizard never asks for these) |

So `compute_load_trust` first asks "does this movement need a load at all?" (per mode) — bodyweight/protocol movements are always FRESH (no load to configure); the rest check the canonical load field's presence + recency.

---

## 2. Fork 2 — Trust is anchored on confirmation-as-an-event-fact

- **`MovementState.confirmed_at`** (new timestamp column) = "when the user last *vouched for* this load" (set it in the wizard, or confirmed a stale one). It is an **event-fact**, in the same category as `SetLog.performed_at` — NOT a stored verdict. (Contrast the rejected stored `NEEDS_CALIBRATION` flag, which would be a verdict that drifts. `confirmed_at` doesn't drift because events don't change; the verdict derived from it updates with time automatically — a confirmation 40 days ago computes to STALE today.)
- **Recency = `max(performed_at_of_last_working_set, confirmed_at)`.** A load is FRESH if recency is within **30 days** of `as_of`; STALE if the load is present but recency > 30 days; UNKNOWN if no real load (Fork 1).
- **Pin (spec it explicitly): `confirmed_at` is a FACT, not a verdict. Trust stays computed** — `compute_load_trust` reads the load field + `max(performed_at, confirmed_at)` and derives UNKNOWN/STALE/FRESH every call. Store the event, derive the verdict.
- **"30 days from confirmation," not "from program-start"** — this is the realized intent, truer than the literal pin: a load is fresh because *you vouched for it recently* (lifted it or confirmed it), not because the calendar says the program is young. It fixes the carryover hole the literal program-start reading had (a load carried from a prior program, last trained 40 days ago, would have read FRESH under a program-start anchor the moment the new program started — exactly backwards; anchoring on confirmation keeps it correctly STALE until you vouch).
- **Per-movement granularity** (a consequence, and correct): you can re-confirm one movement mid-program (re-tested bench → confirm → fresh) without resetting any other movement's clock. Trust IS per-movement, so the fact that establishes it is per-movement.

---

## 3. Fork 3 — Active program: a pointer (structural single-active), not a status flag

- **`EngineState.active_program_id`** (new FK on the global singleton) is the single source of "which program is active." Single-active is **structural** — one FK cannot point to two programs, so "two active programs" is unrepresentable (vs a per-program `status` field where "only one ACTIVE" would be a policed, drift-prone invariant).
- **`Program.started_at` / `Program.ended_at`** (new timestamp columns) are **event-facts** (when this block began/ended). **"Is this program active" is DERIVED** (`program.id == EngineState.active_program_id`) — never a stored per-program active flag. (Same store-event-derive-verdict discipline as Forks 1/2.)
- **`lay_skeleton` scopes to the active program.** Today it finds a `ProgramDay` by `day_role` alone (`.first()`, no program scoping — a single-program assumption). It must filter `ProgramDay` to `EngineState.active_program_id`. (Required edit regardless of option.)
- **Carryover = automatic, no transform (pin the model).** `MovementState.movement_id` is unique → state is **per-movement-GLOBAL, not per-program**. Loads *persist* across programs; starting a new program does not copy or deload them. A movement shared with the prior program keeps its load (surfaced STALE if untrained > 30d → wizard confirms); a movement new to the next program has no MovementState → UNKNOWN → wizard asks. **Pin: "bench's load" is one global thing across all programs** — correct for phased progression (Phase 2 bench continues Phase 1 bench). Same-movement-at-program-specific-loads would be a *model change*, not a config — out of scope, global is right. No carryover math (adjust the number at confirm if a deload is wanted).
- **Scope: data multi-ready, UI minimal-for-beta.** The data model (active pointer + start/end facts) supports multiple programs from the start — not speculative; Phase 1 → Phase 2 is ~4 weeks out. The UI is beta-minimal: "start this program" (the wizard, on completion) + the *seam* for "start the next one later." The catalog/switch screen is a thin addition when program 2 exists.

---

## 4. Fork 4 — The wizard flow: a single needs-attention screen rendering `compute_load_trust`

- **Operates over** the active program's distinct movements (the ~34 referenced by its TierExercises + meso rotations), NOT all 108 library movements. Bodyweight/protocol movements (no load) never appear.
- **Presentation (Option C):** one screen that *is* `compute_load_trust` rendered — **UNKNOWN → empty field to fill, STALE → prefilled value to confirm/adjust, FRESH → collapsed/summarized ("28 ready, N need attention").** The user touches only the un-fresh set (which is exactly the work). A live **"N left" counter** makes the completion gate visible (ticks to 0).
- **One flow, not two (pin as a NON-FORK):** first-run (all-UNKNOWN) and start-new-program (STALE/FRESH mix) are the **same screen rendering the same computation** — trust states differ, the code path does not. There is no "first-run wizard" branch, ever. State this so it is never re-litigated.
- **Completion gate = generation's predicate:** no UNKNOWN + no unconfirmed-STALE for any active-program movement → "Start program" enables → stamp `Program.started_at`, set `EngineState.active_program_id`, generate overview + Day 1. The gate IS `compute_load_trust` returning FRESH for every program movement — so finishing the wizard **guarantees generation prescribes cleanly** (Finding A's spine closes by construction).
- **Write (batch):** the wizard is a config action done at home on wifi — a single **batch POST** of resolved loads (no offline-durable per-set machinery — that was for the gym). For each resolved movement: set the canonical load field (`current_load` / `assist_level` per mode) + **stamp `confirmed_at = now`**.
  - **Refinement (pin): stamp `confirmed_at` ONLY on movements the user actively resolved** (filled an UNKNOWN or confirmed/adjusted a STALE) — **NOT** on untouched-FRESH movements (they're already fresh via `performed_at`; stamping them would record a confirmation that didn't happen). Keeps `confirmed_at` honest: "you vouched," not "the wizard ran."
- **Generation behavior on the two un-fresh states (derived from Fork 1/2, pinned for clarity):** UNKNOWN → **refuse to prescribe** (no number exists) — flag the movement needs-calibration, prescribe the rest of the session normally. STALE → **prescribe the (real, if old) load WITH a confirm flag** (a STALE load has a usable number, unlike UNKNOWN). In the normal flow the completion gate means generation runs only when all-FRESH; these behaviors are the defensive/mid-program cases (a load goes STALE after 30 untrained days; a new movement appears).

---

## 5. The two-repo contract (the server↔client crossing artifact — locked)

Same discipline as logging: two repos, two build paths, so the endpoint shapes are an explicit locked contract; server built-and-tested-stable before the client wizard screen.

**`GET /wizard/state` → `WizardStateResponse`** (the active program's load-config state — *just `compute_load_trust` rendered per movement*, reusing the one shared function):
```
WizardStateResponse:
  program_id: int
  program_name: str
  needs_attention_count: int        # UNKNOWN + unconfirmed-STALE (the "N left")
  movements: WizardMovement[]        # active-program movements that NEED a load (bodyweight excluded)

WizardMovement:
  movement_id: int
  movement_name: str
  load_field: str                    # "current_load" | "assist_level" (per progression_mode)
  trust: str                         # "UNKNOWN" | "STALE" | "FRESH"
  prefill_value: float | null        # the current value for STALE/FRESH; null for UNKNOWN
  unit_hint: str | null              # e.g. "lb" / "assist (band/tube)" — display aid
```

**`POST /wizard/resolve` → `WizardResolveResponse`** (batch-write the resolved loads):
```
WizardResolveRequest:
  resolutions: WizardResolution[]    # only the movements the user touched
WizardResolution:
  movement_id: int
  value: float                       # the entered/confirmed load (into load_field)
POST writes: the load field (current_load|assist_level per mode) + confirmed_at = now, for each.

WizardResolveResponse:
  resolved: int
  needs_attention_count: int         # recomputed after the write (0 = ready to start)
  ready_to_start: bool               # needs_attention_count == 0
```

**`POST /programs/{id}/start` → starts the program** (set `active_program_id`, stamp `started_at`); returns the generated overview / today. (May be folded into `/wizard/resolve` when `ready_to_start` — a plan-level detail.)

The client mirrors these as Kotlin DTOs field-for-field (snake_case), same as the logging contract.

---

## 6. Schema changes + migrations

This chunk has real schema changes (unlike logging) — migration authoring rule applies (single-statement-atomic or idempotent; extend the parity test):
- `MovementState.confirmed_at` (datetime, nullable) — Fork 2.
- `EngineState.active_program_id` (int FK → program.id, nullable) — Fork 3.
- `Program.started_at`, `Program.ended_at` (datetime, nullable) — Fork 3.

On apply: the live V2 DB was just structure-seeded (2026-06-29); these are additive nullable columns. Reversible (the seed is reproducible).

---

## 7. Named test targets (the make-drift-impossible gates)

1. **`compute_load_trust` correctness:** UNKNOWN (no load field value), STALE (value present, `max(performed_at, confirmed_at)` > 30d), FRESH (within 30d); bodyweight/protocol movements always FRESH (no load needed). Per progression_mode load-field selection.
2. **Shared-function consistency (the spine):** generation's resolver, the `GET /wizard/state` endpoint, and the completion gate all derive trust from the SAME `compute_load_trust` — a test that the wizard-state trust for a movement equals what generation sees (they cannot disagree). This is the load-bearing coherence gate.
3. **`confirmed_at` is honest (stamp-only-on-touched):** a FRESH-untouched movement's `confirmed_at` is NOT changed by a wizard resolve that didn't include it; a resolved movement's `confirmed_at` IS stamped. (The event-fact integrity pin.)
4. **needs-calibration not floor (Finding A):** `resolve_start_load` / generation returns a needs-calibration signal for an unconfigured movement, never a fake equipment floor. Test against an empty-MovementState movement (Bench must NOT come back 45).
5. **Single-active is structural:** `EngineState.active_program_id` scopes `lay_skeleton`; with two programs seeded, generation targets the active one; "active" is derived from the pointer.
6. **Completion-gate ⇒ clean generation (Finding A closes by construction):** after the wizard's gate clears (all program movements FRESH), generation prescribes real loads with zero needs-calibration flags — the wizard-finishing-guarantees-clean-generation property.
7. **Carryover persists (global MovementState):** a movement's load set under program 1 is present (as STALE if old) when program 2 becomes active; a movement new to program 2 is UNKNOWN.
8. **Two-repo contract:** server Pydantic ↔ client Kotlin DTOs field-for-field (the crossing artifact).
9. **Migration parity:** chain (incl. the new columns) == `create_all`.

Server tests on myflix (`ssh myflix … pytest`); client via Gradle. NO `from __future__ import annotations` (server).

---

## 8. Settled-permanently vs settled-for-beta

| Decision | Status |
|---|---|
| Fork 1 computed trust + resolve_start_load → needs-calibration; two trust axes separate | **Foundational-locked** |
| Load-field-per-progression-mode rule (§1 table) | **Locked — flagged for gate confirmation** |
| Fork 2 `confirmed_at` event-fact; recency = max(performed_at, confirmed_at); 30d; store-event-derive-verdict | **Foundational-locked** |
| Fork 3 `active_program_id` pointer + started_at/ended_at facts; active derived; lay_skeleton scopes; carryover global | **Foundational-locked** |
| Load is per-movement-global (not per-program) | **Foundational-locked (pinned)** |
| Fork 4 needs-attention wizard (C); one-flow-not-two; gate=generation-predicate; batch write; confirmed_at-only-on-touched | **Foundational-locked** |
| HT = `current_load` (bar, composite deferred) | **Beta — composite is v0.7** |
| Multi-program catalog/switch UI | **Deferred — data multi-ready, UI when program 2 exists** |

---

## Composition

One `compute_load_trust` runs through generation's load resolver, the wizard-state endpoint, and the completion gate — so the wizard finishing *is* generation prescribing cleanly, by construction (Finding A's spine, closed). Every state in the system (trust, active-program) is a derived read over event-facts (`current_load`/`assist_level`, `performed_at`, `confirmed_at`, `active_program_id`, `started_at`) — store the event, derive the verdict — so nothing can drift. This turns the structure-seeded-but-loadless program into a configured, trainable one, and closes the last gap before training on the loop: the app stops prescribing fake floors and starts prescribing loads you vouched for.
