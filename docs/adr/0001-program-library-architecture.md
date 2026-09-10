# ADR 0001: Program-Library Architecture (ProgramDefinition / ProgramRevision / ProgramInstance)

**Status:** Proposed
**Date:** 2026-09-10
**Supporting documents:**
[Current-state audit](../design/program-library-current-state-audit.md) ·
[External research](../design/program-library-external-research.md) ·
[Implementation roadmap](../roadmaps/program-library-roadmap.md)

---

## Context

IronLog V2 today runs exactly one program ("APEX Bridge") and was never designed to run
more than one. The goal is to evolve it into a platform that can run a library of
deliberately-designed programs on the same deterministic engine, while athlete history
(e1RM, calibration, weak-point signals, recovery, body composition) survives moving
between programs, and AI involvement stays exactly as bounded as it is today — the model
proposes; deterministic code disposes.

This decision does **not** cover the phased rollout, UX, program-authoring tooling, or
the equipment/capability model in detail — those live in the roadmap. This ADR settles
the structural question underneath all of them: **what does a "program" actually consist
of, and how does an athlete's execution of one relate to edits made to it later?**

### Current architectural problem

The [current-state audit](../design/program-library-current-state-audit.md) found that
`Program` (`ironlog/models/program.py`) conflates three distinct concepts with no field
to separate them:

1. **Authoring identity** — "APEX Bridge" as a named, evolving thing.
2. **A specific version of its topology** — what it actually prescribed on a given date.
3. **One athlete's live execution of it** — start date, current mesocycle, status.

