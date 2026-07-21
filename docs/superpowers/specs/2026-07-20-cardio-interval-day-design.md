# Standalone Cardio/Interval Day-Type — Design

## Problem

Third and final of the three remaining Phase-1 roadmap items (real weak-point assessment shipped 2026-07-19; missed-workout handling shipped 2026-07-20). The app has no way to log Z2 steady-state cardio sessions (neighborhood walks, treadmill sessions on a Bells of Steel Dreadmill) at all today. The original schema design (`docs/05_session_setlog_schema.md`) anticipated this — "Z2 cardio gets a lightweight log (duration, avg HR from TICKR, incline, backward-walk done)" — but it was never built.

## Scope decisions (from brainstorming)

- **Log-only, no generation.** No `/generate` involvement, no `MovementState`, no progression engine. The athlete does their Z2 session and logs it afterward — no target-setting or adaptive prescription.
- **Fully standalone data model, not tied to `ProgramDay`/`day_role`.** Despite the athlete's real-world rhythm being ~2x/week (roughly the existing empty `ProgramDay` slots at `day_index` 3 and 7, both already `is_rest=true`), the log itself is keyed only by date — no day_index/day_role concept. This avoids `day_role`-uniqueness assumptions elsewhere in the codebase (e.g. `select(ProgramDay).where(ProgramDay.day_role == day_role).first()`) and keeps the feature decoupled from the generation/scheduling machinery entirely.
- **Not tracked by missed-workout-handling.** A skipped cardio session should not generate a `MissedDayRecord`. This falls out for free: `check_missed_days()` already filters `ProgramDay.is_rest == False`, and day_index 3/7 are already `is_rest=true` — no code change needed there, and since the cardio log itself never touches `ProgramDay` at all, there's nothing to wire in regardless.
- **Fields**: `date`, `duration_minutes`, `avg_hr` (optional — TICKR sync doesn't always happen), `modality` (`WALK` | `TREADMILL`), `incline_pct` (optional, treadmill-only), `backward_walk_done` (bool, treadmill-only).
- **Weekly rollup surfaced on the Today screen** ("🏃 Cardio: 1/2 this week"), against a fixed target of 2/week (matching the athlete's stated real rhythm — not athlete-configurable; hardcode for now, revisit if that assumption changes). Uses the same Monday-start week-boundary convention already established by missed-workout-handling's `_current_week_start`, for consistency — a Sunday log counts toward the week that's ending, not the new one starting.
- **History**: a simple list of past entries (date, duration, modality), reusing this repo's existing `history` screen pattern client-side rather than inventing a new one.
- **No interval timer involvement.** This is Z2 steady-state, not interval work — the existing `IntervalTimerService` stays scoped to finishers exactly as it is today; nothing here reuses it.
- **Multiple logs per day are allowed** — no uniqueness constraint (a short walk and a treadmill session on the same day, or correcting a past entry by adding a new one, are both legitimate).

## Components

### 1. `CardioLog` model (server)

New standalone SQLModel table, `ironlog/models/library.py` (or a new small module if that file is getting large — decide at spec-writing time by checking its current size):

```python
class CardioLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    date: date
    duration_minutes: int
    avg_hr: Optional[int] = None
    modality: str  # "WALK" | "TREADMILL"
    incline_pct: Optional[float] = None
    backward_walk_done: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

Additive-only migration (single `CREATE TABLE`, no seed data — matches this repo's established migration convention).

### 2. Endpoints (server)

- `POST /cardio-log` — create one entry from the request body, return it.
- `GET /cardio-log` — list recent entries (for history), most-recent-first.
- `GET /cardio-log/weekly-summary` — count of entries in the current Mon–Sun week (server-side `date.today()`), plus the fixed target (2), so the client doesn't need to duplicate week-boundary math. Response shape: `{"count": int, "target": 2, "week_start": date}`.

### 3. Client

- **Today screen**: a small rollup line ("🏃 Cardio: 1/2 this week"), tappable to open the log entry form. Sourced from `GET /cardio-log/weekly-summary`.
- **Log entry form**: date (default today, editable for backfilling), duration (minutes), avg HR (optional), modality toggle (Walk / Treadmill — incline and backward-walk-done fields only visible/editable when Treadmill is selected). Submits to `POST /cardio-log`.
- **History**: a simple list (date, duration, modality) via `GET /cardio-log`, using the existing `history` screen pattern.

## Edge Cases

- `incline_pct`/`backward_walk_done` are meaningless for `WALK` modality — the client simply hides those fields rather than the server validating/rejecting a value sent alongside `WALK` (defensive validation isn't needed since this is a single-user app and the client controls what it sends).
- Weekly-summary week-boundary: reuse `_current_week_start`'s exact Monday-start logic (mirror, don't reinvent) so a Sunday log counts toward the ending week, not the new one.
- No athlete-facing error states beyond standard field validation (duration must be positive, etc.) — this is a simple log, not a safety-critical prescription path.

## Deploy Classification

Class 2 (schema/data migration) for the new `CardioLog` table, same HUMAN GATE treatment as every other new table this session. Review-exempt at the code-review layer (additive-only schema, no novel logic, no invariant touched) — same treatment as prior additive tables (e.g. `GoalSettings`, `WithingsCredentials`).

## Out of Scope

- Athlete-configurable weekly target (hardcoded to 2 for now).
- Missed-cardio detection/tracking (explicitly declined).
- Generated/adaptive cardio prescriptions (explicitly declined — log-only).
- Interval-timer integration (this is steady-state Z2, not interval work).
