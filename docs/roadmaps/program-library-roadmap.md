# IronLog V2 — Program-Library Implementation Roadmap

**Date:** 2026-09-10
**Depends on:** [ADR 0001](../adr/0001-program-library-architecture.md) (architecture
decision — read that first),
[current-state audit](../design/program-library-current-state-audit.md),
[external research](../design/program-library-external-research.md).

This roadmap sequences the detailed rollout. It adjusts the original research brief's
Phase 0–9 sketch based on what the audit actually found: several capabilities the brief
described as future work already exist. Each phase below is tagged:

- **GENERALIZE EXISTING** — a working mechanism already proves the pattern; extend its
  vocabulary/scope, don't replace it.
- **EXTEND EXISTING** — a related mechanism exists (e.g. `PlanStatus` on `Macrocycle`);
  apply the same shape to a new place.
- **NEW CAPABILITY** — genuinely new architecture, no existing analog.
- **DEFERRED** — out of scope for the phases below; named so it isn't silently dropped.

| Concept the brief called "future" | What the audit found | Roadmap tag |
|---|---|---|
| Typed bounded LLM selections | Already live — `Selections`/`SlotSelection` in `proposer.py` | GENERALIZE EXISTING |
| Deterministic proposer → validator pipeline | Already live — `propose_validate_repair` → `assemble()` → `validator.py` | GENERALIZE EXISTING |
| Program hashing/drift detection | Already live — `program_hash.py`, compared at commit time | EXTEND EXISTING (becomes revision-integrity check) |
| Macrocycle/Mesocycle lifecycle infrastructure | Already live — `PlanStatus` enum, `planning_state` | EXTEND EXISTING (pattern reused for `ProgramInstance`, new enum — see ADR) |
| Long-range periodization objects (Macrocycle/Mesocycle/Microcycle) | Already live and mature | EXTEND EXISTING |
| Program definition/revision/instance | `Program` conflates all three, no revision field | NEW CAPABILITY (ADR 0001) |
| Equipment/capability model | `Equipment` is a flat global catalog, `equipment_tags` is an unstructured list | NEW CAPABILITY |

---

## Phase 0 — Architecture and domain audit (COMPLETE)

Delivered as this document set: current-state audit, external research, ADR 0001, this
roadmap. No code changed.

---

## Phase 1 — Revision & instance foundation

**Tag:** NEW CAPABILITY (schema) + EXTEND EXISTING (lifecycle pattern)

Implements ADR 0001's full implementation sequence (1–8) as one gated slice — **no AI
behavior expansion and no additional production programs until this entire slice is
proven**, per the ADR's revision history.

### 1.1 Expanded golden APEX baseline

Not just the final generated workout. Capture representative fixtures including:

- resolved program topology
- prescription hash and topology hash
- candidate menus presented to the proposer
- the LLM-invocation decision (quiet-week gate fired or not)
- LLM selections, where deterministic fixtures permit
- validator result
- final planned exercises and planned sets
- progression result
- weak-point behavior
- readiness modification
- missed-day behavior
- mesocycle/microcycle identity

This baseline is what ADR 0001's acceptance invariant 10 is checked against — a narrower
baseline (just the final workout) would let a real regression through undetected in any
of the above dimensions.

### 1.2 `ProgramRevision` schema + explicit publish lifecycle

Introduce `ProgramRevision` and the normalized revision-table family
(`ProgramRevisionDay`, `ProgramRevisionTier`, `ProgramRevisionExercise`,
`ProgramRevisionMesoRotation`, `ProgramRevisionParityRotation`,
`ProgramRevisionSlotMovementOverride`), plus a `publish` operation (validate → materialize
→ hash → commit, atomically — see ADR "Revision Execution Model"). `ProgramRevisionExercise`
includes slot-requirement typing (`ANCHOR`/`SEMI_ANCHOR`/`ADAPTIVE_SLOT`) from the start,
even though the resolver/scorer that consumes it is Phase 4–6 work — this avoids a
breaking re-migration of already-published revisions later. `ProgramRevisionEquipmentRequirement`
(revision-bound, immutable) is introduced alongside — see Phase 4 for its relationship to
the athlete-global `AthleteEquipment` inventory, which does **not** get introduced until
Phase 4; Phase 1's revision schema only needs to express *requirements*, not resolve
availability against them yet.

