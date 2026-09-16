# IronLog V2 — Program-Library Current-State Architecture Audit

**Date:** 2026-09-10
**Purpose:** Phase 0 deliverable (current-state map + gap analysis) supporting
[`docs/adr/0001-program-library-architecture.md`](../adr/0001-program-library-architecture.md).
Produced by delegated investigation of the actual codebase — every claim below is
grounded in a specific file/class, not inferred from documentation alone. This is a
snapshot as of 2026-09-10; re-verify against current code before trusting a specific
citation in a later session.

---

## 1. Current-state architecture map

| Concern | Owning code |
|---|---|
| Program definition | `ironlog/models/program.py` — `Program`, `ProgramDay`, `Tier`, `TierExercise`, `MesoRotation`, `MicrocycleParityRotation` (alias `WeekParityRotation`), `SlotMovementOverride`. Seeded once by `ironlog/generation/program_seed.py:307` (`seed_program()`), which creates exactly one `Program` row. |
| Program activation | `ironlog/api/app.py:1456` `POST /programs/{program_id}/start` — gated on `compute_load_trust` (`_needs_attention_count == 0`), sets the singleton `EngineState.active_program_id` (`ironlog/models/library.py:142`) and `Program.started_at`. No deactivation path exists. |
| Mesocycle construction | `ironlog/engine/advancement.py`, `scripts/plan_next_mesocycle.py` (creates a `Mesocycle` from a `MesocycleTemplate`, sets `program_prescription_hash` via `ironlog/engine/program_hash.py`). |
| Microcycle construction | Same advancement engine; `Microcycle`/`MicrocycleSlot` rows (`ironlog/models/periodization.py`), materialized per mesocycle (`scripts/bootstrap_microcycle_one.py` for the first mesocycle). |
| Session generation | `ironlog/generation/skeleton.py:lay_skeleton()` reads `Program → ProgramDay → Tier → TierExercise`, resolving overrides in precedence order `SlotMovementOverride > MicrocycleParityRotation > MesoRotation > TierExercise.movement_id`, into a `Skeleton`. `ironlog/generation/context.py:resolve_context()` builds `GenerationContext`. `ironlog/generation/loop.py:generate_session()` orchestrates: gate (`should_invoke_llm`) → deterministic `program_selections` (no LLM) **or** LLM `propose_validate_repair` → `assemble()` (`ironlog/generation/assembler.py`) → `commit_session()`. |
| Progression | `ironlog/engine/progression.py`, `ironlog/engine/loading.py`, `ironlog/persistence/run_analysis.py` (writes `E1rmHistory`, tier/ceiling fields — never `current_load`); `commit_session` (`loop.py:74`) is the sole writer of `current_load`/`ht_plates`/`ht_band_config`. |
| Weak-point / stall logic | `ironlog/engine/stall.py:detect_stall` (line 46 gates on `objective == Objective.PROGRESS`, early-returns otherwise), `ironlog/engine/analysis.py:162` (same gate), `MovementWeaknessSignal` (`ironlog/models/library.py`), `GET /weak-points` (`app.py:1600`). |
| Readiness / recovery | `ironlog/engine/readiness.py` feeds `DailyReadiness` (raw inputs); `RecoveryStatus` (`periodization.py`) is the resolved daily status (`NORMAL|CAUTION|POOR`) consumed by the envelope resolver. |
| Deload | `DeloadState` (`periodization.py`), attributed to a `Microcycle`, resolved via `resolve_envelope` (`periodization_resolver.py`, called from `app.py:1809`). |
| Body composition | `BodyCompState` timeline (`periodization.py`) — independent axis; `WithingsCredentials` (`library.py`) for sync. |
| AI/LLM generation | `ironlog/generation/proposer.py` (`Proposer` protocol, `PROPOSER_SYSTEM_INSTRUCTION`, `SELECTIONS_JSON_SCHEMA`), concrete impl `ironlog/generation/gemini.py`, invoked from `ironlog/generation/repair.py:propose_validate_repair`. |
| Validation | `ironlog/engine/validator.py:validate()` — deterministic, called on both the quiet and LLM paths. |
| Logging / audit | `GenerationLog` (`library.py` — prompt/selections/clamps/repairs/approval_mode/fallback_used), `AdvancementLog` (`periodization.py` — append-only reconciliation audit). |
| Program hashing / drift | `ironlog/engine/program_hash.py` — `compute_program_prescription_hash()` (all fields affecting generated training) and `compute_slot_topology_hash()` (day/rest skeleton only). Compared against `Mesocycle.program_prescription_hash` / `Microcycle.slot_topology_hash` at commit time (`loop.py:117-119`); mismatch raises `BlockedPlanError('PROGRAM_DRIFT')`. |
| Android API contract (program-related) | `ironlog/api/app.py`: `GET /programs/{id}/slots`, `GET /programs/{id}/days`, `GET /programs/{id}/wizard-state`, `POST /programs/{id}/wizard-resolve`, `POST /programs/{id}/start`, `GET /overrides`, `POST /overrides/{id}/revert`, `GET /training/plan/current`, `GET /training/macrocycles/{id}`. |

