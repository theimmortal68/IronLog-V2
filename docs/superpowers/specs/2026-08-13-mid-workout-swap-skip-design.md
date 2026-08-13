# Mid-Workout Exercise Swap/Skip — Design

## Problem

Mid-workout, the athlete sometimes hits a blocker on a specific exercise — a
piece of equipment is unavailable (e.g. the Matrix Machine Sissy Squat part
that's still on order), an attachment conflict on a shared machine (e.g. the
Apex can't hold two attachments at once), or the exercise just needs to be
skipped for the day. Today, the only way to handle this is to ask Tier A to
SSH into the server and hand-edit the program or the live session. This
design adds a first-class in-app way to swap or skip an exercise, live,
during a session.

## Scope

- **Swap**: replace the movement filling an exercise's remaining (not yet
  logged) sets, either for today's session only or as a permanent program
  change, chosen at the moment of the swap.
- **Skip**: mark an exercise's remaining (not yet logged) sets as
  intentionally skipped, so the session isn't blocked waiting on them.
- Applies to both the Android client (IronLog-V2-Client) and the server
  (IronLog-V2) that backs it.
- Out of scope: swapping/skipping individual sets within an exercise (this
  operates at exercise granularity — "the rest of this exercise," not "just
  this one set"); offline queuing of swap/skip actions (these require live
  connectivity, same as any other in-the-moment gym decision).

## Data Model Changes (additive migrations only)

- `PlannedSet.is_skipped: bool = False` — marks a not-yet-logged set as
  intentionally skipped. No `SetLog` row is ever written for a skipped set.
  Session-completion logic (client-side "is everything done" check) treats a
  skipped set the same as a logged one: accounted for, not blocking.
- `PlannedExercise.tier_exercise_id: Optional[int] = Field(default=None,
  foreign_key="tierexercise.id")` — the program slot that generated this
  exercise. Currently this link exists only in-memory during generation
  (`assembler._build_exercise` receives `tier_exercise_id` as a parameter but
  never persists it) and is discarded once the session is built. Persisting
  it is required so a "make permanent" swap can find the right
  `TierExercise` to attach a `SlotMovementOverride` to. Nullable — historic
  sessions predating this migration simply can't offer the "make permanent"
  path (the "today only" path doesn't need it).

## API Endpoints (server)

### `POST /sessions/{session_id}/exercises/{exercise_id}/skip`

Marks every not-yet-logged `PlannedSet` under that `PlannedExercise` as
`is_skipped=True`. Idempotent — calling it again on an already-fully-skipped
exercise is a no-op. Already-logged sets are untouched. Returns the updated
`ExerciseOut` (same shape `/sessions/{id}` already returns for one exercise)
so the client can patch its local session state without a full re-fetch.

### `POST /sessions/{session_id}/exercises/{exercise_id}/swap`

Body: `{new_movement_id: int, make_permanent: bool}`

1. Validate `exercise_id` belongs to `session_id` and `new_movement_id`
   refers to an ACTIVE movement.
2. For every not-yet-logged, not-skipped `PlannedSet` under this
   `PlannedExercise`: recompute `target_load` / `target_reps_low` /
   `target_reps_high` / HT fields (plates/band_config, if applicable) using
   the new movement's own day-scoped `MovementState` and the *existing*
   slot's `rep_low`/`rep_high`/`scheme` (pulled via `tier_exercise_id`, or —
   if `tier_exercise_id` is null on a legacy session — the exercise's
   current `PlannedSet` rep targets, unchanged). This reuses the same
   per-movement prescription logic `assembler._build_exercise` already uses
   for normal generation, scoped down to one exercise's remaining sets.
   Needs-calibration resolves to `target_load=None`, same as any other
   needs-cal movement.