### 1.3 `ProgramInstance` lifecycle

Introduce `ProgramInstance` with its own `ProgramInstanceStatus` enum
(`PLANNED/ACTIVE/PAUSED/COMPLETED/ABANDONED/SUPERSEDED` — kept separate from
`PlanStatus`, per ADR 0001's finding that `Macrocycle`/`Mesocycle`'s existing `PlanStatus`
is `PLANNED/ACTIVE/COMPLETE/ABANDONED` and has different consumers). `PAUSED` semantics
are defined conservatively per the ADR: no new sessions, no progression advancement,
historical data stays readable, athlete-global state may still update, no silent
microcycle catch-up on resume. Replace `EngineState.active_program_id` with
`active_instance_id`.

### 1.4 Legacy `r0` reconstruction/backfill

Materialize a `ProgramRevision r0` from the current live `Program` state with
`revision_origin = LEGACY_RECONSTRUCTION` (not `PUBLISHED`) and wrap existing history in
a `ProgramInstance`. Any audit/reporting surface must render `LEGACY_RECONSTRUCTION`
visibly differently from a real publish — this backfill gives historical sessions
continuity, it must not imply they were generated against a real immutable revision at
the time.

### 1.5 Runtime revision execution + the definitive authority test

Re-point every runtime read path identified in the current-state audit's architecture map
(§1) — `lay_skeleton()` above all, but every row in that table is a candidate — from live
`Program`/`ProgramDay`/`Tier`/`TierExercise` to the revision-scoped tables via the active
`ProgramInstance`.

