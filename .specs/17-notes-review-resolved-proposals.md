# Spec 17: Surface resolved proposals on GET /notes/review

## Objective
Wire the resolver (spec 16) into the existing `/notes/review` endpoint so the client can pre-fill its apply wizard instead of showing blank pickers. `NoteReviewOut` gains a `resolved_proposals` field; `get_notes_review` computes it fresh per request (not cached — program state can change between note creation and review).

## File targets
- Modify: `ironlog/api/app.py` (`NoteReviewOut`, `get_notes_review`)
- New/modify test(s) under `tests/`: an endpoint-level test (find this repo's existing pattern for testing `/notes/review`, likely `tests/test_notes_review*.py` or similar — check first) asserting a CONFIG_CHANGE note with a resolvable subject movement returns a non-empty `resolved_proposals` list on the response, and a note with `action_type=OTHER` (or unresolvable subject) returns an empty list, not an error.

## The fix
1. **`NoteReviewOut`** (`ironlog/api/app.py:344`): add `resolved_proposals: List[ProposalOut] = []`, where `ProposalOut` is a new Pydantic model mirroring spec 16's `ResolvedProposal` dataclass field-for-field (read `ironlog/notes/resolver.py` once spec 16 is merged to confirm the exact field names before writing this — do not guess ahead of the merged code). Place `ProposalOut`'s class definition near `NoteReviewOut`, following this file's existing convention for where response-shape helper models live.
2. **`get_notes_review`**: for each note in the result set with `classification in (CONFIG_CHANGE, PROGRAMMING_REQUEST)` and `action_type` not `None`/`OTHER`, call `resolve_note(note, db)` (from spec 16) and attach the result as `resolved_proposals`. For notes where resolution isn't applicable (`OTHER`, or `proposed_change is None`), `resolved_proposals` stays an empty list — do not call the resolver needlessly for notes it can't do anything with.

## Edge cases
- **This endpoint must not get meaningfully slower for a large notes-review inbox** — the resolver does a bounded number of DB reads per note (candidate slots, per-candidate override computation), not an unbounded scan; if the resolver's own per-note cost turns out non-trivial (N+1 query patterns etc.), that's worth noting in the PR but is not blocking for this spec (optimize later if it's actually a real problem, not preemptively).
- **A resolver exception for one note must not break the whole endpoint** — wrap the per-note `resolve_note` call so a single note's resolution failure (e.g. malformed `proposed_change` data from an old note predating spec 13) degrades to an empty `resolved_proposals` list for THAT note, not a 500 for the whole `/notes/review` response. Log the failure rather than silently swallowing it (check this file's existing logging convention, if any).

## Dependencies
Depends on spec 16 (`.specs/16-note-resolver.md`) merging first — needs `resolve_note`/`ResolvedProposal` to exist. No schema change of its own, no HUMAN GATE for this spec specifically (pure API-response-shape addition, additive field with a default).

## Verification
- New test(s) described above, green.
- Full server suite green: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'`.
- Manual: after deploy, `curl http://localhost:8000/notes/review` on a real CONFIG_CHANGE note shows a populated `resolved_proposals` array matching expectations.
