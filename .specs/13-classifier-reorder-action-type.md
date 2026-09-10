# Spec 13: Add a REORDER action_type + structured before/after params to NoteClassifier

## Objective
`NoteClassifier` (`ironlog/notes/classify.py`) currently has 5 `action_type` values (`SWAP`, `LOAD_INCREASE`, `LOAD_DECREASE`, `REP_CHANGE`, `OTHER`) and no dedicated category for "move this exercise's position in the day's sequence" requests — the one real example seen (D4: "move knee raises between meadows rows and single arm rows") was classified `OTHER` with an unstructured `params` string. Add a `REORDER` action_type with structured neighbor targets so the resolver (a separate, dependent spec) can compute a concrete slot-position change instead of parsing free text.

## Background
This is part of a larger design ("H — AI acts on programming notes," approved via brainstorming session 2026-07-12): the classifier's job stays exactly what it already does — classify + extract structured fields, no new judgment calls — the resolver (spec 16) does the deterministic slot/candidate resolution downstream. This spec only touches classification and extraction.

## The fix
1. **`NOTE_SYSTEM_INSTRUCTION`**: add a `REORDER` bullet to the action_type list, e.g.:
   ```
   - REORDER: change where this movement falls in the day's exercise sequence relative to
     OTHER named movements (e.g. "move knee raises between meadows rows and single arm rows").
   ```
   Also instruct the model: for `REORDER`, extract `before_movement` and `after_movement` as the two neighboring movement names the note describes (the movement should end up positioned between them) — `null` for either if the note only specifies one neighbor (e.g. "move X after Y" has an `after_movement` but no `before_movement`).
2. **`NOTE_CLASSIFICATION_SCHEMA`**: add `"REORDER"` to `action_type`'s enum. Add two new optional string fields to the `proposed_change` object schema: `before_movement` and `after_movement` (both `{"type": ["string", "null"]}`), populated only when `action_type == "REORDER"` (leave `null` for all other action types — do not repurpose the existing free-text `params` field for this).
3. **`_ACTION_TYPES`** (the Python-side validation set): add `"REORDER"`.
4. **`NoteClassification` dataclass**: no new fields needed here IF `proposed_change` (already a plain `dict`) simply carries `before_movement`/`after_movement` keys when present — confirm this is sufficient by reading how `proposed_change` is currently consumed downstream (`ironlog/notes/apply.py`, `ironlog/api/app.py`'s `NoteReviewOut.proposed_change: Optional[dict]`) before deciding whether the dataclass itself needs a new typed field or the dict is enough. Prefer NOT adding new dataclass fields if the existing `dict`-typed `proposed_change` already flows through untouched to all consumers — minimal diff.

## File targets
- Modify: `ironlog/notes/classify.py` (`NOTE_SYSTEM_INSTRUCTION`, `NOTE_CLASSIFICATION_SCHEMA`, `_ACTION_TYPES`)
- New/modify test(s): `tests/` — a test asserting the schema accepts `action_type="REORDER"` with `before_movement`/`after_movement` populated, and that the existing 5 action types are completely unaffected (no behavior change for SWAP/LOAD_INCREASE/LOAD_DECREASE/REP_CHANGE/OTHER). Since `NoteClassifier` calls a real Gemini API, mirror whatever mocking/fixture pattern this repo's existing classifier tests already use (find them first, e.g. `tests/test_note_classif*.py` or similar) — do not add a test that makes a real network call.

## Edge cases
- **A REORDER note with only one neighbor specified** ("move X after Y") must produce `after_movement` populated and `before_movement` null — the resolver (a later spec) is responsible for turning a single-neighbor request into a concrete position; this spec only needs to extract what the note actually said, not guess the missing neighbor.
- **Existing action types must see zero schema/behavior change** — this is purely additive to the schema and instruction text.
- **`proposed_change.movement`** (the subject) still gets populated for REORDER exactly as it already does for every other action_type — no special-casing needed there.

## Dependencies
None — standalone. No HUMAN GATE required (no schema/DB change, no auth/API-surface change to the public HTTP API — this only changes the internal `NoteClassifier` class and its Gemini prompt/schema).

## Verification
- New test(s) described above, green.
- Full server suite green: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'`.
