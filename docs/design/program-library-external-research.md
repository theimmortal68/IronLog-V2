# IronLog V2 — Program-Library External-Project Research

**Date:** 2026-09-10
**Purpose:** Phase 0 deliverable (external-project findings) supporting
[`docs/adr/0001-program-library-architecture.md`](../adr/0001-program-library-architecture.md).
Each project below was investigated for one specific architecture question relevant to
IronLog's program-library work, via public GitHub source/docs (not marketing copy where
avoidable). Confidence level is stated per project — treat "Low/Medium" findings as
leads to re-verify, not settled facts.

---

## 1. Liftosaur (`astashov/liftosaur`, AGPL) — primary reference

**Question:** How does it represent programs as data?

**Pattern:** Programs are defined in **Liftoscript**, a small JS-like DSL evaluated by a
custom interpreter (`liftoscriptDocs.ts`, `PlannerEvaluator`). A program has a
**declarative form** (`IPlannerProgram` — human-readable `exerciseText` strings per
week/day, e.g. `if (completedReps >= reps) { state.weight = state.weight + 5lb }`) and an
**evaluated form** (`IEvaluatedProgram`, produced by `PlannerEvaluator_evaluate()`)
containing fully parsed `IPlannerProgramExercise[]` plus `states: IByTag<IProgramState>`
(progression state keyed by exercise tag) and `errors: IEvaluatedProgramError[]`.
`IProgram` carries `nextDay`, `exercises: IProgramExercise[]` (each with warmup sets +
its own state), `clonedAt` (clone/versioning timestamp), and tombstone arrays
(`deletedWeeks/Days/Exercises`) for sync. Active workouts are captured as
`IHistoryRecord`, a snapshot derived from the evaluated program at the moment of
execution — **definition, evaluated-instance, and performed-history are three distinct
types.** Built-in programs (5/3/1, GZCLP, Starting Strength, in `builtinPrograms.ts`) are
just Liftoscript text run through the same evaluator as user-authored ones — no separate
"built-in engine."

