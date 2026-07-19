# Missed-Workout Handling — Design

## Problem

Second of the three remaining Phase-1 roadmap items (real weak-point assessment already shipped 2026-07-19; a standalone cardio/interval day-type designed separately, no dependency between the two). The app has no concept of a missed training day today — `/generate` is entirely client-driven (the client explicitly passes `day_role`), and `ProgramDay.day_index` (1=Mon...7=Sun) exists in the schema but is not read anywhere in the live generation path, only by one-off seed scripts.

## Scope decisions (from brainstorming)

- **Training rhythm is fixed weekly** (confirmed with the user): each `day_role` is meant to happen on roughly the same weekday most weeks, so `ProgramDay.day_index` is a legitimate anchor for "missed" — this feature becomes the first real consumer of that field.
- **"Missed" means the biggest of three considered options**: detect a gap AND let the athlete explicitly acknowledge or reschedule a specific missed `day_role` — not just passive detection, and not an automatic generation-time adjustment (e.g. auto-softening the next session).
- **Detection timing: end of the day's own calendar date, with a grace window** (e.g. until 6am the next day) — not lazily computed only when a later day is generated. Requires a background timer, not pure on-demand computation (the user explicitly chose to keep the nightly-timer + stored-record approach over a simpler read-time-only alternative Tier A proposed).
- **Makeup scope: "do it today" only** — no date picker, no explicit future-date targeting. Rescheduling just means "recommend this missed day_role the next time I ask what to train."
- **No change to `/generate`'s signature or logic** — this feature is purely additive. A rescheduled missed day surfaces as a recommendation via a new endpoint; the client (which already knows how to call `/generate` with whatever `day_role` it's told) naturally requests that. No new server-side "what should I train right now" resolver is introduced.

## Components

### 1. Nightly detection timer

New `ironlog-missed-days.timer`/`.service` pair (mirrors the Withings integration's `ironlog-withings-sync.timer` pattern — a thin script wrapper + systemd timer/service, installed the same way). Runs once daily. For each non-rest `ProgramDay`:
- Resolve its calendar date for the current week (Monday of the current week + `day_index - 1` days).
- If that date has fully elapsed (now is past that date's end plus a grace window, e.g. 6am the following day) and no `Session` exists matching that `day_role` within that calendar week, AND no `MissedDayRecord` already exists for this `program_day_id`+`week_start_date` (avoid duplicate detection on subsequent timer runs), insert a new `MissedDayRecord`.
- Also re-check existing `PENDING`/`RESCHEDULED` records: if a matching `Session` now exists (logged after detection, regardless of acknowledge/reschedule status), flip the record to `RESOLVED` — see Component 3.

### 2. `MissedDayRecord` model + migration

```python
class MissedDayRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    program_day_id: int = Field(foreign_key="programday.id")
    week_start_date: date       # Monday of the missed week
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    status: str = "PENDING"     # PENDING | ACKNOWLEDGED | RESCHEDULED | RESOLVED
    resolved_at: Optional[datetime] = None
```
Append-only-ish history table (like `MovementWeaknessSignal` — not a singleton), one row per missed instance. Migration is additive-only, single `CREATE TABLE`.

### 3. Auto-resolution

If a `Session` matching the missed `day_role` gets logged at any point after a `MissedDayRecord` was created — whether or not the athlete explicitly acknowledged or rescheduled it — the record flips to `RESOLVED`. The athlete trained it; the record's job is done regardless of which path got them there. Exact mechanism (checked by the nightly timer on its next run, vs. checked synchronously at session-submit time) decided at spec-writing time — either is acceptable, but if checked only nightly, a same-day resolution won't show as resolved until the next timer run, which is fine given this is a "was I behind schedule" signal, not a real-time notification.

### 4. Endpoints

- `GET /missed-days` — returns current `PENDING`/`RESCHEDULED` records (not `ACKNOWLEDGED`/`RESOLVED` — those are settled, no longer need surfacing).
- `POST /missed-days/{id}/acknowledge` — sets `status="ACKNOWLEDGED"`. Dismissal, no further effect.
- `POST /missed-days/{id}/reschedule` — sets `status="RESCHEDULED"`. No date field. This just marks the record so the client can display it as a recommended next session — the athlete still calls `/generate` themselves with that `day_role` when ready, same as always.

## Data flow

```
[nightly timer] ──▶ for each non-rest ProgramDay this week:
                       day fully elapsed + grace window elapsed
                       AND no matching Session
                       AND no existing MissedDayRecord for this (program_day, week)
                            │
                            ▼
                     INSERT MissedDayRecord(status=PENDING)

[nightly timer, same run] ──▶ for each PENDING/RESCHEDULED record:
                                 matching Session now exists?
                                      │
                                      ▼
                                 UPDATE status=RESOLVED

GET /missed-days ──▶ PENDING/RESCHEDULED records
POST .../acknowledge ──▶ status=ACKNOWLEDGED
POST .../reschedule ──▶ status=RESCHEDULED (client shows as "recommended next")
```

## Testing approach

- Pure/integration tests for the detection logic: a day fully elapsed with no session → `MissedDayRecord` created; a day not yet elapsed → no record; a day within the grace window → no record yet; a day already past the grace window with an existing session → no record (correctly not flagged); running detection twice doesn't create a duplicate record for the same `(program_day_id, week_start_date)`.
- Auto-resolution test: a `PENDING` record with a matching session now logged → flips to `RESOLVED`.
- Endpoint tests: `GET /missed-days` excludes `ACKNOWLEDGED`/`RESOLVED`; `acknowledge`/`reschedule` correctly mutate status; acting on an already-resolved or nonexistent record returns a clear error, not a silent no-op or crash.

## Explicitly out of scope

- Any UI — server-first, same pattern as every other feature this session.
- Date-picker rescheduling to a specific future date — "do it today" only, per the brainstorming decision.
- Any automatic generation-time adjustment (e.g. softening the next session after a long gap) — explicitly not chosen; this is detection + explicit athlete action only.
- Changing `/generate`'s signature or introducing a server-side "what to train right now" resolver — the recommendation surfaces via `GET /missed-days`, the client still drives `/generate` explicitly.