---

## 2. Model inventory

| Model | Exists | File:class | Mutable / immutable | Scope |
|---|---|---|---|---|
| `Program` | Yes | `program.py:Program` | `started_at`/`ended_at` mutable event-facts; everything else static post-seed, edited only via ad hoc migrations | Global singleton — only one row has ever existed |
| `ProgramDay` | Yes | `program.py:ProgramDay` | Static | Program-scoped |
| `Tier` | Yes | `program.py:Tier` | Static | Program-scoped |
| `TierExercise` | Yes | `program.py:TierExercise` | Static (edited live via migrations) | Program-scoped |
| `MesoRotation` | Yes | `program.py:MesoRotation` | Static, keyed by `meso_number`/`mesocycle_id` | Program-scoped |
| `MicrocycleParityRotation` / `WeekParityRotation` | Yes | `program.py:MicrocycleParityRotation` | Static | Program-scoped |
| `SlotMovementOverride` | Yes | `program.py:SlotMovementOverride` | Mutable (`active` flag) | Program-scoped, note-driven |
| `Macrocycle` | Yes | `periodization.py:Macrocycle` | `status`/`planning_state`/dates mutate | Athlete-global (no program FK) |
| `MesocycleTemplate` | Yes | `periodization.py:MesocycleTemplate` | Static, reusable | Global template |
| `Mesocycle` | Yes | `periodization.py:Mesocycle` | `status`, `program_prescription_hash`, dates mutate | Instance; carries `program_id` FK — the one place a Mesocycle is explicitly bound to a Program |
| `Microcycle` | Yes | `periodization.py:Microcycle` | `lifecycle_status`, `drift_status`, `slot_topology_hash` mutate; `planned_posture` meant immutable post-creation (convention only, not DB-enforced) | Instance, scoped to a Mesocycle |
| `MicrocycleSlot` | Yes | `periodization.py:MicrocycleSlot` | `resolution`/`session_id` mutate | Instance, per-Microcycle |
| `Movement` | Yes | `library.py:Movement` | Static definition | Global library |
| `MovementState` | Yes | `library.py:MovementState` (line 210) | Fully mutable | **Athlete-global but keyed `(movement_id, day_id)`, not by program** — see §3 |
| `Session` | Yes | `session.py:Session` | `status`, `approved_at`, `analyzed_at` mutate | Instance, links to `microcycle_id`; `day_role: str` is a plain string, no FK |
| `PlannedExercise` | Yes | `session.py:PlannedExercise` | Static per session | Instance |
| `PlannedSet` | Yes | `session.py:PlannedSet` | Static per session | Instance |
| `SetLog` | Yes | `session.py:SetLog` | Append-only actuals | Instance |
| `AdvancementLog` | Yes | `periodization.py:AdvancementLog` | Append-only | Athlete-global audit |
| `BodyCompState` | Yes | `periodization.py:BodyCompState` | Timeline (`effective_from/to`) | Athlete-global |
| `RecoveryStatus` | Yes | `periodization.py:RecoveryStatus` | One resolved row/day, upserted | Athlete-global |
| `DeloadState` | Yes | `periodization.py:DeloadState` | `active`/`resolved_at` mutate | Attributed to a Microcycle |
| `Objective` (enum) | Yes | `enums.py:Objective` = `MAINTAIN \| PROGRESS \| MEASURE` | n/a | Consumed at `stall.py:46`, `analysis.py:162`, `progression.py:20`, `assembler.py:275-286` |
| `Equipment` | Yes | `library.py:Equipment` (line 37) | Static vocabulary + hard load floors | Global — no per-athlete inventory concept |
| `BandPair` | Yes | `library.py:BandPair` (line 48) | `calibration_status`/`usable` mutate | Global equipment calibration |