`Program` has no revision counter and no lifecycle enum (`docs/build-plan.md` Open Item
#3 already names this as unscoped debt). It is edited in place via migrations (e.g.
migration 072's rear-delt split), and the only integrity check is a live hash
(`program_hash.py`) recomputed and compared at commit time — a **drift detector**, not a
**versioned history**. A mismatch blocks generation; it does not tell you what the
program looked like when a past session was actually generated, because nothing froze
that state.

Two further structural details silently assume a single program: `day_role` is used as a
global, unnamespaced string identifier across `Session`, `MicrocycleSlot`, and (until
corrected) would-be cross-program lookups; and `MovementState` is keyed
`(movement_id, day_id)`, not by program. Both are real collision hazards the moment a
second program exists (see [State Ownership](#state-ownership-model) and
[Identity Strategy](#identityday-collision-strategy) below).

---

## Decision

Introduce three explicitly distinct concepts, replacing the current conflated `Program`:

```
Program            — durable authoring identity ("APEX Bridge")
ProgramRevision    — one immutable, executable version of that program ("APEX Bridge r7")
ProgramInstance    — one athlete's lifecycle running exactly one revision
                      ("athlete running APEX Bridge r7, Aug 17 – Sep 14")
```

`Program`'s existing authoring tables (`ProgramDay`, `Tier`, `TierExercise`,
`MesoRotation`, `MicrocycleParityRotation`, `SlotMovementOverride`) remain the live,
mutable surface an author edits — this is unchanged from today. What changes is that
**generation for an active instance never reads those live tables directly.** Editing
`Program`'s authoring tables after an instance has started must not alter what that
instance generates, unless a program author or the athlete explicitly transitions the
instance to a new revision. This is the acceptance bar the rest of this document is
built to satisfy — see [Acceptance Invariants](#acceptance-invariants).

---

## Alternatives considered

### Revision execution model

The critical design question, called out explicitly during review: an immutable
`ProgramRevision` that runtime generation does not actually read from is immutability in
name only. Two options were evaluated.

**Option A — Snapshot execution.** `ProgramRevision` stores one immutable serialized
blob (JSON) of the fully resolved topology. Runtime "compiles" or loads this blob and
generates from it in memory.

**Option B — Immutable normalized revision tables.** `ProgramRevision` is paired with
revision-scoped mirrors of each authoring table — `ProgramRevisionDay`,
`ProgramRevisionTier`, `ProgramRevisionExercise`, `ProgramRevisionMesoRotation`,
`ProgramRevisionParityRotation`, `ProgramRevisionSlotMovementOverride` — populated by a
deterministic "materialize revision" step at `ProgramRevision`-creation time. Runtime
queries the revision-scoped tables directly, using the same join/precedence-resolution
pattern `lay_skeleton()` already uses today, just parameterized by revision instead of by
the live pointer.

**Decision: Option B.**

`lay_skeleton()`'s override-precedence resolution (`SlotMovementOverride >
MicrocycleParityRotation > MesoRotation > TierExercise.movement_id`) is a real relational
query with joins and precedence logic, not a flat lookup. Under Option A, that logic
would have to be reimplemented twice — once as SQL/ORM queries against the live authoring
tables (still needed for previews, authoring UX, and the materialize step itself), and
again as an in-memory JSON-walking interpreter for execution. That is exactly the
"parallel version of a concept that already exists" this codebase's own conventions warn
against, and it forks correctness: a bug fixed in one implementation can silently persist
in the other.

Under Option B, the same query shape and the same SQLModel ORM patterns keep working —
generation code changes to accept a revision-scoped table set instead of the live one,
which is a surgical, testable change to call sites, not a rewrite of the resolution
logic. The cost is real (materializing full copies at revision-creation time, and keeping
the revision-table schemas in lockstep with authoring-table schema changes — flagged in
[Risks](#risks)), but it is mechanical, testable cost, not a second engine.

`program_hash.py`'s existing hash functions remain exactly what they are today —
**integrity verification**, not a substitute for immutable execution. They are
recomputed over the materialized revision tables at creation time (as the
`ProgramRevision`'s stored `prescription_hash`/`topology_hash`) and can be recomputed
later to prove a revision's stored rows haven't been tampered with. The revision's
*authority* comes from runtime reading the revision-scoped tables, never the live ones,
once an instance is active — the hash is a check on that guarantee, not the mechanism
that provides it.

### Program/revision/instance split, vs. two simpler alternatives

Two lighter alternatives were considered and rejected:

- **Minimal — add fields to `Program` only** (a status enum + revision counter, no new
  tables). Rejected: a `Program` edit would retroactively change what a past session
  appears to have run under, directly violating the "historical sessions must remain
  explainable against the revision that generated them" invariant.
- **Full immutable split — migrate authoring itself onto immutable tables**, removing
  live-mutable `Program`/`ProgramDay`/`Tier`/`TierExercise` entirely. Rejected as
  disproportionate: it would force every authoring edit (including today's ordinary
  migration-driven fixes, like the rear-delt split) through a revision-publish
  ceremony, and touches every FK that currently points at the authoring tables. The
  chosen design gets the same immutability guarantee for *execution* without changing
  how programs are authored today.

---

## Program / ProgramRevision / ProgramInstance semantics

| Concept | Represents | Mutability | Identity example |
|---|---|---|---|
| `Program` | Durable authoring identity | Authoring tables remain live-editable (as today) | "APEX Bridge" |
| `ProgramRevision` | One frozen, executable version | Immutable once created; a new authoring edit produces a new revision, not a mutation of an old one | "APEX Bridge r7" |
| `ProgramInstance` | One athlete's run of exactly one revision | Lifecycle fields (`status`, dates) mutate; `revision_id` never changes once set | "athlete on APEX Bridge r7, Aug 17 – Sep 14" |

A `ProgramRevision` is created explicitly, not on every authoring save — see
[Deferred Decisions](#deferred-decisions) for the exact trigger, left open. `Mesocycle`
and `Microcycle` continue to exist beneath a `ProgramInstance` exactly as they do beneath
`Program` today; only the top-level binding changes, from "points at the live `Program`"
to "points at the `ProgramInstance`, which points at a pinned `ProgramRevision`."

---

## Revision execution model

(Selected above: Option B, normalized revision tables.)

At `ProgramRevision` creation:

1. Copy the current state of `Program`'s authoring tables (`ProgramDay`, `Tier`,
   `TierExercise`, `MesoRotation`, `MicrocycleParityRotation`,
   `SlotMovementOverride`) into revision-scoped mirror tables, keyed by the new
   `revision_id`.
2. Compute `prescription_hash`/`topology_hash` over the materialized revision rows
   (reusing `program_hash.py`'s existing projection logic) and store them on
   `ProgramRevision`.
3. The revision's adaptation policy (see
   [AI/Adaptation-Policy Relationship](#aiadaptation-policy-relationship)) is
   materialized alongside it in the same step — anything capable of changing generated
   behavior is captured here, not left pointing at the live `Program`.

At generation time, `lay_skeleton()` and every other read path resolve
`ProgramInstance → revision_id → revision-scoped tables`, never the live authoring
tables. `EngineState.active_program_id` becomes `EngineState.active_instance_id`.
`Mesocycle.program_prescription_hash`/`Microcycle.slot_topology_hash` continue to be
compared, but now against the pinned revision's stored hash rather than a live re-hash of
a mutable object — same drift-detection mechanism, clearer subject (has the *revision's
own stored rows* been tampered with, versus "has the live Program changed since I last
looked," which is no longer the question that matters once an instance is pinned).

---

## State ownership model

`MovementState`'s fields were classified individually against the test: *if an athlete
leaves Program A and enters Program B, should this value follow them?* Blindly adding
`program_id` to the existing table would duplicate genuinely athlete-global capability
data per program, which is wrong in the other direction from today's bug.

**Stays in `MovementState` (athlete-global, unkeyed by program):** `calibration_status`,
`e1rm`, `e1rm_updated_at`, `assist_level`, `ht_plates`, `ht_band_pair_id`, `ht_felt_peak`,
`unassisted_max_rolling`. These are physical-capability facts — long-term strength,
calibration, equipment configuration — that a program change should never reset.

**Moves to a new `ProgramMovementState` (scoped to `movement_id` + `program_instance_id`
+ a day identity, not a raw string — see next section):** `current_load`,
`current_increment_tier`, `pending_load_delta`, `current_rep_scheme`,
`rep_scheme_locked_until`, `consecutive_ceiling_sessions`,
`consecutive_failed_progressions`, `confirmed_at`, `pending_ht_plates`,
`pending_ht_band_config`, `consecutive_advance_count`, `active_rule`,
`current_body_position`, `current_rep_target`, `duration_ladder`,
`current_duration_seconds`, `current_rope`, `stall_signal`. These depend on the
*current program's* rep scheme, increment ladder, and objective configuration — they are
expected to reset (or be deliberately re-derived from the global capability fields) when
an athlete starts a new program or revision.

This directly resolves invariant 2.2 (definition/state separation) at the movement-state
level: `MovementState` becomes purely "what this athlete can do," and
`ProgramMovementState` becomes "where they are in this program's specific execution of
it" — the same shape as the `Program`/`ProgramInstance` split, one level down.

---

## Identity / collision strategy

`Session.day_role` and `MicrocycleSlot.day_label`/`day_code` are plain strings today
(confirmed: no FK). This is the actual multi-program collision hazard the audit found —
not that a label needs a namespace prefix, but that a label is being used as identity at
all. The fix is to add a stable identity column,
**`program_revision_day_id`** (FK into the revision-scoped day table), to every place
that currently joins or looks up by `day_role`/`day_label`, and keep the string fields as
denormalized **display-only** copies populated from that identity at generation time —
never used for lookups or joins going forward.

`MissedDayRecord` already uses a proper `program_day_id` FK (not a string) — it is
migrated to point at `program_revision_day_id` for consistency once revision tables
exist, but it was never part of the string-identity problem.

`ProgramMovementState` (above) uses this same `program_revision_day_id` identity for its
day-scoping, rather than the loose string `day_id` `MovementState` uses today — the same
fix, applied once, in the one place a new table is being introduced anyway.

Not every string in the codebase needs to become a foreign key — this fix is scoped to
the specific fields identified as acting as cross-table identity today, not a general
string-to-FK sweep.

---

## AI / adaptation-policy relationship

The audit's most important finding for this section: **the bounded-intent pattern
already exists and works.** `proposer.py`'s `Selections`/`SlotSelection` schema — four
non-numeric fields per slot, explicitly forbidden from touching load/reps/weight — is
already exactly the "model proposes, deterministic code disposes" shape this ADR would
otherwise have to invent. Future work here is **generalizing an existing, proven
mechanism**, not building a second one. See the roadmap for the specific new intent
types; this ADR fixes only where the *policy* controlling them lives.

**`ProgramAdaptationPolicy` is bound to `ProgramRevision`, not `Program`.** A
`ProgramInstance` is pinned to an immutable revision specifically so that "what happened
and why" stays reconstructable after the athlete finishes; if AI authority could be
changed on the live `Program` mid-instance, that guarantee breaks silently — an instance
could start under one authority level and finish under another with no record of when or
why. The revision's materialization step (above) therefore includes whichever of these
apply as part of what gets hashed and frozen: slot topology, progression configuration,
objective/priority configuration, adaptation authority, candidate-selection restrictions,
mesocycle topology, and transition/exit rules. Anything capable of changing generated
behavior is in scope for revision-binding — the test is not "is this AI-related," it's
"could this change what a session looks like."

**Equipment/capability state is explicitly excluded from revision-binding.** The
roadmap's equipment-capability model (owned equipment → derived capabilities → eligible
exercise library → candidate filtering) is athlete-global runtime state, not
program-authored configuration — an athlete can buy a new piece of equipment mid-program,
and that should be reflected in the very next session's candidate pool, not frozen at
whatever the equipment inventory looked like when the revision was created. It is
resolved live at generation time, the same way `RecoveryStatus`/`BodyCompState` already
are, feeding into candidate scoring rather than into the revision snapshot. A program's
revision defines slot **requirements** (what a slot needs to be satisfied); resolved
**availability** against those requirements is always computed fresh.

---

## Historical / audit guarantees

Because `Mesocycle`/`Microcycle`/`Session` chain down to a `ProgramInstance` and its
pinned `revision_id`, a historical session remains explainable against the exact revision
that generated it indefinitely, even after `Program`'s authoring tables have since been
edited many times over. `GenerationLog`/`AdvancementLog` need no structural change — they
already record provenance per session/event; they simply now trace back through a stable,
immutable revision rather than a live, potentially-since-mutated `Program`.

---

## API compatibility implications

Per invariant 2.5 (preserve the API contract), this is additive. `EngineState`'s pointer
rename (`active_program_id` → `active_instance_id`) is internal. New endpoints
(`GET /programs`, `GET /programs/{id}/revisions/{revision}`, program history) are new
surface, not replacements. Existing endpoints (`GET /programs/{id}/slots`,
`GET /training/plan/current`, etc.) keep their response shapes; where they currently
resolve data from the live `Program`, they resolve from the active instance's pinned
revision instead — a behavior change only in the sense that responses become
revision-stable rather than live-mutable, not a schema change. No Android client change
is required for this ADR's scope; client-visible additions belong to the roadmap's
program-catalog phase.

---

## Migration implications

- Existing `Program`/`Mesocycle`/`Microcycle`/`Session` history predates
  `ProgramRevision`/`ProgramInstance`. A one-time backfill materializes a `ProgramRevision
  r0` from the current live `Program` state and a `ProgramInstance` wrapping the existing
  `started_at` history, so historical data isn't orphaned by the new model.
- `MovementState` → `MovementState` + `ProgramMovementState` is a real data-splitting
  migration against live production data with real athlete history — must go through
  IronLog's standard migration-based deployment process; **no production reseeding**
  (per `CLAUDE.md`'s standing warning that reseeding has previously destroyed live
  history).
- APEX Bridge's generated behavior must be equivalent before and after this migration —
  golden/regression tests captured **before** any schema change, re-verified after (see
  [Implementation Sequence](#implementation-sequence) step 1 and 5).

---

## Consequences

- `Program` stops being "the thing a session was generated under" — `ProgramInstance` /
  its pinned `ProgramRevision` is. This is the intended effect, not a side effect: it's
  what makes historical explainability possible.
- Every runtime read path that currently touches live `Program`/`ProgramDay`/`Tier`/
  `TierExercise` must be identified and re-pointed at revision-scoped tables. The
  current-state audit's architecture map (§1) is the checklist for this — every row in
  that table is a candidate call site to verify, not just the obvious ones
  (`lay_skeleton`).
- Authoring workflow is unchanged day-to-day (migrations still edit `Program`'s tables
  directly) — the new ceremony is only at the moment a `ProgramRevision` is published and
  an instance starts or transitions.
- Two schema families (authoring tables and revision-scoped mirrors) must be kept in
  lockstep going forward — a new column on `TierExercise` needs a matching column on
  `ProgramRevisionExercise`, or materialization silently drops data.

---

## Risks

1. **Schema drift between authoring and revision-table families.** Every future schema
   change to an authoring table must be mirrored in its revision-table counterpart, or
   materialization silently loses fields. Needs an explicit convention (shared base
   columns, a codegen step, or a test that fails when the two schemas diverge) before
   this scales past the first couple of programs.
2. **Missed call sites re-pointing to revision tables.** This is the specific "theater"
   failure mode this ADR is designed to avoid — any read path left pointing at live
   `Program` tables silently defeats immutability for that one path. The audit's
   architecture map is the checklist; each row needs to be explicitly verified re-pointed,
   not assumed.
3. **`MovementState` split is a live-data migration**, not just a schema change — splitting
   existing rows into global vs. program-scoped state against production data with real
   athlete history. Higher blast radius than an additive migration; needs its own
   verification pass distinct from the schema migration itself.
4. **`ProgramInstance` status semantics are new** — no existing code models "pausing" a
   program. What happens to readiness/recovery tracking, missed-day detection, and
   generation availability while `PAUSED` needs explicit design before Phase 2 of the
   roadmap, not assumed by analogy to `Mesocycle`'s simpler `PlanStatus`.
5. **Revision materialization is a new deterministic pipeline** (copy + hash) with no
   existing test coverage — needs its own test suite proving materialization is
   idempotent and lossless before APEX migrates onto it.

---

## Deferred decisions

These are explicitly left open — not resolved by this ADR, and not silently defaulted:

- **Revision creation trigger.** Whether every authoring-table edit automatically
  produces a new `ProgramRevision`, or an explicit "publish revision" action is required.
  Needs a decision before Phase 1 implementation begins.
- **Exact scope of the `day_role`/`day_label` → `program_revision_day_id` migration.**
  Which specific consumers get the new FK column added is deferred to an implementation-
  time audit of all read/write sites, not enumerated exhaustively here.
- **`MissedDayRecord`'s exact target after the FK migration** — whether missed-day
  detection should key off `ProgramRevisionDay` or a separate athlete-level schedule
  concept independent of any specific revision. Deferred to when multi-program missed-day
  semantics are actually exercised (roadmap Phase "prove a second program").
- **`entry_state_snapshot`/`exit_state_snapshot` JSON shape on `ProgramInstance`** —
  deferred until program-transition logic (roadmap, later phase) is designed.
- **Declarative YAML-authoring pipeline generalization** (`docs/build-plan.md` Open Item
  #4) — a prerequisite for scaling program authoring, tracked separately, not decided
  here.

---

## Acceptance invariants

1. Once a `ProgramInstance` begins, subsequent edits to `Program`'s authoring tables
   cannot alter any prescription generated for that instance, unless an explicit
   migration/transition mechanism says so.
2. A `ProgramRevision`'s stored hash is a verification checksum, never the sole source of
   truth for generation — generation always reads from revision-scoped tables, never live
   authoring tables, once an instance is active.
3. Athlete-global movement capability (`e1rm`, calibration, equipment calibration) is
   never duplicated per program; program-scoped progression/execution state is never
   conflated with it.
4. Any identifier used for joins/lookups across program-day/session/slot is a stable
   identity (FK); free-text labels (`day_role`, `day_label`) are display-only.
5. `ProgramInstance` status semantics are independent of `PlanStatus` — no enum sharing
   unless genuinely equivalent semantics are proven, not assumed from label overlap.
6. A `ProgramRevision`'s snapshot includes every behaviorally-relevant configuration
   surface — topology, progression, objective/priority config, adaptation authority,
   candidate-selection constraints, mesocycle topology, transition/exit rules. Nothing
   capable of changing generated behavior lives outside the revision boundary once an
   instance is pinned to it.
7. New adaptation-intent types extend the existing `Selections`/`SlotSelection` schema
   and propose → validate → clamp → commit pipeline; no second AI-action framework is
   introduced.
8. Equipment/capability availability is resolved live at generation time from
   athlete-global state, never frozen into a `ProgramRevision` snapshot.
9. `Program` / `ProgramRevision` / `ProgramInstance` identity stays distinct throughout:
   authoring identity, immutable executable version, and one athlete's lifecycle running
   exactly one revision are never conflated back into one object.
10. APEX Bridge's generated behavior is equivalent before and after migration onto this
    execution model (golden regression tests), unless a behavior change is deliberately
    specified and called out.
11. The production database is never reseeded as a shortcut for any part of this
    migration.

---

## Implementation sequence

Full phasing lives in [the roadmap](../roadmaps/program-library-roadmap.md). At the
level this ADR is responsible for:

1. Capture an APEX Bridge regression/golden baseline (topology, sessions, loads,
   progression, rotations, weak-point/readiness/deload behavior) before any schema
   change.
2. Introduce `ProgramRevision` + the normalized revision-table family, and the
   materialize-revision pipeline (reusing `program_hash.py`'s projection as the
   integrity-hash source).
3. Introduce `ProgramInstance` with its own lifecycle status, replacing
   `EngineState.active_program_id` with `active_instance_id`; re-point
   `Mesocycle`/`Microcycle` hash comparisons at the pinned revision.
4. Split `MovementState` into global `MovementState` + program-scoped
   `ProgramMovementState`; replace `day_role`/`day_label` as join keys with
   `program_revision_day_id` identity (display strings retained).
5. Migrate APEX Bridge itself onto the new execution path end-to-end, verified against
   the step 1 golden baseline.
6. Prove a second, structurally different program end-to-end through the same pipeline,
   with zero program-specific branches added to the generic generator.
