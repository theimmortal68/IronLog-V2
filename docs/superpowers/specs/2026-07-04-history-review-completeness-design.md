# History Review Completeness — Design

**Date:** 2026-07-04
**Repos:** server `~/projects/IronLog-V2` (FastAPI/SQLModel — additive read only) + client `~/projects/IronLog-V2-Client` (Kotlin/Compose).
**Status:** Approved design → spec for implementation planning.

## Goal

Make a completed session fully reviewable in the client's History detail. The in-gym capture loop now records per-set actuals (load, reps, `feedback_tap`, `rpe_numeric`, `felt_peak`), per-exercise `ExerciseSurvey` (asymmetry/technique flags + sticking_point), and `Note`s (session-level + per-exercise). But `HistoryDetailScreen` renders only `"load×reps (warmup)"` via `GET /sessions/{id}/logs`, which exposes none of the richer data. This chunk surfaces all of it — the review side of the loop whose capture side just shipped.

## Scope

| IN | OUT (deferred) |
|---|---|
| Extend `GET /sessions/{id}/logs`: add `rpe_numeric`+`felt_peak` to each set; add `surveys[]` + `notes[]` arrays | Any new schema/migration/engine logic (pure additive read) |
| Client History detail renders: per-set RPE + tap + felt-peak; per-exercise asymmetry/technique flag badges; session note + per-exercise notes | Editing/deleting past logs, surveys, or notes from History |
| Server pytest + client logic tests | Note re-classification / confirm UI (`Note.classification` is still `JOURNAL`; not consumed here) |
| | History **list** changes (`HistoryScreen` stays date + day_role) |

`sticking_point` is included in the survey DTO for completeness (it rides along) but is always `null` today, so the client does not render it this chunk.

## Server (additive — extend the existing endpoint)

No new endpoint, no schema, no migration. In `ironlog/api/app.py`:

- **`LoggedSet`** (response model) gains: `rpe_numeric: Optional[float] = None`, `felt_peak: Optional[float] = None`. Populate from `SetLog.rpe_numeric` / `SetLog.felt_peak` in `get_session_logs`.
- **New response models:**
  - `SurveyOut`: `movement_id: int`, `movement_name: str`, `asymmetry_flag: Optional[bool]`, `technique_flag: Optional[bool]`, `sticking_point: Optional[str]`.
  - `NoteOut`: `movement_id: Optional[int]`, `text: str`.
- **`LoggedSetsResponse`** gains: `surveys: List[SurveyOut] = []`, `notes: List[NoteOut] = []`.
- **`get_session_logs`** adds two queries alongside the existing SetLog query (same pattern): select `ExerciseSurvey` where `session_id == :id` (join movement name), and `Note` where `session_id == :id` (keep null `movement_id` for the session note). Build the two arrays. 404 behavior unchanged.

Existing clients that ignore the new fields are unaffected (additive response).

## Client (`~/projects/IronLog-V2-Client`)

**DTOs** (`data/api/dto/GenerateModels.kt` — where `LoggedSet`/`LoggedSetsResponse` live):
- `LoggedSet` gains `rpe_numeric: Double? = null`, `felt_peak: Double? = null` (defaulted → cache/back-compat).
- New `SurveyOut(movement_id: Int, movement_name: String, asymmetry_flag: Boolean? = null, technique_flag: Boolean? = null, sticking_point: String? = null)`.
- New `NoteOut(movement_id: Int? = null, text: String)`.
- `LoggedSetsResponse` gains `surveys: List<SurveyOut> = emptyList()`, `notes: List<NoteOut> = emptyList()`.

**Rendering** (`ui/screens/history/HistoryDetailScreen.kt`; grouping helper in `HistoryViewModel.kt`):
- **Session note:** the `notes` entry with `movement_id == null` renders below the header (`"date · day_role"`). If absent, nothing.
- **Per-set line** (`SetRow` / `setSummary`): `load×reps` + `@{rpe}` when `rpe_numeric != null` + a tap indicator + `peak~{felt_peak}` when `felt_peak != null`. Warmup tag unchanged. Tap indicator mapping (pure function): `ON_TARGET → "✓"`, `TOO_EASY → "↓ easy"`, `TOO_HARD → "↑ hard"`, null → omitted.
- **MovementCard:** its header shows flag badges — `⚠ L/R` if the movement's `SurveyOut.asymmetry_flag == true`, `⚠ tech` if `technique_flag == true` (nothing if false/absent). Per-exercise notes (the `notes` entries whose `movement_id` equals this movement) render under the card's sets.
- Match survey/notes to movements by `movement_id`. Reuse the existing `groupLogsByMovement`; add small pure helpers to (a) find a movement's survey, (b) collect a movement's notes, (c) extract the session note, (d) format the set line, (e) map the tap — each unit-testable.

Repo (`GenerateRepo`/`CaptureRepo` — whichever exposes the logs call) and `HistoryDetailViewModel` need no logic change: same endpoint, richer body.

## Data flow

Completed session → `GET /sessions/{id}/logs` (now returns `logs` + `surveys` + `notes`) → `HistoryDetailViewModel` → `HistoryDetailScreen`: header, session note, then one `MovementCard` per movement with enriched set lines + flag badges + per-exercise notes.

## Error handling

Unchanged: 404 on missing session (server), `ErrorRetryBox` on failure + `CircularProgressIndicator` on load (client, already present). Empty arrays render nothing (no session note / no badges / no notes). Missing DTO fields default (older cached responses / a set with no RPE) — no crash.

## Testing

**Server (pytest, `~/projects/IronLog-V2`, run via `ssh myflix`):**
- `get_session_logs` returns `rpe_numeric` + `felt_peak` on sets that have them (and null when absent).
- Returns `surveys` with correct flags for a session that has an `ExerciseSurvey`, and `notes` split into the session note (`movement_id` null) + per-exercise notes.
- Empty `surveys`/`notes` when the session has none.
- 404 unchanged for a missing session.
- Full existing suite stays green (additive change).

**Client (pure-logic unit tests, Robolectric where a DTO is involved):**
- Set-line formatting across combinations: RPE present/absent, felt-peak present/absent, each tap value, warmup.
- Flag-badge decision (asymmetry/technique true → badge; false/absent → none).
- Note partition: session note (null movement) vs per-exercise notes grouped by movement_id.
- DTO back-compat: a response JSON missing `surveys`/`notes`/`rpe_numeric`/`felt_peak` deserializes with defaults (no crash).
- `assembleDebug` build green.

## Build order (SDD, server-first)

1. **Server:** add response fields + `SurveyOut`/`NoteOut` + the two queries in `get_session_logs` + pytest. (Claude Code Agent subagent applies+tests; codex is read-only.)
2. **Client DTOs:** `LoggedSet` fields + `SurveyOut`/`NoteOut` + `LoggedSetsResponse` arrays, field-for-field with the server.
3. **Client render:** enriched set line + flag badges + notes sections + the pure helpers and their unit tests + build.

## Global constraints

- Server: additive read only — NO schema/migration/engine change; `from __future__ import annotations` NOT used (project rule); existing pytest suite stays green; response is purely additive (no field renamed/removed).
- Client: no new Gradle dependency; `SERVER_BASE_URL` stays local-uncommitted; new DTO fields defaulted for back-compat; History **list** screen untouched.
- History is read-only — this chunk displays; it never writes or edits past data.
