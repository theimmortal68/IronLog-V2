# Spec 16: The note resolver — deterministic candidate-finding + pre-validated proposals

## Objective
New module `ironlog/notes/resolver.py`: given an already-classified `CONFIG_CHANGE` note (from `NoteClassifier`, spec 13's `REORDER` extension included), deterministically find candidate slot(s) matching the note's subject movement, compute the concrete override each candidate implies, reject/flag any candidate that would violate a safety clamp, and return a ranked list of pre-validated proposals — no new LLM judgment, this is the "AI resolves the note" step that lets the client show a ready-to-confirm proposal instead of blank pickers.

## Background — the design (approved via brainstorming session 2026-07-12)
This is the core new piece of "H — AI acts on programming notes." The governing principle from that session: **nothing auto-applies** — the resolver's output is always a *proposal* for one-tap human confirmation, never a committed change. The resolver's job is entirely deterministic code operating on data the classifier already extracted (`proposed_change.movement`, `action_type`, and for REORDER, `before_movement`/`after_movement`) plus program state already in the DB — it does not call any LLM itself.

**Depends on spec 13 (classifier REORDER extension) and spec 14 (REORDER override type) both merging first** — this module's candidate/override computation needs both the classifier's structured REORDER fields and the `OverrideType.REORDER`/`override_order` model fields to exist.

## The fix

### Data shape
```python
@dataclass
class ResolvedProposal:
    tier_exercise_id: int
    day_role: str                     # for display — which day this candidate is on
    slot_label: str                   # e.g. "GS1" — for display
    override_type: str                # "MOVEMENT" | "LOAD" | "REPS" | "REORDER"
    # exactly the fields apply_override()/ApplyNoteRequest already accept, populated per type:
    override_movement_id: Optional[int] = None
    load_delta: Optional[float] = None
    load_absolute: Optional[float] = None
    rep_low: Optional[int] = None
    rep_high: Optional[int] = None
    override_order: Optional[float] = None
    valid: bool = True                # False if this candidate would violate a safety check
    validation_note: Optional[str] = None  # human-readable reason when valid=False
    summary: str = ""                 # human-readable one-line description of the resulting change
```
(Exact field names/shapes are a starting point — adjust if something doesn't fit cleanly against the real `apply_override`/`ApplyNoteRequest` shapes, but keep the SAME field names those already use wherever possible so the API layer, spec 17, can pass this through with minimal translation.)

### 1. Finding candidates
```python
def find_candidate_slots(subject_movement_name: str, db: Session) -> List[TierExercise]:
```
Resolve `subject_movement_name` (from `proposed_change.movement`) to a `Movement` row (read `ironlog/notes/apply.py`'s existing movement-name resolution, if any, for the exact matching convention — e.g. exact name match vs. fuzzy — do not invent a new matching strategy if one already exists in this codebase). Find every `TierExercise` whose *effective* movement matches — that means the base `movement_id` OR an active `MOVEMENT`-type `SlotMovementOverride`'s `override_movement_id` for that slot (read `ironlog/generation/skeleton.py`'s existing MOVEMENT-override resolution to mirror the exact "effective movement" computation, do not re-derive it independently).

### 2. Ranking candidates
```python
def rank_candidates(candidates: List[TierExercise], note: Note, db: Session) -> List[TierExercise]:
```
Best guess first. Heuristic (tune as needed, this is not required to be perfect): prefer the candidate whose day matches the note's own `session_id`'s `day_role` (the attachment) if that day is among the candidates; otherwise prefer the day-track whose `MovementState` row (day-scoped, per the recently-fixed `(movement_id, day_id)` composite key — see spec 12, already merged) has the most recent `confirmed_at`. Document the heuristic clearly in a docstring since it's the kind of thing a future session may want to revisit.

### 3. Computing the concrete override, per action_type
- **SWAP** → `OverrideType.MOVEMENT`: resolve `proposed_change.movement`'s replacement target (whatever the note says to swap TO) to a `Movement` row → `override_movement_id`. **Note: there is no existing server-side name-to-movement resolution to reuse for the swap TARGET** — `resolve_slot` (`ironlog/notes/apply.py`) resolves the SOURCE slot by attachment (a different problem, and deliberately not what this resolver uses either — see step 1). Match by exact (case-insensitive) `Movement.name`/`Movement.base_name`, and if nothing matches, that candidate is `valid=False` with `validation_note` explaining the swap target couldn't be resolved — do not guess a fuzzy match for a movement swap target.
- **LOAD_INCREASE/LOAD_DECREASE** → `OverrideType.LOAD`: derive a numeric `load_delta` (signed, positive for INCREASE, negative for DECREASE) from `proposed_change.params`. **Correction from an earlier draft of this spec: there is NO existing numeric-parsing code to reuse here** — today, a human always types the number by hand in the client wizard; the classifier's `params` field is free text (e.g. "+10", "10 lbs heavier"), never previously parsed programmatically anywhere in this codebase. Write a small, honest best-effort extractor (e.g. a regex for a signed number: `re.search(r'[-+]?\d+\.?\d*', params)`) and if it finds nothing parseable, that candidate is `valid=False` with `validation_note` explaining no numeric value could be extracted — **never fabricate a default delta**. This is a real, acknowledged limitation of this spec (a future spec could extend the classifier itself to extract a structured numeric field, mirroring how spec 13 did this for REORDER's before/after fields — flag this as a good follow-on in your commit message, but do not attempt it as part of this spec).
- **REP_CHANGE** → `OverrideType.REPS`: derive `rep_low`/`rep_high` from `proposed_change.params` via the same honest best-effort regex approach (e.g. a pattern like `\d+\s*-\s*\d+` for a range, or a single number for both bounds) — same rule: unparseable → `valid=False` with an explanatory note, never a fabricated guess.
- **REORDER** → `OverrideType.REORDER`: resolve `before_movement`/`after_movement` (from spec 13) to their own candidate `TierExercise` rows within the SAME tier as the target slot, read their `exercise_order` (or effective order, if either neighbor itself has an active REORDER override — mirror spec 15's effective-order computation), and compute `override_order` strictly between them (e.g. the midpoint). If only one neighbor is specified (see spec 13's edge case), place the slot immediately before/after that neighbor using a reasonable offset (e.g. `neighbor_order - 0.5` or `+ 0.5`) — document this choice in a comment, it's a judgment call.

### 4. Pre-commit safety check
Before including a computed override in the returned list, check whether it would violate a known hard constraint — **at minimum, the HT bottom-position clamp** (`ironlog/engine/validator.py`'s `_check_ht_safety`, `ctx.ht_bottom_clamp`) for any LOAD-type change touching an HT-composite movement, since that's the exact near-miss this design exists to prevent. Read `_check_ht_safety`, `_check_load_below_floor`, and `_check_load_over_cap` in full — they operate on a full `Session`/`ValidationContext`, which is heavier machinery than a single computed override needs. **Prefer a narrower, bespoke comparison against the same underlying thresholds** (`ctx.ht_bottom_clamp`, `movement.load_floor`, `movement.cap` — read how these values are sourced in the validator and reuse the SAME lookup, not a re-derived one) rather than fabricating a synthetic `Session`/`ValidationContext` to run the full checks, UNLESS you find fabricating a minimal synthetic session is actually straightforward given existing test fixtures (check `tests/` for any existing helper that builds a minimal validation-context fixture before deciding). If a computed override would violate a threshold, set `valid=False` and `validation_note` to a human-readable explanation — do NOT drop the candidate from the list entirely (the client should still show it, flagged, rather than silently hiding that a note couldn't be safely auto-resolved).

### 5. Top-level entry point
```python
def resolve_note(note: Note, db: Session) -> List[ResolvedProposal]:
```
Ties the above together: only meaningful for `action_type in {SWAP, LOAD_INCREASE, LOAD_DECREASE, REP_CHANGE, REORDER}` (not `OTHER`) — return an empty list for `OTHER` or if `proposed_change` is `None`. Find candidates, rank them, compute + validate an override per candidate, return the ranked list.

## File targets
- New: `ironlog/notes/resolver.py`
- New tests: `tests/test_note_resolver.py` (or match this repo's existing test-file naming convention for the `notes/` module — check `tests/` for an existing `test_note_*` file first) covering: (a) a SWAP note resolves to the correct candidate movement/slot; (b) a LOAD note touching a NON-clamp-bounded movement, with a cleanly-parseable numeric delta in `params`, resolves to a `valid=True` proposal; (c) **the HT clamp near-miss scenario from this session, reconstructed as a fixture** — a LOAD note that would push an HT movement's bottom position over `ctx.ht_bottom_clamp` must resolve to `valid=False` with a clear `validation_note`, NOT silently included as a green-light proposal; (d) a REORDER note with both neighbors specified computes an `override_order` strictly between them; (e) a movement present on multiple day-tracks produces multiple ranked candidates, with the ranking heuristic's stated preference actually winning; (f) a LOAD/REPS note whose `params` has no parseable number resolves to `valid=False` with an explanatory note, not a fabricated delta.

## Edge cases
- **`action_type == OTHER`** (or `proposed_change is None`) → empty proposal list, not an error.
- **Zero candidate slots found** (subject movement doesn't match any current program slot) → empty list, not an error — the client falls back to the existing manual picker.
- **A REORDER note whose named neighbor(s) don't resolve to any slot** in the same tier as the target → do not fabricate a guess; return either an empty list or a single `valid=False` candidate with an explanatory note (implementer's call, document the choice).
- **This module must not mutate anything** — it's read-only; no `db.add`/`commit` anywhere. Creating the actual override remains `apply_override`'s job, called only after human confirmation via the existing `/notes/{note_id}/apply` endpoint.

## Dependencies
Depends on spec 13 (`.specs/13-classifier-reorder-action-type.md`) and spec 14 (`.specs/14-reorder-override-type.md`) both merging first. Spec 15 (`lay_skeleton` honoring REORDER) is helpful context but not a hard blocking dependency for this module's own logic (it can compute a valid `override_order` without `lay_skeleton` yet consuming it — though the FEATURE isn't end-to-end complete until 15 also merges). No schema change of its own, no HUMAN GATE for this spec specifically.

## Verification
- New test(s) described above, green — especially the reconstructed HT-clamp-near-miss fixture, which is the single most important test in this whole spec.
- Full server suite green: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'`.