3. Update `PlannedExercise.movement_id` to `new_movement_id`.
4. If `make_permanent` and `tier_exercise_id` is set: write an
   `OverrideType.MOVEMENT` `SlotMovementOverride` row for that
   `tier_exercise_id` (the same mechanism `lay_skeleton` already honors for
   note-driven movement swaps) — no new override machinery needed. If
   `make_permanent` is requested but `tier_exercise_id` is null (legacy
   session), return a 409 with a clear message rather than silently
   dropping the "permanent" half of the request.
5. Returns the updated `ExerciseOut`.

Already-logged `SetLog` rows are untouched and remain correctly attributed
to whichever movement was active when they were logged (`SetLog.movement_id`
is captured at log time, independent of the exercise's current
`movement_id`) — no special handling needed.

### `GET /movements/substitutes/{movement_id}`

Returns ACTIVE movements sharing `primary_muscle` with the given movement,
excluding itself. Powers the "suggested substitutes" list in the swap
picker. The client falls back to the existing `/movements` list + local
search for "browse the full library" when nothing suggested fits.

## Client UX (IronLog-V2-Client)

- Each exercise name in `CaptureScreen.kt` (both giant-set and straight-tier
  rendering) gets a small overflow icon (⋮) — new affordance, nothing
  currently occupies that space.
- Tapping it opens a menu: **Swap exercise** / **Skip remaining sets**. The
  menu (and the swap/skip actions themselves) only appears when the exercise
  has at least one not-yet-logged, not-yet-skipped set remaining — nothing
  to act on otherwise.
- **Skip** → confirmation dialog ("Skip remaining sets of {movement}? Sets
  you've already logged stay logged.") → Confirm calls the skip endpoint,
  patches local session state from the response, remaining sets for that
  exercise render as a "Skipped" row instead of input fields, and the
  capture cursor (`CaptureViewModel`'s "what's the current set" logic) skips
  over `is_skipped` sets when advancing.
- **Swap** → bottom sheet: suggested substitutes (from
  `/movements/substitutes/{id}`) as tappable rows, plus a search field
  querying the full movement library. Picking one shows a second small
  confirmation: "Today only" vs "Update program going forward" (radio
  choice) → calls the swap endpoint, patches local session state, remaining
  sets for that exercise now show the new movement's name and (if
  calibrated) its own load.
- Giant-set rendering (the round-major fix already shipped) needs no
  structural change — it iterates `group.exercises` and per-round
  `planned_sets`; a shrunk rotation is just fewer/renamed entries for
  remaining rounds. Already-rendered/logged rounds for a skipped or
  swapped-out exercise stay as historical rows, unaffected.

## Edge Cases

- An exercise with nothing left to act on (fully logged, fully skipped, or a
  mix covering every set) doesn't show the swap/skip menu.
- Historical accuracy is automatic: `SetLog.movement_id` is captured at log
  time, so sets logged before a swap stay correctly attributed to the
  original movement even after `PlannedExercise.movement_id` changes.
- Swap works uniformly across progression types (plain load, needs-cal, HT
  plates/bands, assisted) — it reuses the same per-movement prescription
  logic the generator uses elsewhere rather than reimplementing it per type.
- "Make permanent" on a legacy (pre-migration) session with no
  `tier_exercise_id` returns a 409 rather than silently only applying the
  today-only half of the change.

## Testing

- Server: unit tests for both endpoints covering — skip marks only
  not-yet-logged sets, skip is idempotent, swap recomputes remaining sets
  correctly for a plain-load movement and for an HT movement, swap leaves
  already-logged `SetLog` rows' `movement_id` untouched, "make permanent"
  writes a `SlotMovementOverride` that a subsequent `lay_skeleton` call
  honors, "make permanent" on a null-`tier_exercise_id` session 409s.
  Migration tests for both new columns (additive, existing rows default
  correctly).
- Client: unit tests for the capture cursor skipping `is_skipped` sets, and
  for the giant-set rendering continuing to iterate correctly with a
  shrunk `group.exercises` list. Manual smoke test on a live giant set:
  skip one member mid-round, confirm the remaining members keep rotating
  correctly for subsequent rounds.