**Reusable for IronLog:** the three-tier separation (declarative definition → evaluated/
typed instance → performed-history record) maps closely onto IronLog's
definition-vs-state and planned-vs-logged invariants. The clone-on-start pattern
(`clonedAt` timestamp, program cloned into the user's own copy before running) is a
clean, concrete mechanism for "immutable/versioned snapshot when a user starts a
program." Treating built-in and user programs as the same data shape through the same
engine directly supports the multi-program-library goal without a special-cased
"official program" code path.

**Do NOT borrow:** Liftoscript lets program logic *itself* compute `state.weight` via
arbitrary embedded script — progression math lives inside the program's own DSL,
evaluated per-exercise. That inverts IronLog's invariant that a single, rules-owned
deterministic engine (not a per-program embedded script) computes all math. Adopting
Liftoscript-style embedded scripting would let each program define its own math path —
exactly the surface IronLog wants centralized and auditable, not distributed into
per-program text.

**Confidence:** High — pulled actual `program.ts` field shapes and the `src/models` file
listing directly from source, not just README text.

---

## 2. wger (`wger-project/wger`, AGPL)

**Question:** How does it represent deterministic routine/progression rules via its REST
API?

**Pattern:** Confirmed via `wger.readthedocs.io/en/latest/api/routines.html` (real docs).
Hierarchy: `Routine` (≤120 days) → `Day` (ordered) → `Slot` (an exercise position/
superset) → `SlotEntry` (a specific exercise in that slot). Progression is **not** a
value on the entry — it's a separate config-record system: `WeightConfig`,
`RepetitionsConfig`, `SetsConfig`, `RirConfig`, `RestConfig` (each with a "max-" range
variant). Each config record specifies an `operation` (`r`=replace, `+`/`-`=adjust) and a
`step` type (`abs` or `percent`), plus a `repeat` flag so a rule persists across
iterations without restating it. Progression can be gated by a `requirements` field — a
JSON object with a `rules` list; the rule only fires if all listed conditions were met in
at least one log of the *previous* iteration. Actual performance is logged separately:
`WorkoutSession` (session/date) and `WorkoutLog` (both actual `weight/repetitions/rir/
rest` **and** the prescribed `*_target` values side by side, on the same row).

**Reusable for IronLog:** the config-record pattern (declarative operation + step-type +
conditional `requirements`) is a strong, concrete reference for representing
deterministic progression rules as *data* rather than code — close to what IronLog's
typed-intent layer wants for "bounded" adaptation rules. The `WorkoutLog` pattern of
storing prescribed-target and actual-performed values side by side on the same log row
is a directly reusable planned-vs-performed representation (IronLog's `PlannedSet`/
`SetLog` split already achieves the same separation via two rows instead of one — no
change implied, just corroboration the pattern is sound).

**Do NOT borrow:** nothing here conflicts with IronLog's invariants — this is arguably
the closest existing precedent for rules-as-data. One caution: wger's config system has
no concept of an AI/model proposer at all — pure human/rule-authored — so it offers no
precedent for the bounded-intent-from-model half of IronLog's design; that part still has
to be designed fresh.

**Confidence:** High for the routine/progression model (real docs page quoted above with
real field/model names). Did not verify actual Django source files, so exact Python class
names are inferred from docs, not from a grep of `models.py`.

---

## 3. LiftLog (`LiamMorrow/LiftLog`, AGPL)

**Question:** How does it package/version/import a program?

**Pattern:** Verified via `docs/PlanFileFormat.md`. Programs are packaged as a single
`.liftlogplan` JSON file with an explicit **root-level `version` field (currently 3)**
and a **separate, independently-incrementing `version` field on each session** (currently
6) — schema versioning is granular per-substructure, not one monolithic file version.
Structure: root `{version, name, lastEdited, sessions[]}`; each session `{version, name,
notes, exercises[]}`; exercises are a tagged union (`WeightedExerciseBlueprint` |
`CardioExerciseBlueprint`). Weighted exercises carry `plannedSets[]` (min/max rep targets
per set), `restBetweenSets`, a `resistance` type (external/bodyweight/none), and a
`progression` block. Decimal values are serialized as strings (`"2.5"`) to avoid float
precision issues; durations use ISO-8601 (`"PT3M"`). There is an **authoritative JSON
Schema** at `docs/schemas/program-blueprint/ProgramBlueprint.json`, auto-generated from
the app's own source models (schema-from-code, not hand-maintained), plus a standalone
dependency-free validator script giving field-level error messages — built because the
app's own inline validation errors were too generic.

**Reusable for IronLog:** the per-substructure version field (root version + independent
session version) is a good concrete pattern for immutable/versioned program definitions
where sub-parts can evolve independently of the whole. Auto-generating a JSON Schema from
source models, plus a standalone validator, is directly applicable to IronLog's
"program definitions must be immutable/versioned" requirement — a mechanical,
spec-driven way to validate a new revision before it's accepted. String-encoded decimals
for weight fields is a good convention for a deterministic engine to avoid
float-precision progression bugs.

**Do NOT borrow:** the doc explicitly ties `.liftlogplan` to "how to generate plans with
an AI for import" — the AI-authors-the-whole-plan-file workflow is the opposite of
IronLog's invariant that the model only proposes bounded typed intents, never raw
structural/numeric program content directly. If IronLog borrows the file-format/
versioning mechanics, that specific entry point should not be replicated.

**Confidence:** High for the file-format description — came directly from
`PlanFileFormat.md` content. Did not independently open `ProgramBlueprint.json` itself to
confirm exact key names beyond what the doc summarized.

---

## 4. LiftTrace (`TraceApps/lifttrace`, AGPL)

**Question:** How can AI interact with a self-hosted structured training system?

**Pattern:** The AI coach is given broad read context — the README states it "reads your
workouts, programs, PRs, body stats, and coach prescriptions" — and is granted **18
total tools** for action, including directly logging a workout, starting a program
template, updating an active program, or (coach role) prescribing a workout to an
athlete, all "conversationally." Could not retrieve an enumerated tool list or an
explicit propose-vs-auto-execute statement — the `/docs` directory only exposed a
`screenshots` subfolder in the fetch attempted, and the README's disclaimer language
("Trace AI answers can be incorrect... not medical/health/fitness-professional software")
is a liability disclaimer, not an architectural boundary statement. Multi-provider LLM
support (Claude/OpenAI/Gemini/OpenAI-compatible) is mentioned, with coaching logic said
to stay server-side.

**Reusable for IronLog:** very little concretely — the weakest-verified of the four. The
one real signal is architectural, by omission: LiftTrace's model directly mutates
workout logs and active programs as a first-class capability ("update your active
program" conversationally), with no visible propose/dispose gate described in the
docs reached.

**Do NOT borrow:** this is the clearest anti-pattern relative to IronLog's core
invariant. If accurately represented in the README (direct program/log mutation via LLM
tool calls, no stated propose/approve boundary), LiftTrace is exactly the shape IronLog's
ADR should point to as "what not to build" — LLM tools that can write final numeric/
programmatic state directly, rather than emitting a bounded intent for a separate
deterministic layer to accept or reject.

**Confidence:** Low/Medium. Only the top-level README rendered; the docs directory
listing failed to load real file contents (GitHub's tree view errored during the fetch),
so it's unconfirmed whether a more detailed architecture doc exists elsewhere in the repo
describing an actual propose/approve boundary. Treat "no stated boundary" as "not found
in what was reachable," not a confirmed absence — worth a follow-up direct fetch of
specific doc URLs (e.g. `docs/ai-coach.md`, if it exists) before citing this as settled.

---

## Synthesis for the ADR

| Project | Contributes |
|---|---|
| Liftosaur | Definition → evaluated-instance → performed-history separation; clone-on-start immutability pattern |
| wger | Rules-as-data progression config; target-vs-actual on one log row |
| LiftLog | Per-substructure schema versioning; schema-from-code + standalone validator |
| LiftTrace | Concrete anti-pattern: direct AI mutation with no stated propose/dispose boundary |

None of the four projects centralize deterministic math the way IronLog already does
(`assemble()`/`validator.py`) while also constraining the LLM to a closed, non-numeric
schema (`Selections`) — IronLog's existing proposer architecture is, on this evidence,
already ahead of all four references on the specific "bounded AI" axis. The borrowable
value is entirely on the *program representation/versioning* side (Liftosaur, LiftLog)
and the *progression-rules-as-data* side (wger), not on the AI-boundary side.