**Required pass/fail check before this phase is considered done** (ADR 0001, "The
definitive revision-authority test"):

```
publish APEX r1 → activate an instance on r1 → generate session A
edit live Program authoring tables drastically (do NOT publish)
generate session B from the SAME active instance
    → must match session A's behavior exactly, unaffected by the live edit
publish r2 from the edited state
    → the still-active instance must still use r1
    → only a newly activated instance may use r2
```

Any deviation means a read path was missed — treat it as a blocking defect, not an
acceptable edge case.

### 1.6 Collision/state-scope migration

Split `MovementState` into athlete-global `MovementState` (calibration, e1RM, equipment
calibration fields — see ADR's State Ownership Model for the exact field list) and new
`ProgramMovementState` (progression-ladder/execution state, scoped to `movement_id` +
`program_instance_id` + `program_revision_day_id`). Add `program_revision_day_id`
identity to `Session` and `MicrocycleSlot`. Re-target `MissedDayRecord` to the
instance/microcycle **occurrence**, not `ProgramRevisionDay` directly (per ADR — a missed
workout is an event in the athlete's execution timeline, not a property of the recurring
template day). Retain `day_role`/`day_label` as display-only denormalized strings, never
used for joins/lookups after this phase.

**This is a live-data migration**, not just a schema change (ADR Risk #3) — needs its own
verification pass against production data, per the standing "no production reseeding"
rule.

### 1.7 Migration release gate

Before touching the live database: snapshot/backup production → restore to an isolated
scratch database → run the migration there → backfill `r0` there → run integrity checks
→ run the full test suite → run the golden regression fixtures (1.1) → only then apply to
production. Additive/reversible as far as practical. No reseeding, at any point.

### 1.8 Regression verification

Re-run the golden baseline (1.1) end to end; full test suite green; the revision-authority
test (1.5) passing; a synthetic second `Program` row can exist in the database without any
query returning cross-program-contaminated `MovementState`/day data (test this directly,
don't just assume the FK change is sufficient).

**Overall Phase 1 acceptance criterion (ADR 0001, invariant 10):** the same athlete state
plus the same program revision produces the same observable prescription and
deterministic decisions as the legacy APEX implementation, across every dimension listed
in 1.1, except where an explicitly documented migration difference is unavoidable. No new
programs, no AI-behavior expansion, until this holds.

---

## Phase 2 — Prove generality with a second program

**Tag:** NEW CAPABILITY

ADR 0001's implementation-sequence step 6. Add one program that is **structurally
different** from APEX Bridge — different training frequency, volume distribution,
progression strategy, mesocycle topology, or exercise-role policy — through pure
authoring (new `Program`/`ProgramDay`/`Tier`/`TierExercise` rows + a `ProgramRevision`),
with **zero program-specific branches added to the generic generator.**

If generic generator code needs an `if program.name == "..."` branch to make the second
program work, stop and revisit the abstraction before adding a third program — this is
the brief's own stated stress test and it still applies.

**Acceptance criterion:** both programs generate correctly through the identical code
path; adding the second program was authoring/configuration work plus tests, not engine
surgery.

---

## Phase 3 — Program catalog + manual activation

**Tag:** NEW CAPABILITY (mostly additive API)

Additive endpoints only — per ADR's API-compatibility section, no existing endpoint
shape changes:

```
GET  /programs
GET  /programs/{id}
GET  /programs/{id}/revisions/{revision}
POST /programs/{id}/activate
GET  /training/program/current
GET  /training/program/history
```

Program listing, metadata, revision pinning, manual activation, program completion/
abandonment state, program history. **Do not expand AI authority in this phase** — the
objective is purely "IronLog can contain multiple programs and the athlete deliberately
chooses which to run."

---

## Phase 4 — Equipment & capability model

**Tag:** NEW CAPABILITY, with rich-metadata partially EXTEND EXISTING

This elevates equipment/capability reasoning to a first-class architectural layer, per
project direction, rather than leaving it as unstructured context handed to the LLM. Four
concrete additions:

### 6.1 Athlete Equipment Profile

New `AthleteEquipment` table: `equipment_id`, `enabled`, `quantity`, `location`,
`configuration`, `load_min`/`load_max`, `increments`, `notes`. This distinguishes
"exists in IronLog's master `Equipment` catalog" from "this athlete actually owns/has
access to it" — a distinction the current schema has no way to express (today `Equipment`
rows are implicitly assumed available to the one athlete this system serves). Matters
today for correctness (equipment gated by `available_phase` is a global on/off switch,
not a real inventory) and matters structurally the moment IronLog could serve more than
one athlete or gym.

### 6.2 Movement Capability Requirements

Replace `Movement.equipment_tags`' flat string list with an expression schema capable of
representing alternatives and combinations — `ALL_OF`/`ANY_OF`, nestable:

```yaml
# Meadows Row
requirements:
  ANY_OF:
    - landmine
    - ALL_OF: [barbell, landmine_attachment]
optional:
  - meadows_row_handle
```

A deterministic resolver evaluates this expression against the athlete's
`AthleteEquipment` inventory to produce feasibility — **the deterministic engine already
knows which exercises are possible before the AI is ever invoked**, rather than handing
the AI a raw equipment list and asking it to reason about feasibility itself. This is the
direct fix for the current-state audit's finding that `Movement.equipment_tags` cannot
express "requires landmine OR (barbell + attachment)."

### 6.3 Rich exercise metadata

**Partially EXTEND EXISTING** — `Movement` already carries `region`, `lift_category`,
`primary_muscle`/`secondary_muscles`, `progression_mode`, `family`/`is_family_anchor`,
`unilateral`. Extend with the dimensions that matter for substitution/candidate-scoring
reasoning: strength curve, stability demand, axial fatigue, joint stress, skill demand,
weak-point tags, program role (anchor/secondary/accessory/isolation), suitable goals,
substitution family, and related-movement IDs for similarity. This is additive schema
work on an existing rich model, not a new subsystem.

### 6.4 Program compatibility scoring

Depends on 6.1–6.2 and Phase 3's program catalog. For each program × athlete equipment
combination, compute per-slot compatibility distinguishing:

- **REQUIRED, no valid substitute** → program incompatible
- **OPTIONAL, missing** → program remains viable
- **SUBSTITUTABLE** → original unavailable, an equivalent movement (same
  `substitution_family`) is available

This is what makes a generic program adapt to a specific home gym without ceasing to be
recognizably the same program — the point emphasized in project direction. Surfaced to
the athlete as a compatibility percentage plus a specific missing/substituted list, not a
bare pass/fail.

**Architectural principle established by this phase, applying to every later phase that
touches candidate selection:**

```
Equipment        → "Can I perform it?"
Exercise library → "What does it train and how?"
Program          → "Is it appropriate in this slot?"
AI               → "Which of these equally legal choices is best right now?"
```

Each layer answers a different question; a layer never has to re-derive an answer that
belongs to the layer below it. The AI in particular never re-discovers equipment
feasibility from prompt text — it receives an already-filtered, already-legal candidate
set.

**Note on revision-binding:** per ADR 0001, equipment/capability state is athlete-global
runtime state, resolved live at generation time — never frozen into a `ProgramRevision`
snapshot. A revision defines slot *requirements*; this phase's resolver computes
*availability* against those requirements fresh, every time.

---

## Phase 5 — Generalize existing adaptive-intent architecture

**Tag:** GENERALIZE EXISTING — not "build typed AI intents"

The proposer pipeline (`Selections`/`SlotSelection` → `assemble()` → `validator.py`)
already is the typed-bounded-intent architecture. This phase widens its vocabulary and
adds the policy layer that governs it, without replacing the pipeline shape:

1. New intent types, additive to the existing schema: `EMPHASIZE_WEAK_POINT`,
   `REDUCE_OPTIONAL_VOLUME`, `CHANGE_ACCESSORY_ORDER`, `RECOMMEND_DELOAD`,
   `RECOMMEND_MESOCYCLE_EXTENSION`, `RECOMMEND_PROGRAM_TRANSITION`. Each resolved
   deterministically downstream exactly like `SlotSelection` is today — same
   propose → validate → clamp → commit shape, no second framework introduced (ADR
   invariant 7).
2. `ProgramAdaptationPolicy`, revision-bound per ADR 0001 — declares which intent types
   are legal per program/revision, their eligibility conditions, and maximum
   intervention magnitude. `PROPOSER_SYSTEM_INSTRUCTION` becomes assembled from this
   policy per program/revision, rather than one fixed global string.
3. Candidate pools feeding the widened proposer are the output of Phase 4's
   equipment/capability resolver — the AI selects only among already-feasible,
   already-legal candidates, never raw library-wide options.

**Acceptance criterion:** every new intent type has a fixture proving it is resolved
deterministically and cannot bypass the validator; the existing quiet-week bypass
(zero LLM calls when nothing needs judgment) is preserved unchanged.

---

## Phase 6 — Deterministic candidate scoring

**Tag:** NEW CAPABILITY, consistent with existing pipeline shape

A pure deterministic ranking/filtering layer between Phase 4's feasibility resolver and
the (now-widened) proposer — inputs: weak-point relevance, program priority,
movement-pattern match, recovery/readiness, historical response, exposure recency,
equipment convenience; outputs a ranked, inspectable Top-K candidate menu per slot. Score
components must be inspectable (why did this rank highest), not an opaque model — this is
a deterministic scoring function, not a second LLM call.

---

## Phase 7 — Mesocycle/program-boundary intelligence

**Tag:** EXTEND EXISTING — `Macrocycle`/`Mesocycle` lifecycle infrastructure already
supports this shape

Mesocycle extension/deload recommendation, mesocycle-exit assessment, program completion
assessment, candidate successor programs, program-transition recommendation. **All actual
program changes require explicit user approval initially** — this phase produces
recommendations only, using the `RECOMMEND_*` intent types from Phase 5.

---

## Phase 8 — Macrocycle sequencing

**Tag:** EXTEND EXISTING

Once individual program transitions are trustworthy (Phase 7 proven in practice), use the
existing `Macrocycle` entity for longer-range sequencing across multiple program
instances toward a stated goal (e.g. APEX Bridge → Pivot → Strength Accumulation →
Strength Intensification). Macrocycle provides intent/sequencing context without owning
or overriding any individual program's identity.

---

## Phase 9 (DEFERRED) — Program authoring, import/export, and AI-assisted program creation

Only after the schema from Phases 1–7 has stabilized in production across at least two
real programs. Includes:

- Clone/edit/validate/preview a personal program; import/export; archive/compare
  revisions.
- **AI-assisted program creation, reusing the same capability engine as session
  generation** (project direction, explicitly called out as important): goal +
  constraints (days/week, duration, equipment, weak points, restrictions) →
  deterministic capability/filter engine (Phase 4/6's machinery, not a separate
  pipeline) → eligible movement pools by role → AI composes program topology from
  allowed pools only → program validator → human review → becomes a real
  `ProgramRevision` through the normal immutable-revision path. This is explicitly
  **not** a second AI-authoring system alongside session-level adaptation — it is the
  same feasibility/legality machinery, applied one level up, with AI choosing among
  program-topology alternatives instead of session-slot alternatives.
- Community/shared programs — explicitly not started until the schema is stable
  (matches the brief's own caution here).

**Also deferred, tracked but not scheduled:**

- Declarative YAML-authoring pipeline generalization beyond the one-shot seed script
  (`docs/build-plan.md` Open Item #4 — prerequisite work, tracked separately).
- Program simulator/playground (run a revision forward against synthetic/historical
  feedback) — valuable, but depends on the revision-materialization pipeline (Phase 1)
  being stable first.
- Historical program-response intelligence ("you respond better to moderate-volume bench
  frequency") — needs sufficient multi-program observation history to exist at all;
  track confidence/sample size once attempted, don't introduce conclusions prematurely.

---

## Testing strategy (applies across all phases)

- **Golden APEX tests** (Phase 1, §1.1) — captured before any change, required to pass
  after every subsequent phase unless a behavior change is deliberately specified.
- **Program-schema tests** — reject invalid movement references, impossible equipment
  requirements, invalid tier topology, contradictory adaptation policy, bad
  revision/hash relationships.
- **Property/invariant tests** directly encoding ADR 0001's acceptance invariants — e.g.
  "AI can never exceed program authority," "an active instance cannot silently change
  revision," "a completed session remains linked to its original revision."
- **Historical replay** — replay real historical sessions through new analysis/adaptation
  logic without applying the output, to compare against what actually happened.
- **AI benchmark fixtures** — fixed scenarios (high readiness, poor readiness, persistent
  weak point, missed session, equipment conflict, conflicting signals) each with defined
  permitted/forbidden intents and required deterministic outcome.
- **Shadow/dry-run mode** for any new adaptation logic before it controls production
  workouts — record the new logic's recommendation alongside the current engine's actual
  output, without applying it, before cutting over.

---

## Explicit anti-goals (carried forward from the original brief, still binding)

Do not: let the LLM calculate load or bypass progression rules; let the LLM invent
exercises outside the resolved-feasible candidate menu; let the LLM change weekly
training frequency unilaterally; silently mutate an active program definition or
transition to another program; conflate program definition with athlete state; replace
the planned/performed separation; duplicate `Macrocycle`/`Mesocycle`/`Microcycle`
concepts; broaden scope into nutrition/social/gym-management features; add many programs
before Phase 2 proves the abstraction; sacrifice deterministic test discipline for faster
AI iteration; reseed the production database as a migration shortcut.