**Not present as distinct tables:** `ProgramDefinition`/`ProgramRevision`/`ProgramInstance` — `Program` conflates all three today. **Not present:** `AthleteEquipment` inventory, `MovementRequirement` expression schema (see §5).

---

## 3. Direct answers

**What `Program` means today:** conflated. `Program` has no revision/version field and no lifecycle enum — just `started_at`/`ended_at` datetimes. It is simultaneously the *definition* (the seeded skeleton, mutated in place via ad hoc migrations — e.g. migration 072's rear-delt split) and the *instance* (the thing pointed to by `EngineState.active_program_id`). Only one `Program` row has ever existed; nothing enforces or expects more than one.

**Existing revisioning / hashing / drift detection:** a derived-hash drift check, not a revision history. `ironlog/engine/program_hash.py` computes `compute_program_prescription_hash()` (day roles, tier structure, rep/duration ranges, RPE caps, scheme, HT unification) and `compute_slot_topology_hash()` (day/rest skeleton only) by walking `Program → ProgramDay → Tier → TierExercise` and SHA-256'ing a normalized JSON projection. These hashes are snapshotted onto `Mesocycle.program_prescription_hash`/`Microcycle.slot_topology_hash` at planning time, then re-computed and compared at every `commit_session` (`loop.py:117-119`) — mismatch raises `BlockedPlanError('PROGRAM_DRIFT')`. There is **no versioned history** of past program states; it's a single live mutable `Program` compared against a point-in-time hash. `docs/build-plan.md` Open Item #3 explicitly names "a `Program.revision` counter for real write concurrency" as still unscoped.

**Single-program hardcoding:** "APEX" in this repo is the athlete's equipment brand (APEX Bench/rack/etc.), **not** a program-identity assumption — a false lead investigated and ruled out. The program is named "APEX Bridge." No code hardcodes `program_id=1` (grep confirmed zero hits outside tests). The real single-program assumption is structural: `lay_skeleton()` (`skeleton.py:136-183`) resolves `program_id` from the one global `EngineState.active_program_id`, and if unset falls back to matching `ProgramDay.day_role == day_role` with **no program filter at all** ("back-compat for existing single-program callers"). `day_role` strings are used as a de facto cross-cutting identifier throughout (`Session.day_role`, `MicrocycleSlot.day_label`, plain strings — see §2) — two concurrent programs with overlapping `day_role` names would silently collide in `lay_skeleton`'s `.first()` query and in slot/session matching. `MovementState` is keyed `(movement_id, day_id)` (unique constraint `uq_movementstate_movement_day`), not `(movement_id, program_id, day_id)` — switching programs would inherit or corrupt load state on any colliding `(movement_id, day_id)` pair. **Note:** `MissedDayRecord` (`program.py:49`) already uses a proper `program_day_id` FK, not a string — it is *not* part of this collision risk.

**Athlete-global vs. program-instance-scoped state:**
- Clearly athlete-global: `Movement`, `Equipment`, `BandPair`, `BodyCompState`, `RecoveryStatus`, `AdvancementLog`, `MesocycleTemplate`, `Macrocycle` (no program FK), `WithingsCredentials`, `DailyReadiness`.
- Clearly program-instance-scoped: `ProgramDay`/`Tier`/`TierExercise`/`MesoRotation`/`MicrocycleParityRotation`/`SlotMovementOverride`, `Mesocycle` (has `program_id` FK), `Microcycle`/`MicrocycleSlot` (transitively).
- Ambiguous — resolved by field-level classification, not a blanket call (see the ADR's State Ownership Model): `MovementState` mixes true athlete-global fields (`calibration_status`, `e1rm`, `e1rm_updated_at`, `assist_level`, `ht_plates`, `ht_band_pair_id`, `ht_felt_peak`, `unassisted_max_rolling` — physical/capability facts) with program-progression-scoped fields (`current_load`, `current_increment_tier`, `current_rep_scheme`, `rep_scheme_locked_until`, `consecutive_ceiling_sessions`, `consecutive_failed_progressions`, `confirmed_at`, `consecutive_advance_count`, `active_rule`, `current_body_position`, `current_rep_target`, `duration_ladder`, `current_duration_seconds`, `current_rope`, `pending_load_delta`, `pending_ht_plates`, `pending_ht_band_config`, `stall_signal` — progression-ladder/objective-gated execution state that depends on the *current program's* rep scheme and progression rules).

**LLM/AI integration point:** `ironlog/generation/proposer.py` defines the `Proposer` protocol (`propose(payload) -> Selections`) and a fixed `SELECTIONS_JSON_SCHEMA` with exactly four fields per slot — `slot_id`, `movement_id`, `variant`, `technique_tags` — **no numeric fields at all**. `PROPOSER_SYSTEM_INSTRUCTION` (`proposer.py:6-23`) explicitly states: "select movements, variants, and technique tags ONLY. Never compute or set loads, weights, or reps." Called from `repair.py:propose_validate_repair()`, orchestrated by `loop.py:generate_session()` only when `should_invoke_llm()` detects a deviation signal — otherwise the deterministic `program_selections()` path runs with zero LLM calls (the "quiet week" gate). After the LLM returns `Selections`, `assemble()` computes loads/reps deterministically, then `validator.py:validate()` runs; `apply_clamps` repairs out-of-bounds output before falling back to `fallback_session()`. **This is already exactly the "typed, bounded intent" shape later capability work should generalize** — it's just narrowly scoped to slot/variant selection today, not a general adaptation-intent vocabulary.

**`Objective` enum:** `MAINTAIN | PROGRESS | MEASURE` (`enums.py:58`). Resolved per-movement via `Movement.objective_override` (nullable) falling back to `PhasePolicy.default_objective`, also factoring in the periodization envelope's `progression_mode` (`assembler.py:275-286::_effective_objective`). Consumed as a hard gate at `stall.py:46`, `analysis.py:162`, `progression.py:20`; `run_analysis.py:76,484` filter e1RM history to `objective == PROGRESS` for stall-window analysis.

**Equipment/capability representation (relevant to the equipment-capability principle in the ADR):** `Equipment` (`library.py:37`) is a global vocabulary table with hard load floors/steps and a single `available_phase: EquipPhase` gate ("when it joins the gym") — there is no per-athlete inventory concept (owned/enabled/quantity/location), and nothing distinguishes "exists in the master catalog" from "this athlete actually has access to it." `Movement.load_equipment_id` is a single FK to the one load-bearing equipment item; `Movement.equipment_tags` (line 73) is an unstructured flat JSON list of strings — there is no `ALL_OF`/`ANY_OF` expression capability, so a movement requiring *either* a landmine *or* a barbell-plus-attachment, or requiring a *combination* of specialty equipment, cannot be represented today. `BandPair` (line 48) is the only place with a real per-item calibration/usability state (`calibration_status`, `usable`), but it is not connected to a general capability-derivation layer.

---

## 4. Gap analysis

| Proposed capability | Classification | Justification |
|---|---|---|
| Declarative program schema (YAML/JSON) compiled to DB | **Partially supported** | `docs/program/source/*.yaml` + `program_seed.py` already do YAML→DB compilation, but it's a one-shot seed script, not a general "compile any program definition, including updates" pipeline; re-running against a live DB isn't safe/idempotent (build-plan Open Item #4 — recurring seed/live-DB drift debt), and there's no schema validation layer beyond Python-level asserts. |
| `ProgramDefinition`/`ProgramRevision` separate from `ProgramInstance` | **Requires new abstraction** | `Program` conflates definition+instance with no immutability and no revision history. The hash mechanism is the closest analog but hashes a *live mutable* object, not a stored immutable revision — `program_hash.py`'s projection logic is directly reusable as a revision's content-hash function, but the revision concept itself does not exist. |
| Program-level adaptation-policy / AI-authority matrix | **Requires new abstraction** | No policy table exists; the LLM's authority is hardcoded globally in one fixed `PROPOSER_SYSTEM_INSTRUCTION` string, not per-program-configurable. |
| Typed adaptation intents (`EMPHASIZE_WEAK_POINT`, `SWAP_ADAPTIVE_SLOT`, ...) resolved deterministically | **Generalize existing** | The mechanism already exists in miniature: `Selections`/`SlotSelection` is exactly a typed, schema-constrained, non-numeric intent object, resolved deterministically downstream by `assemble()`+`validate()`. Extending the vocabulary is additive to a proven pattern, not new architecture. |
| Multiple concurrent program definitions in the catalog | **Requires extension** | No FK forbids a second `Program` row, but `day_role`-as-global-identifier and `MovementState`'s `(movement_id, day_id)` keying mean two active programs would collide today; `active_program_id` is a single pointer so "concurrent" currently means "one active + others dormant" at best. |
| Program transition/lifecycle (`PLANNED/ACTIVE/PAUSED/COMPLETED/ABANDONED/SUPERSEDED`) | **Requires extension, pattern already proven** | `Program` has no status enum at all. `Macrocycle`/`Mesocycle` already use `PlanStatus` (`PLANNED/ACTIVE/COMPLETE/ABANDONED` — confirmed exact values, `periodization.py:17-21`) — the pattern is proven, just not applied at the Program level, and its exact value set does not cover `PAUSED`/`SUPERSEDED` (see ADR §"State Ownership" on why this argues for a distinct enum). |
| Athlete state carrying forward across program changes | **Requires extension** | Movement/e1RM/library state is already athlete-global in intent, but `MovementState`'s `day_id` keying ties it to a specific program's day-labeling scheme — needs the field-level split described above before this is safe across a program swap. |
| Program preview/simulation before activation | **Requires new abstraction** | No simulation/dry-run capability exists. Closest analogs: `--dry-run` on one-off scripts (`bootstrap_microcycle_one.py`) and the wizard's read-only trust computation (`GET /programs/{id}/wizard-state`) — neither previews generated *sessions*. |
| Candidate scoring/ranking layer before LLM selection | **New capability, but consistent with existing pattern** | No scoring/ranking exists — the LLM picks directly from a fixed `candidates` menu per slot. A deterministic pre-filter/score before the LLM sees candidates is architecturally consistent with the existing pipeline shape (deterministic skeleton → bounded LLM choice → deterministic validate), so it's a legitimate extension in the existing spirit, but genuinely new code. |
| Athlete equipment inventory + movement capability requirements (`AthleteEquipment`, `MovementRequirement` `ALL_OF`/`ANY_OF`) | **Requires new abstraction** | `Equipment` is a global catalog with no per-athlete ownership concept; `Movement.equipment_tags` is an unstructured flat list with no expression logic — see §3's equipment/capability finding. |
| Rich exercise metadata (strength curve, stability, joint stress, substitution family, etc.) | **Partially supported** | `Movement` already carries a meaningful set of descriptive dimensions (`region`, `lift_category`, `primary_muscle`/`secondary_muscles`, `progression_mode`, `family`/`is_family_anchor`, `unilateral`) — extending this is additive to an existing rich model, not greenfield. |
| Program compatibility scoring against owned equipment | **Requires new abstraction** | No compatibility computation exists; depends on the equipment-inventory/capability-requirement gap above being closed first. |

**Key finding for the ADR:** the biggest real gap is not the AI/typed-intent layer — that pattern (deterministic skeleton → schema-bounded LLM proposal → deterministic validate/clamp) is already fully proven for the single-program case and generalizes cleanly. The biggest real gaps are (1) `Program` has no identity/versioning/lifecycle model separate from "the one active thing," with `day_role`-as-identifier and `MovementState`'s `(movement_id, day_id)` key as the two concrete structural collision points, both already named as known soft spots in `docs/build-plan.md` Open Items #3 and #4; and (2) equipment is a flat global vocabulary with no per-athlete inventory or expression-based capability derivation, which blocks both program-compatibility scoring and equipment-aware candidate filtering.
