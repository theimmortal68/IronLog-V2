## Objective
Implement the two pure, deterministic hash functions the advancement design depends on: `program_prescription_hash` (broad — everything that can alter generated training) and `slot_topology_hash` (narrow — only what determines the weekly slot skeleton).

## Context
Source: design doc §5c. Confirmed via codebase survey: `ironlog/models/program.py` defines `Program` (`id, name, phase, duration_weeks, started_at, ended_at`), `ProgramDay` (`id, program_id, day_index, day_role, is_rest, warmup_config`), `Tier` (`id, program_day_id, paired_tier_id, tier_label, tier_order, tier_kind, rest_seconds, rounds, shoe`), `TierExercise` (`id, tier_id, slot_id, movement_id, exercise_order, tier_role, pattern, knee_modality, rep_low, rep_high, duration_low_seconds, duration_high_seconds, rpe_cap, scheme, unified_ht_group, derived_from_unified_group, derive_ratio`).

## File targets
- `ironlog/engine/program_hash.py` (new file).
- `tests/test_program_hash.py` (new file).

## Changes
Two public functions, both pure (no DB writes, take a loaded `Program` ORM object or equivalent DTO and return a `str` hash):

```python
def compute_program_prescription_hash(program: Program) -> str: ...
def compute_slot_topology_hash(program: Program) -> str: ...
```

**Canonicalization (shared helper, design §5c fix #7):** before hashing anything, both functions sort their input collections deterministically:
- `ProgramDay`s by `day_index`
- `Tier`s within a day by `tier_order`
- `TierExercise`s within a tier by `exercise_order`

Then serialize to a stable form (e.g. `json.dumps(..., sort_keys=True)` over a plain-dict projection built from the sorted collections — not the ORM objects directly, to avoid hashing irrelevant SQLAlchemy internals) and hash with a standard digest (e.g. `hashlib.sha256(...).hexdigest()`).

**`compute_program_prescription_hash` covers** (design §5c, revision 6 broadened scope): `ProgramDay.day_index/day_role/is_rest`, `Tier.tier_order/tier_kind/rest_seconds/rounds`, `TierExercise.exercise_order/movement_id/tier_role/pattern/rep_low/rep_high/duration_low_seconds/duration_high_seconds/rpe_cap/scheme`. Excludes irrelevant metadata (e.g. `Program.started_at/ended_at`, any purely-cosmetic label fields) — if unsure whether a field is prescription-relevant, err toward including it (a false-positive drift block is far cheaper than a false-negative that silently accepts a real prescription change).

**`compute_slot_topology_hash` covers ONLY** (design §5c, revision 8 fix #2 — strict subset of the above): `ProgramDay.day_index`, `ProgramDay.is_rest` (this is what determines `MicrocycleSlot.slot_type` — `TRAINING` vs `REST`), and day ordering. Nothing about `Tier`/`TierExercise` content at all — a prescription-only change (different exercise, different reps) must produce an *unchanged* `slot_topology_hash` even though `program_prescription_hash` changes.

## Edge cases
- Two `Program` states that differ only in row/JSON-key serialization order (not content) must hash identically — this is the canonicalization regression guard from design §9. Write the test as: build the same logical `Program` twice with collections inserted/queried in different orders, assert both hash functions produce identical output.
- A `Program` edit that changes only `TierExercise.rep_low/rep_high` (no day/tier/exercise added or removed) must change `program_prescription_hash` but leave `slot_topology_hash` unchanged.
- A `Program` edit that flips one `ProgramDay.is_rest` (a day changes from `TRAINING` to `REST`) must change *both* hashes.
- `movement_id` participates in the prescription hash (changing which movement is prescribed is a prescription change) but is irrelevant to topology.

## Dependencies
None — reads existing `Program`/`ProgramDay`/`Tier`/`TierExercise` models as they exist today; does not require `adv-01`'s schema changes (this module only computes and returns a string; consumers store it).

## Verification
- `pytest tests/test_program_hash.py -q` — new tests, including the canonicalization guard and the two edge cases above.
- `pytest -q` overall stays green.
