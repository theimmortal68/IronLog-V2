# ADR 0001: Program-Library Architecture (ProgramDefinition / ProgramRevision / ProgramInstance)

**Status:** Approved (2026-09-10) — implementation proceeds per this ADR's
[Implementation Sequence](#implementation-sequence)
**Date:** 2026-09-10 (revised same day — see [Revision History](#revision-history))
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

This decision does **not** cover UX or program-authoring tooling in detail — those live
in the roadmap. This ADR settles the structural question underneath all of them: **what
does a "program" actually consist of, how does an athlete's execution of one relate to
edits made to it later, and what does the engine actually read from at runtime?**

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

## Reinforced architecture invariants

Two invariants govern this entire design, of equal standing:

1. **Rules dispose; the model proposes** (pre-existing, already implemented by
   `proposer.py`'s `Selections` schema — see
   [AI/Adaptation-Policy Relationship](#aiadaptation-policy-relationship)).
2. **Equipment feasibility is deterministic; AI never decides what physically exists.**
   Ownership chain:

   ```
   Equipment inventory   — "Do I have the hardware?"      (athlete-global, mutable)
   Capability resolver   — "What can this hardware do?"   (deterministic)
   Movement requirements — "What does this exercise need?" (revision-bound, immutable)
   Program policy        — "Is this exercise legal here?"  (revision-bound, immutable)
   AI                    — "Which legal choice is best now?"
   ```

   Each layer answers a question the layer above it never has to re-derive. AI is
   invoked only at the last step, over an already-feasible, already-legal candidate set —
   never given a raw equipment/movement list to reason about feasibility itself. See
   [Equipment/Program-Requirement Separation](#equipmentprogram-requirement-separation).

---

## Decision

Introduce three explicitly distinct concepts, replacing the current conflated `Program`:

```
Program            — durable authoring identity ("APEX Bridge")
ProgramRevision    — one immutable, executable version, created by explicit publication
                      ("APEX Bridge r7")
ProgramInstance    — one athlete's lifecycle running exactly one revision
                      ("athlete running APEX Bridge r7, Aug 17 – Sep 14")
```

`Program`'s existing authoring tables (`ProgramDay`, `Tier`, `TierExercise`,
`MesoRotation`, `MicrocycleParityRotation`, `SlotMovementOverride`) remain the live,
mutable surface an author edits — this is unchanged from today. What changes is that
**generation for an active instance never reads those live tables directly, and a
revision comes into existence only through an explicit publish action, not automatically
on every edit.** Once `ProgramInstance` is activated against a revision, `Program`'s
authoring tables are irrelevant to that instance's workout generation until a
deliberately published, deliberately activated new revision replaces it. This is the
acceptance bar the rest of this document is built to satisfy — see
[Acceptance Invariants](#acceptance-invariants), in particular the definitive
[revision-authority test](#the-definitive-revision-authority-test).

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
deterministic "materialize revision" step at publish time. Runtime queries the
revision-scoped tables directly, using the same join/precedence-resolution pattern
`lay_skeleton()` already uses today, just parameterized by revision instead of by the
live pointer.

**Decision: Option B.**

`lay_skeleton()`'s override-precedence resolution (`SlotMovementOverride >
MicrocycleParityRotation > MesoRotation > TierExercise.movement_id`) is a real relational
query with joins and precedence logic, not a flat lookup. Under Option A, that logic
would have to be reimplemented twice — once as SQL/ORM queries against the live authoring
tables (still needed for previews, authoring UX, and the materialize step itself), and
again as an in-memory JSON-walking interpreter for execution. That is exactly the
"parallel version of a concept that already exists" this codebase's own conventions warn
against, and it forks correctness: a bug fixed in one implementation can silently persist
in the other. It also generalizes better to structured runtime consumers beyond the
generator itself — the validator, progression engine, and reporting/audit code all need
structured, queryable access to program topology, not a blob they'd each have to parse.

Under Option B, the same query shape and the same SQLModel ORM patterns keep working —
generation code changes to accept a revision-scoped table set instead of the live one,
which is a surgical, testable change to call sites, not a rewrite of the resolution
logic. The cost is real (materializing full copies at publish time, and keeping the
revision-table schemas in lockstep with authoring-table schema changes — flagged in
[Risks](#risks)), but it is mechanical, testable cost, not a second engine.

`program_hash.py`'s existing hash functions remain exactly what they are today —
**integrity verification**, not a substitute for immutable execution. They are
recomputed over the materialized revision tables at publish time (as the
`ProgramRevision`'s stored `prescription_hash`/`topology_hash`) and can be recomputed
later to prove a revision's stored rows haven't been tampered with. The revision's
*authority* comes from runtime reading the revision-scoped tables, never the live ones,
once an instance is active — the hash is a check on that guarantee, not the mechanism
that provides it.

### Program/revision/instance split, vs. two simpler alternatives

- **Minimal — add fields to `Program` only.** Rejected: a `Program` edit would
  retroactively change what a past session appears to have run under.
- **Full immutable split**, removing live-mutable authoring tables entirely. Rejected as
  disproportionate: it would force every ordinary authoring edit through a
  revision-publish ceremony. The chosen design gets the same immutability guarantee for
  *execution* without changing how programs are authored day-to-day.

### Revision-creation trigger: automatic vs. explicit publish

**Considered:** create a new `ProgramRevision` automatically on every authoring-table
edit. **Rejected.** A revision is meant to represent something executable and
historically meaningful — an object worth pinning an athlete's entire training block to —
not every intermediate database edit made while iterating on a change. Automatic
per-edit revisioning would flood the revision history with unpublished, possibly
inconsistent intermediate states and make "which revision was this athlete actually on"
a much noisier question than it needs to be.

**Decision: explicit publication**, detailed in
[Revision Execution Model](#revision-execution-model).

---

## Program / ProgramRevision / ProgramInstance semantics

| Concept | Represents | Mutability | Identity example |
|---|---|---|---|
| `Program` | Durable authoring identity | Authoring tables remain live-editable (as today); conceptually in a `DRAFT` state between publishes | "APEX Bridge" |
| `ProgramRevision` | One frozen, executable version | Immutable once published; a new authoring edit + publish produces a new revision, never a mutation of an old one | "APEX Bridge r7" |
| `ProgramInstance` | One athlete's run of exactly one revision | Lifecycle `status` and boundary snapshots mutate; `revision_id` never changes once set | "athlete on APEX Bridge r7, Aug 17 – Sep 14" |

`Mesocycle` and `Microcycle` continue to exist beneath a `ProgramInstance` exactly as
they do beneath `Program` today; only the top-level binding changes, from "points at the
live `Program`" to "points at the `ProgramInstance`, which points at a pinned
`ProgramRevision`."

---

## Revision execution model

(Selected above: Option B, normalized revision tables, created by explicit publish.)

### Publication lifecycle

```
Program authoring state (DRAFT)
        ↓ edits (as today — migrations, seed adjustments)
        ↓ validate
READY
        ↓ explicit publish action
ProgramRevision N  (immutable)
```

A **publish** operation:

1. Validates complete topology — every `ProgramDay`/`Tier`/`TierExercise` reference
   resolves, no orphaned slots, no contradictory overrides.
2. Validates every referenced `Movement`, `Equipment` requirement, and adaptation-policy
   reference exists and is internally consistent (see
   [Equipment/Program-Requirement Separation](#equipmentprogram-requirement-separation)
   and [AI/Adaptation-Policy Relationship](#aiadaptation-policy-relationship) — both are
   part of what a publish validates and freezes, not follow-on work).
3. Materializes the immutable revision-table family from the current authoring state.
4. Computes `prescription_hash`/`topology_hash` over the materialized rows (reusing
   `program_hash.py`'s existing projection logic).
5. Commits the new `ProgramRevision` atomically — steps 3–5 succeed or fail together;
   there is no partially-materialized revision state visible to any reader.

`Program`'s authoring tables continue changing after a publish without affecting the
already-published revision — authoring moves back to (or continues in) `DRAFT` for the
next round of edits.

### Runtime execution

At generation time, `lay_skeleton()` and every other read path resolve
`ProgramInstance → revision_id → revision-scoped tables`, never the live authoring
tables. `EngineState.active_program_id` becomes `EngineState.active_instance_id`.
`Mesocycle.program_prescription_hash`/`Microcycle.slot_topology_hash` continue to be
compared, but now against the pinned revision's stored hash rather than a live re-hash of
a mutable object — same drift-detection mechanism, clearer subject.

### Slot requirement typing (schema note, not full implementation)

The roadmap's later candidate-scoring/equipment work (Phases 6–8) needs
`ProgramRevisionExercise`/slot rows to express more than a single hardcoded movement ID,
so this phase's schema is designed to support it from the start rather than needing a
breaking change later:

```
ANCHOR         — exact movement required, no substitution
SEMI_ANCHOR    — movement, or an approved substitution family
ADAPTIVE_SLOT  — required movement pattern, muscle/weak-point constraints,
                 allowed program role, prohibited characteristics
```

Phase 1 implements the schema capability (a slot-role/constraint column set on
`ProgramRevisionExercise`) but does not implement the candidate resolver or scorer that
consumes it — that is roadmap Phase 6–8 work. The point is that Phase 1's revision
schema must not make that later work require re-migrating already-published revisions.

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

## ProgramInstance lifecycle semantics

`ProgramInstanceStatus` is a new enum, kept separate from `PlanStatus` (see
[Risks](#risks) item 4's resolution below). Defined conservatively:

```
ACTIVE
    generation allowed; sessions may be started; normal program clock behavior

PAUSED
    no new program sessions generated
    no program progression advancement
    historical/logging data remains readable
    athlete-global state may still be updated by unrelated inputs
      (e.g. a Withings body-comp sync is not a program-progression event)
    instance remains resumable — resuming does not silently fast-forward
      microcycles to "catch up" for elapsed calendar time

COMPLETED / ABANDONED / SUPERSEDED
    no further generation; terminal states
```

The load-bearing rule: **a paused instance must never silently advance microcycles
because calendar time passed while paused.** Whether readiness observations continue to
be *collected* while paused is separate from whether they *advance program state* —
collection can continue (it's athlete-global signal, useful regardless of program
status); advancement must not.

---

## Identity / day collision strategy

`Session.day_role` and `MicrocycleSlot.day_label`/`day_code` are plain strings today
(confirmed: no FK). This is the actual multi-program collision hazard the audit found —
not that a label needs a namespace prefix, but that a label is being used as identity at
all. The fix is to add a stable identity column,
**`program_revision_day_id`** (FK into the revision-scoped day table), to every place
that currently joins or looks up by `day_role`/`day_label`, and keep the string fields as
denormalized **display-only** copies populated from that identity at generation time —
never used for lookups or joins going forward.

`ProgramMovementState` (above) uses this same `program_revision_day_id` identity for its
day-scoping, rather than the loose string `day_id` `MovementState` uses today.

**`MissedDayRecord` is the one exception to this rule, not an application of it.** A
missed workout is an occurrence in an athlete's *execution timeline* — it happened (or
didn't) in a specific microcycle, in a specific week — not a property of the reusable
revision-day template. The same `ProgramRevisionDay` recurs across many weeks of a
mesocycle; "was Tuesday's Upper Push missed" is a question about one specific occurrence
of that template, not about the template itself. `MissedDayRecord`'s identity is
therefore corrected to reference the **scheduled/expected session occurrence** —
reached via `ProgramInstance → Microcycle → the specific slot/occurrence` — with
`program_revision_day_id` retained only as "what kind of day was expected," not as the
record's primary identity. This avoids ambiguity when the same revision day recurs across
multiple weeks.

Not every string in the codebase needs to become a foreign key — this fix is scoped to
the specific fields identified as acting as cross-table identity today, not a general
string-to-FK sweep.

---

## Equipment / program-requirement separation

Two things must never be conflated, even though both are "about equipment":

```
AthleteEquipment
    what is available now — athlete-global, mutable, changes whenever
    the athlete buys/removes/reconfigures hardware

ProgramRevisionEquipmentRequirement
    what this immutable program revision requires/allows per slot —
    revision-bound, frozen at publish time, never changes for a
    published revision
```

`CompatibilityResult` (program-catalog browsing) and per-session feasible-candidate
resolution (generation time) are both computed **live**, as:

```
ProgramRevision (frozen requirements) + current AthleteEquipment → CompatibilityResult
```

**A revision's requirements must not mutate; the athlete's available equipment can.**
This is what lets an athlete start a program, add a new machine or attachment mid-block,
and have IronLog immediately recognize additional feasible substitutions for adaptive
slots — without altering what the program revision itself means. It equally lets IronLog
detect when equipment becomes unavailable mid-program and identify exactly which slots
are affected and what legal substitutes remain, all without touching the revision.

This is a direct consequence of the equipment-feasibility invariant stated above: the
revision defines *legality* (what's allowed in a slot), the live equipment state defines
*feasibility* (what's physically possible right now), and the two are combined fresh on
every read — never pre-computed into the revision.

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
changed on the live `Program` mid-instance, that guarantee breaks silently. The publish
operation's materialization step therefore includes whichever of these apply as part of
what gets hashed and frozen: slot topology, progression configuration,
objective/priority configuration, adaptation authority, candidate-selection restrictions
(the *legality* half — see above), mesocycle topology, and transition/exit rules.
Anything capable of changing generated behavior is in scope for revision-binding — the
test is not "is this AI-related," it's "could this change what a session looks like."

**Equipment/capability availability is explicitly excluded from revision-binding** — see
[Equipment/Program-Requirement Separation](#equipmentprogram-requirement-separation)
above. A revision defines slot **requirements**; resolved **availability** against those
requirements is always computed fresh from current `AthleteEquipment` state.

---

## Historical / audit guarantees

Because `Mesocycle`/`Microcycle`/`Session` chain down to a `ProgramInstance` and its
pinned `revision_id`, a historical session generated under a genuinely published revision
remains explainable against that exact revision indefinitely, even after `Program`'s
authoring tables have since been edited many times over. `GenerationLog`/`AdvancementLog`
need no structural change — they already record provenance per session/event; they
simply now trace back through a stable, immutable revision rather than a live,
potentially-since-mutated `Program`.

### Legacy revision provenance (`r0`)

The one-time backfill (see [Migration Implications](#migration-implications)) must not
overstate what actually happened historically. Existing APEX Bridge sessions were
generated from **live, mutable** `Program`/`ProgramDay`/`Tier`/`TierExercise` tables —
there was no immutable revision in effect at the time. Materializing an `r0` revision
from today's live state and linking historical sessions to it for continuity is useful,
but claiming those sessions were "generated against `ProgramRevision r0`" would
manufacture a historical guarantee the old architecture never actually provided.

`ProgramRevision` therefore gets a `revision_origin` field:

```
PUBLISHED            — created by a real publish action; fully authoritative from
                        the moment of publication
LEGACY_RECONSTRUCTION — materialized after the fact from live-table state, to give
                        pre-migration history a linkable revision for continuity;
                        does NOT assert that generation was actually pinned to this
                        exact state at the time
```

Any audit/reporting output that surfaces "which revision generated this session" must
render `LEGACY_RECONSTRUCTION` visibly differently from `PUBLISHED` — e.g. "reconstructed
baseline (pre-migration)" vs. "APEX Bridge r7" — so a reader never mistakes reconstructed
provenance for a real historical immutability guarantee.

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
  `ProgramRevision`/`ProgramInstance`. A one-time backfill materializes a
  `ProgramRevision r0` (`revision_origin = LEGACY_RECONSTRUCTION`, see above) from the
  current live `Program` state and a `ProgramInstance` wrapping the existing
  `started_at` history, so historical data isn't orphaned by the new model — without
  overstating what guarantees applied at the time.
- `MovementState` → `MovementState` + `ProgramMovementState` is a real data-splitting
  migration against live production data with real athlete history — must go through
  IronLog's standard migration-based deployment process; **no production reseeding**
  (per `CLAUDE.md`'s standing warning that reseeding has previously destroyed live
  history).
- APEX Bridge's generated behavior must be equivalent before and after this migration —
  golden/regression tests captured **before** any schema change, re-verified after (see
  [Implementation Sequence](#implementation-sequence)).
- **Migration is a release-gated operation, not an in-place production change.** Before
  touching the live database: snapshot/backup production → restore to an isolated
  scratch database → run the migration there → backfill `r0` there → run integrity
  checks → run the full test suite → run the golden regression fixtures → only then
  apply to production. Migration steps are additive/reversible as far as practical. No
  reseeding, at any point in this sequence.

---

## Consequences

- `Program` stops being "the thing a session was generated under" — `ProgramInstance` /
  its pinned `ProgramRevision` is. This is the intended effect, not a side effect.
- Every runtime read path that currently touches live `Program`/`ProgramDay`/`Tier`/
  `TierExercise` must be identified and re-pointed at revision-scoped tables. The
  current-state audit's architecture map (§1) is the checklist for this.
- Authoring workflow is largely unchanged day-to-day (migrations still edit `Program`'s
  tables directly) — the new ceremony is the explicit publish action at the moment a
  revision needs to become executable, plus activation/transition of instances.
- Two schema families (authoring tables and revision-scoped mirrors) must be kept in
  lockstep going forward.
- `ProgramInstance` boundary snapshots (entry/exit) are deliberately lightweight audit
  artifacts, not a duplicate athlete-state database — see the field list under
  [Deferred Decisions](#deferred-decisions) resolution below; they capture *what
  condition the athlete entered/exited in*, referencing IDs/versions plus a small set of
  denormalized metrics, not a full copy of mutable state.

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
   architecture map is the checklist; the
   [revision-authority test](#the-definitive-revision-authority-test) is the mechanical
   proof this hasn't happened.
3. **`MovementState` split is a live-data migration**, not just a schema change —
   splitting existing rows into global vs. program-scoped state against production data
   with real athlete history. Higher blast radius than an additive migration; needs its
   own verification pass distinct from the schema migration itself.
4. **Publish-operation atomicity.** The publish action (validate → materialize → hash →
   commit) must not leave a partially-materialized revision visible to any reader if it
   fails partway — needs a real transaction boundary, not best-effort sequential writes.
5. **Revision materialization is a new deterministic pipeline** (copy + hash) with no
   existing test coverage — needs its own test suite proving materialization is
   idempotent and lossless before APEX migrates onto it.

---

## Deferred decisions

Resolved by this revision of the ADR (previously listed here as open): revision-creation
trigger (→ explicit publish), `PAUSED` semantics (→ defined conservatively above),
`MissedDayRecord`'s target identity (→ instance/microcycle occurrence, not
`ProgramRevisionDay` directly), and entry/exit snapshot scope (→ lightweight audit
fields, listed below). Still genuinely open:

- **Exact scope of the `day_role`/`day_label` → `program_revision_day_id` migration.**
  Which specific consumers get the new FK column added is deferred to an implementation-
  time audit of all read/write sites, not enumerated exhaustively here.
- **`ProgramInstance` entry/exit snapshot's exact field list.** Candidate fields:
  timestamp, body-composition summary, relevant e1RMs, active restrictions, important
  weak-point state, recovery baseline, goal/context metadata — preferring IDs/version
  references plus a small set of denormalized metrics over copying full mutable state.
  The exact final list is an implementation-time decision against this candidate set,
  not fixed here.
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
   `MissedDayRecord` keys off the instance/microcycle occurrence, not the revision-day
   template directly.
5. `ProgramInstance` status semantics are independent of `PlanStatus` — no enum sharing.
   A `PAUSED` instance never advances microcycles due to elapsed calendar time.
6. A `ProgramRevision`'s snapshot includes every behaviorally-relevant configuration
   surface — topology, progression, objective/priority config, adaptation authority,
   candidate-selection *legality* constraints, mesocycle topology, transition/exit rules.
   It explicitly does **not** include `AthleteEquipment` inventory — equipment
   *requirements* are revision-bound; equipment *availability* is always resolved live.
7. New adaptation-intent types extend the existing `Selections`/`SlotSelection` schema
   and propose → validate → clamp → commit pipeline; no second AI-action framework is
   introduced.
8. Equipment feasibility is deterministic end to end (inventory → capability resolver →
   movement requirements → program policy → AI preference); AI is never the source of
   truth for whether a piece of equipment exists or a movement is physically executable.
9. `Program` / `ProgramRevision` / `ProgramInstance` identity stays distinct throughout:
   authoring identity, immutable executable version, and one athlete's lifecycle running
   exactly one revision are never conflated back into one object.
10. **The same athlete state plus the same program revision produces the same observable
    prescription and deterministic decisions as the legacy APEX implementation** —
    resolved topology, prescription hash, topology hash, candidate menus, the
    LLM-invocation decision, validator result, final planned exercises/sets, progression
    result, weak-point behavior, readiness modification, missed-day behavior, and
    mesocycle/microcycle identity — except where an explicitly documented migration
    difference is unavoidable and called out as such.
11. A `ProgramRevision`'s `revision_origin` distinguishes `PUBLISHED` from
    `LEGACY_RECONSTRUCTION`; audit/reporting output never presents a reconstructed
    baseline as if it were a real historical immutability guarantee.
12. The production database is never reseeded as a shortcut for any part of this
    migration; migration follows the release-gate sequence in
    [Migration Implications](#migration-implications).

### The definitive revision-authority test

The mechanical proof that a `ProgramRevision` is actually authoritative at runtime, not
merely an audit artifact — required to pass before Phase 1 is considered complete:

```
publish APEX r1
activate a ProgramInstance using r1
generate session A

drastically edit live Program authoring tables (do NOT publish)

generate session B from the SAME active instance
    → session B must continue behaving according to r1, identically to
      what it would have before the live-table edit

publish r2 (from the now-edited authoring state)
    → the current (still-active) instance must still use r1
    → only a newly activated instance may use r2
```

Any deviation from this sequence's expected outcomes means a read path was missed —
treat it as Risk #2 realized, not as an acceptable edge case.

---

## Implementation sequence

Full phasing lives in [the roadmap](../roadmaps/program-library-roadmap.md). At the
level this ADR is responsible for:

```
1. Golden APEX baseline
2. ProgramRevision schema (including publish lifecycle + slot-requirement typing)
3. Immutable normalized topology (materialization pipeline + hashing)
4. ProgramInstance lifecycle (status semantics, active-instance pointer)
5. Legacy r0 reconstruction/backfill (revision_origin = LEGACY_RECONSTRUCTION)
6. Runtime revision execution (re-point all read paths; run the revision-authority test)
7. Collision/state-scope migration (MovementState split; day identity fix)
8. Regression verification (golden baseline re-run; full test suite; migration release
   gate per Migration Implications)
```

Do not expand AI behavior or add additional production programs until this slice is
proven end to end.

---

## Revision History

- **2026-09-10 (initial):** Program/ProgramRevision/ProgramInstance decision, Option B
  execution model, MovementState field split, identity strategy, revision-bound
  adaptation policy.
- **2026-09-10 (this revision):** Explicit-publish lifecycle (resolves the
  revision-creation-trigger deferral); `LEGACY_RECONSTRUCTION` provenance for `r0`;
  conservative `PAUSED` semantics; `MissedDayRecord` re-targeted to instance/microcycle
  occurrence rather than revision-day template; lightweight entry/exit snapshot scope;
  explicit `AthleteEquipment` vs. `ProgramRevisionEquipmentRequirement` separation;
  slot-requirement typing (`ANCHOR`/`SEMI_ANCHOR`/`ADAPTIVE_SLOT`) included in Phase 1
  schema; equipment-feasibility chain elevated to a named reinforced invariant; expanded
  golden-baseline acceptance criterion; migration release-gate sequence; the definitive
  revision-authority test added as a required Phase 1 pass/fail check. Status moved from
  Proposed to Approved.
