# Long-Range Periodization: Macrocycle / Mesocycle / Microcycle — Design

## Background

IronLog-V2 currently has no first-class concept of training time beyond
"today's session." `Phase` (`ironlog/models/enums.py`, `PhasePolicy` /
`EngineState` in `ironlog/models/library.py`) is a single overloaded enum
(`CALIBRATION | CUT | STAB | REBUILD`) that conflates body-composition
status, recovery-gated RPE/volume defaults, and (unimplemented) training-block
structure. `meso_number` (`MesoRotation` table) is an arbitrary int, manually
passed by callers (mostly hardcoded `meso_number=1`), used only for per-slot
movement substitution — it has no duration, no start/end, no auto-increment,
and no relationship to calendar time. `WeekParityRotation` is the only
genuinely calendar-driven mechanism today, and it's a binary A/B toggle keyed
off ISO calendar-week parity. No deload mechanism exists anywhere — it's
mentioned only as unimplemented prose in `docs/03_progression_model_spec.md`.

This design replaces that with a real macrocycle → mesocycle → microcycle
hierarchy, decomposes `Phase` into orthogonal state axes that resolve into a
session's effective training envelope, and defines a clean migration off the
old model. It does **not** change the D1–D6 day/tier/movement structure
(that's `program_seed.py`/the seed yaml, unaffected) — this is purely a new
time/state layer sitting above session generation.

This document reflects an extended human-in-the-loop design session,
including two rounds of independent architecture review (codex + gemini via
`consensus_multi_query`) and five substantive corrections the athlete made to
the reviewed design before approval — see "Corrections from review" at the
end of each relevant section for the specific gaps closed.

## Architecture invariants this design must not violate

Per repo-root `CLAUDE.md`:
1. **Rules dispose; the model proposes.** The new policy resolver (below) is
   100% deterministic. The LLM proposer receives its output as context and
   cannot loosen it.
2. **Definition vs State.** Static template/policy data (MesocycleTemplate,
   posture policy tables) vs. time-varying instance data (Mesocycle,
   Microcycle, BodyCompState, RecoveryStatus, DeloadState) follow the same
   split `Movement`/`MovementState` already establishes.
3. **Planned vs Logged.** Extended to this layer: Microcycle's planned vs.
   actual execution state is the same pattern as `PlannedSet`/`SetLog` — the
   delta is itself meaningful data, never collapsed.
4. **The capture fix** (`SetLog.feedback_tap` mandatory, `is_warmup` a real
   column) is untouched by this design — it operates one layer below
   anything described here.
5. **Objective gating** is unaffected — `objective_override`/phase-default
   resolution in `ironlog/engine/progression.py` continues to work, just
   reading `BodyCompState`'s policy instead of `PhasePolicy`.
6. **Locked reference data** — none of this design touches equipment floors,
   HT bands, or existing caps.

## 1. The hierarchy

```
Macrocycle (long-range goal, metadata/planning only)
  └── Mesocycle (instance of a MesocycleTemplate; a training block)
        └── Microcycle (one planned week within the mesocycle)
              └── Session (existing D1–D6, unchanged)
```

A Mesocycle is not required to belong to a Macrocycle — standalone
mesocycles are valid (a Macrocycle is a convenience container for sequencing
multiple mesocycles toward a goal, not a required parent).

## 2. State axes and resolution order

Four axes replace `Phase`. They are not fully independent in *effect* (both
review passes correctly flagged this) — they resolve in a fixed order into
one deterministic envelope per session:

```
Mesocycle template baseline (planned_posture for this microcycle)
  → BodyCompStatePolicy   (redefines what the posture MEANS under current body comp)
  → RecoveryStatusPolicy  (today's execution adjustment from RHR/sleep/HRV)
  → DeloadPolicy          (state-agnostic override; fires only on persistent,
                            accumulated evidence — see §5)
= effective envelope (RPE cap, volume multiplier, progression eligibility,
  optional-work eligibility)
```

**Why BodyCompState modifies the baseline rather than the deload
threshold:** the naive fix for "CUT keeps triggering deload and cancelling
PUSH weeks" is to make deload harder to trigger during a CUT. That's wrong —
it teaches the system to discount genuinely poor recovery just because a CUT
is in progress. Instead, `BodyCompState=CUT` changes what a given
`training_posture` *prescribes* (e.g. PUSH-under-CUT already has a lower
volume ceiling and RPE cap than PUSH-under-MAINTENANCE). Hitting that
already-reduced envelope isn't "recovery failure requiring intervention" —
it's the plan working as intended. `DeloadPolicy` stays state-agnostic and
fires on the same evidence bar regardless of BodyCompState.

- **`Mesocycle` / `MesocycleTemplate`** — training-block structure. A
  template is a reusable, ordered list of `training_posture` values (open
  vocabulary — `ESTABLISH`, `BUILD`, `PUSH`, `CONSOLIDATE`, `INTENSIFY`,
  `PEAK`, `DELOAD`, more may be added later). **Never described as a fixed
  accumulation → intensification → deload cycle** — that was explicitly
  rejected. A template's default length is 4 microcycles but this is a
  template property, not a hardcoded constant. A Mesocycle *instance* = a
  template + its own exercise/stimulus-variant selection for that specific
  block (e.g. landmine press instead of seated OHP).
- **`BodyCompState`** — `CUT | MAINTENANCE | GAIN`. Its own independent
  timeline; can change mid-macrocycle without requiring a new macrocycle.
- **`RecoveryStatus`** — computed from the existing RHR/sleep/HRV capture
  pipeline (confirmed already present in this codebase — see the untracked
  `.specs/24-withings-credentials-model.md` etc. in the current working tree,
  which is that pipeline's own design trail). This design only consumes it;
  it does not build new capture.
- **`DeloadState`** — adaptive, not templated. Triggers on **persistence**:
  multiple suppressed RecoveryStatus readings + performance regression
  (RPE trend, rep-target misses, stalls) + accumulated fatigue signals, not
  a single bad night. A sufficiently severe single signal may still trigger
  immediate intervention, but ordinary noise must not. When active, it
  overrides the resolved envelope; it does not silently replace the
  microcycle's *planned* posture (see §3).

## 3. Microcycle

A Microcycle is a calendar-anchored, drift-tolerant week — not a rigid
7-day box, and not purely session-count-driven either (both were considered
and rejected: fixed-calendar corrupts weekly accounting when training
naturally shifts a day or two; pure session-count loses the time dimension
mesocycle-length planning needs).

**Fields:**
- `mesocycle_id`, `ordinal` (position within the mesocycle — this is also
  the parity key for exercise rotation, see §6)
- `planned_start_date`, `planned_end_date`
- `actual_start_date`, `actual_completion_date` (nullable until known)
- `expected_sessions`, `completed_sessions`
- **Lifecycle status** (mutually exclusive): `NOT_STARTED | ACTIVE |
  COMPLETE | ABORTED`
- **Schedule-drift status** (independent axis, can combine with any
  lifecycle status — e.g. `ACTIVE` + `EXTENDED` is valid):
  `ON_TIME | EXTENDED | DRIFT_FLAGGED`
- `drift_days` (numeric, computed)
- `planned_posture` — resolved from the template, **immutable once set**
- `effective_posture` — normally equals `planned_posture`; reflects the
  session-time resolved outcome only for the purpose of the human-readable
  "what actually happened" view. The deload override itself is **not**
  written back into `planned_posture`, ever.

**Drift guardrail thresholds (0–2 days tolerate, 3–4 flag, beyond that
terminate/replan) are policy/configuration, not baked into the domain model
or schema** — they're a tunable input to the drift-status computation, not
an enum's fixed meaning.

**Shift vs. skip:** a session logged late against its planned microcycle
slot is `shifted`; a planned slot never logged and explicitly abandoned is
`skipped`. This distinction feeds volume/recovery accounting and drift
computation but does not rename or renumber the session's D1–D6 identity —
a Sunday-planned D6 performed Monday is still D6, still counted in the
microcycle it was planned for.

*Corrections from review:* the lifecycle/drift split and the
planned/effective posture split were both athlete corrections to the
originally reviewed design — the reviewed version had a single
`execution_status` enum conflating lifecycle and drift, and a single
`training_posture` field that adaptive deload would have silently
overwritten, destroying the historical record of what was actually planned.

## 4. Macrocycle

Pure planning/container layer. **Explicit non-goal:** no session
generation, progression, RecoveryStatus, BodyCompState, deload, or posture
resolution logic reads from Macrocycle, in this pass or any until a real
need is demonstrated. Its only job: goal metadata (free text or lightly
structured — e.g. "cut ~20lb preserving lean mass"), an ordered sequence of
Mesocycle instances, and planned-vs-actual timeline/status for long-range
visibility.

Both independent reviews (codex, gemini) flagged Macrocycle as the
design's biggest YAGNI risk. The athlete's counter-argument, accepted: at
its current scope (a goal string + an ordered FK list + planned/actual
dates, zero behavioral logic) it costs almost nothing to build, and skipping
it now would likely mean re-deriving the same sequencing relationship
implicitly later (via `previous_mesocycle_id` chains, ordinal sorting, or
UI-side assumptions) and having to retrofit a real container afterward —
more expensive than building the cheap version now. It stays in scope, kept
deliberately inert.

## 5. Deload

Not a templated week. `DeloadPolicy` evaluates accumulated evidence —
persistent RecoveryStatus suppression across multiple readings, performance
regression (RPE trend, rep/load misses, stalls), and/or unusual
soreness/fatigue signals — and, when the evidence bar is met, overrides the
resolved envelope for the current microcycle (reduced volume/intensity, no
progression attempts). This can happen during, instead of, or after any
planned posture, or not at all in a given mesocycle. A `MesocycleTemplate`
*may* explicitly include a planned deload microcycle where appropriate (e.g.
a dedicated "planned deload block" template), but deload is not assumed as
every template's automatic last week.

## 6. Movement rotation: `MesoRotation` and `WeekParityRotation`

`MesoRotation.meso_number` (arbitrary int) is replaced with a real
`mesocycle_id` FK — the table's role (per-slot movement substitution for a
specific block) doesn't change, only what identifies the block. Existing
resolution precedence in `skeleton.py` is preserved structurally:

```
SlotMovementOverride > MicrocycleParityRotation > MesoRotation > default
```

**`WeekParityRotation` must be renamed/re-keyed to resolve off
`Microcycle.ordinal` parity, not calendar-ISO-week parity — this was a
correctness gap in the originally reviewed design, not a naming
preference.** Once a Microcycle is allowed to extend across a calendar-week
boundary (§3), a resolver still keyed on calendar week could flip the A/B
rotation mid-microcycle, corrupting the exact invariant this whole design
protects (a microcycle's identity/content shouldn't drift out from under
it due to calendar mechanics it doesn't control). `MicrocycleParityRotation`
resolves parity from the owning `Microcycle.ordinal`, which is stable for
the microcycle's full lifetime regardless of how long it actually runs.

## 7. Session-level provenance

`Session.phase` becomes **historical-only** — never repurposed to mean
mesocycle/posture/BodyCompState under the new model, so old and new rows
never carry silently different semantics under the same field name.

Its replacement is a **generation-time snapshot**, sized to answer "why did
the engine prescribe this?" months later without needing today's policy
tables to still say the same thing they said then:

```
prescription_snapshot (JSON, written once at generation time):
  macrocycle_id (nullable)
  mesocycle_id
  microcycle_id
  planned_posture
  body_comp_state
  recovery_status
  deload_state (active/inactive + trigger reason if active)
  resolved_envelope (rpe_cap, volume_multiplier, progression_mode, optional_work_eligible)
  resolver_policy_version
```

Changing policy defaults next month must not rewrite the explanation of why
yesterday's session was generated the way it was — this snapshot is the
mechanism that guarantees that.

## 8. Resolver explainability

The deterministic policy resolver (§2) must emit not just the final
envelope but the trace of how it got there — each axis's contribution,
in resolution order:

```
Base (from planned_posture=PUSH):
  volume_multiplier = 1.00, rpe_cap = 8.5, progression = ACTIVE

BodyCompState=CUT:
  volume_multiplier -> 0.90, rpe_cap -> 8.0

RecoveryStatus=CAUTION:
  progression -> HOLD_IF_BORDERLINE, optional_work -> SUPPRESS

DeloadState=NONE

Effective:
  volume_multiplier = 0.90, rpe_cap = 8.0, progression = HOLD_IF_BORDERLINE,
  optional_work = SUPPRESS
```

This must be deterministic and unit-testable independent of the LLM layer —
"why did IronLog lower my target today" must be answerable by reading the
trace, not by asking the LLM to reconstruct its own reasoning. At minimum a
condensed form of this trace is exposed via the read API (§9); full tracing
may remain internal/debug-only.

## 9. API surface (this pass)

Minimal, read-only:
- `GET /training/plan/current` — current macrocycle (if any) / mesocycle /
  microcycle, resolved planned + effective posture, planned-vs-actual/drift
  status, active BodyCompState, RecoveryStatus, active DeloadState if any,
  condensed resolver trace.
- `GET /training/macrocycles/{id}` — goal metadata, ordered mesocycles,
  planned-vs-actual timeline/status.

Out of scope this pass: create/edit/reorder endpoints, plan-builder
workflows, any client UI. Per the client-contract section of CLAUDE.md,
these are new endpoints (additive), not a change to any existing response
shape — no client (`IronLog-V2-Client`) DTO changes required for this pass.

## 10. Migration off `Phase`

**Clean one-time cutover, rehearsed with pre-cutover shadow validation
against recent historical sessions — no period where old `Phase` and the
new model are both authoritative.** Running both as live decision-makers
recreates exactly the ambiguity this redesign exists to remove.

- `EngineState.current_phase` is split, not blindly remapped:
  - `CUT → BodyCompState.CUT`, `STAB → BodyCompState.MAINTENANCE` — the two
    unambiguous cases.
  - **`CALIBRATION` and `REBUILD` are audited individually, not
    auto-mapped.** `CALIBRATION` likely belongs to system/program lifecycle
    state, not `BodyCompState`. `REBUILD` likely represents a
    training-volume/recovery posture, not necessarily `BodyCompState.GAIN`
    — do not force either into the body-comp enum purely to make the
    migration mechanical.
  - `RecoveryStatus` is **computed fresh** from the existing physiological
    pipeline at cutover time, never derived by translating the old `Phase`
    value.
- An initial Macrocycle/Mesocycle/Microcycle is seeded representing the
  athlete's actual current position (the live APEX Bridge block), not a
  restart at week 1 of a new mesocycle.
- `MesoRotation.meso_number` values are migrated to the corresponding
  seeded `mesocycle_id`; if provenance matters, the old int may be kept
  temporarily as migration metadata but never participates in runtime
  resolution again.
- Old `Phase`/`PhasePolicy`/`EngineState.current_phase` logic is removed as
  a source of truth at cutover. Anything worth preserving must already have
  been reassigned to `BodyCompState`, `RecoveryStatus`, mesocycle posture,
  or `DeloadPolicy` before cutover — the migration step itself makes no new
  design decisions.
- A short-lived **read-only compatibility shim** for `current_phase` is
  acceptable if something still expects the old field, but it must be
  derived from the new state (or explicitly marked deprecated) and must
  never feed back into training decisions.

## Non-goals (this design pass)

- Write/edit/reorder API endpoints, plan-builder UX, client UI.
- New physiological data capture (RecoveryStatus consumes what already
  exists).
- Macrocycle-level engine behavior of any kind.
- A fixed accumulation → intensification → deload template — templates are
  open-vocabulary posture sequences, not this specific cycle.
- Changes to D1–D6 day/tier/movement structure (`program_seed.py`, the seed
  yaml) — unaffected by this design.

## Open questions carried forward (not blocking)

- Exact `training_posture` → deterministic-knob mapping table (the concrete
  numbers per posture × BodyCompState combination) is implementation-detail,
  deferred to the implementation plan rather than fixed in this design doc.
- Exact DeloadPolicy evidence thresholds (how many suppressed readings,
  what performance-regression magnitude) — same, deferred to implementation
  and expected to need real-data tuning.
- Whether `CALIBRATION`'s post-audit home is a new lightweight enum or
  reuses an existing program-lifecycle concept — deferred to migration
  implementation once the audit in §10 is actually done.
