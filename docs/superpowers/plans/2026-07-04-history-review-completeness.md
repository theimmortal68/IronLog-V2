# History Review Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a completed session fully reviewable in History — per-set RPE/tap/felt-peak, per-exercise asymmetry/technique flags, and notes (session + per-exercise).

**Architecture:** Additive server read (extend `GET /sessions/{id}/logs` with two set fields + `surveys[]`/`notes[]` — no schema/migration/engine change), then client DTO mirror + enriched `HistoryDetailScreen` render driven by small pure helpers. Server-first so the client builds against the real response shape.

**Tech Stack:** Server — Python/FastAPI/SQLModel, pytest (run via `ssh myflix`). Client — Kotlin/Compose, kotlinx.serialization, JUnit4/Robolectric. Two repos.

**Spec:** `~/projects/IronLog-V2/docs/superpowers/specs/2026-07-04-history-review-completeness-design.md` (commit 5a5dbfd).

## Global Constraints

- **Server:** additive read only — NO schema/migration/engine change; do NOT use `from __future__ import annotations`; the full existing pytest suite must stay green; the response is purely additive (no field renamed or removed). Server tests run remote: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'`.
- **Client:** no new Gradle dependency; `SERVER_BASE_URL` in `app/build.gradle.kts` stays local-uncommitted (never commit that file); new DTO fields must be defaulted (kotlinx back-compat with older cached responses); the History **list** screen (`HistoryScreen`) is untouched. Client build on the workstation: `./gradlew :app:assembleDebug`; unit tests `./gradlew :app:testDebugUnitTest`.
- History is **read-only** — this chunk only displays; it never writes or edits past data.
- `sticking_point` rides along in the survey DTO but is always `null` today and is NOT rendered this chunk.
- Match survey/notes to movements by `movement_id`; the session note is the note with `movement_id == null`.

---

## File Structure

**Server (`~/projects/IronLog-V2`):**
- `ironlog/api/app.py` — MODIFY: extend `LoggedSet`, add `SurveyOut`/`NoteOut`, extend `LoggedSetsResponse`, extend `get_session_logs`.
- `tests/test_session_logs.py` — MODIFY: update the exact-key assertion; add coverage for rpe/felt-peak/surveys/notes.

**Client (`~/projects/IronLog-V2-Client`):**
- `app/src/main/java/com/jauschua/ironlogv2/data/api/dto/GenerateModels.kt` — MODIFY: `LoggedSet` fields, add `SurveyOut`/`NoteOut`, extend `LoggedSetsResponse`.
- `app/src/main/java/com/jauschua/ironlogv2/ui/screens/history/HistoryDetailLogic.kt` — CREATE: pure render helpers.
- `app/src/main/java/com/jauschua/ironlogv2/ui/screens/history/HistoryDetailScreen.kt` — MODIFY: render session note, flag badges, enriched set lines, per-exercise notes.
- `app/src/test/java/com/jauschua/ironlogv2/ui/history/HistoryDetailLogicTest.kt` — CREATE.
- `app/src/test/java/com/jauschua/ironlogv2/data/dto/HistoryDtoBackCompatTest.kt` — CREATE.

---

### Task 1: Server — extend `/sessions/{id}/logs` (additive)

**Files:**
- Modify: `ironlog/api/app.py` (`LoggedSet`, new `SurveyOut`/`NoteOut`, `LoggedSetsResponse`, `get_session_logs`)
- Modify: `tests/test_session_logs.py`

**Interfaces:**
- Produces response JSON: each `logs[]` item gains `rpe_numeric: float|null`, `felt_peak: float|null`; response gains `surveys: [{movement_id, movement_name, asymmetry_flag, technique_flag, sticking_point}]` and `notes: [{movement_id (nullable), text}]`. The client (Task 2) mirrors these names verbatim.

- [ ] **Step 1: Update + extend the failing test**

In `tests/test_session_logs.py`: (a) extend `_make_completed_session_with_logs` to also write RPE + felt_peak + a survey + two notes; (b) update the exact-key assertion; (c) add assertions for the new data. Replace the body from `_make_completed_session_with_logs` through `test_session_logs_returns_actuals` with:

```python
def _make_completed_session_with_logs(engine):
    """A COMPLETED session with a tapped working SetLog (Bench 165x8, ON_TARGET, RPE 8,
    felt_peak 250), one ExerciseSurvey (asymmetry flagged), a session note, and a
    per-exercise note — exercises every field the logs endpoint now returns."""
    from ironlog.models.session import ExerciseSurvey, Note
    with DbSession(engine) as s:
        mv = Movement(name="Bench Press [PB]", base_name="Bench Press")
        s.add(mv); s.commit(); s.refresh(mv)

        ws = WorkoutSession(date=date(2026, 7, 1), day_role="D1 Upper Push",
                            phase="P1", status=SessionStatus.COMPLETED)
        s.add(ws); s.commit(); s.refresh(ws)

        s.add(SetLog(session_id=ws.id, movement_id=mv.id, set_index=0,
                     actual_load=165.0, actual_reps=8, rpe_numeric=8.0, felt_peak=250.0,
                     feedback_tap=FeedbackTap.ON_TARGET, is_warmup=False))
        s.add(ExerciseSurvey(session_id=ws.id, movement_id=mv.id,
                             asymmetry_flag=True, technique_flag=False))
        s.add(Note(session_id=ws.id, movement_id=None, text="felt strong"))
        s.add(Note(session_id=ws.id, movement_id=mv.id, text="right side lagging"))
        s.commit()
        return ws.id, mv.id


def test_session_logs_returns_actuals():
    client, engine = _client()
    sid, mid = _make_completed_session_with_logs(engine)
    resp = client.get(f"/sessions/{sid}/logs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == sid
    assert len(body["logs"]) >= 1
    first = body["logs"][0]
    assert set(first.keys()) == {
        "movement_id", "movement_name", "set_index", "reps", "load", "tap",
        "is_warmup", "rpe_numeric", "felt_peak"}
    assert first["load"] == 165.0 and first["reps"] == 8
    assert first["tap"] == "ON_TARGET"
    assert first["rpe_numeric"] == 8.0
    assert first["felt_peak"] == 250.0
    app.dependency_overrides.clear()


def test_session_logs_returns_surveys_and_notes():
    client, engine = _client()
    sid, mid = _make_completed_session_with_logs(engine)
    body = client.get(f"/sessions/{sid}/logs").json()

    assert len(body["surveys"]) == 1
    sv = body["surveys"][0]
    assert set(sv.keys()) == {
        "movement_id", "movement_name", "asymmetry_flag", "technique_flag", "sticking_point"}
    assert sv["movement_id"] == mid
    assert sv["movement_name"]                    # joined from Movement
    assert sv["asymmetry_flag"] is True
    assert sv["technique_flag"] is False

    notes = body["notes"]
    assert {n["text"] for n in notes} == {"felt strong", "right side lagging"}
    session_note = [n for n in notes if n["movement_id"] is None]
    per_ex = [n for n in notes if n["movement_id"] == mid]
    assert len(session_note) == 1 and session_note[0]["text"] == "felt strong"
    assert len(per_ex) == 1 and per_ex[0]["text"] == "right side lagging"
    app.dependency_overrides.clear()


def test_session_logs_empty_surveys_and_notes_when_none():
    client, engine = _client()
    with DbSession(engine) as s:
        ws = WorkoutSession(date=date(2026, 7, 2), day_role="D2 Lower A",
                            phase="P1", status=SessionStatus.COMPLETED)
        s.add(ws); s.commit(); s.refresh(ws)
        sid = ws.id
    body = client.get(f"/sessions/{sid}/logs").json()
    assert body["surveys"] == []
    assert body["notes"] == []
    app.dependency_overrides.clear()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_session_logs.py'`
Expected: FAIL — `rpe_numeric`/`felt_peak` keys missing; `body["surveys"]`/`["notes"]` KeyError.

- [ ] **Step 3: Extend the response models**

In `ironlog/api/app.py`, replace the `LoggedSet` + `LoggedSetsResponse` class block with:

```python
class LoggedSet(BaseModel):
    movement_id: int
    movement_name: str
    set_index: int
    reps: Optional[int] = None
    load: Optional[float] = None
    tap: Optional[str] = None
    is_warmup: bool
    rpe_numeric: Optional[float] = None
    felt_peak: Optional[float] = None


class SurveyOut(BaseModel):
    movement_id: int
    movement_name: str
    asymmetry_flag: Optional[bool] = None
    technique_flag: Optional[bool] = None
    sticking_point: Optional[str] = None


class NoteOut(BaseModel):
    movement_id: Optional[int] = None
    text: str


class LoggedSetsResponse(BaseModel):
    session_id: int
    date: str
    day_role: str
    logs: List[LoggedSet]
    surveys: List[SurveyOut] = []
    notes: List[NoteOut] = []
```

- [ ] **Step 4: Extend the handler**

In `get_session_logs`, (a) populate the two new set fields, (b) query surveys + notes. Update the import line and the body. Replace the handler with:

```python
@app.get("/sessions/{session_id}/logs", response_model=LoggedSetsResponse)
def get_session_logs(session_id: int, db: Session = Depends(get_session)):
    """Logged actuals (SetLogs) + per-exercise surveys + notes for a session.
    Client groups sets by movement and matches surveys/notes by movement_id."""
    from ..models.session import (
        Session as WorkoutSession, SetLog, ExerciseSurvey, Note)
    ws = db.get(WorkoutSession, session_id)
    if ws is None:
        raise HTTPException(404, "session not found")

    rows = db.exec(
        select(SetLog).where(SetLog.session_id == session_id).order_by(SetLog.id)
    ).all()
    logs = []
    for sl in rows:
        mv = db.get(Movement, sl.movement_id)
        logs.append(LoggedSet(
            movement_id=sl.movement_id, movement_name=(mv.name if mv else ""),
            set_index=sl.set_index, reps=sl.actual_reps, load=sl.actual_load,
            tap=(sl.feedback_tap.value if sl.feedback_tap else None),
            is_warmup=sl.is_warmup,
            rpe_numeric=sl.rpe_numeric, felt_peak=sl.felt_peak,
        ))

    survey_rows = db.exec(
        select(ExerciseSurvey).where(ExerciseSurvey.session_id == session_id)
        .order_by(ExerciseSurvey.id)
    ).all()
    surveys = []
    for sv in survey_rows:
        mv = db.get(Movement, sv.movement_id)
        surveys.append(SurveyOut(
            movement_id=sv.movement_id, movement_name=(mv.name if mv else ""),
            asymmetry_flag=sv.asymmetry_flag, technique_flag=sv.technique_flag,
            sticking_point=sv.sticking_point,
        ))

    note_rows = db.exec(
        select(Note).where(Note.session_id == session_id).order_by(Note.id)
    ).all()
    notes = [NoteOut(movement_id=n.movement_id, text=n.text) for n in note_rows]

    return LoggedSetsResponse(
        session_id=session_id, date=ws.date.isoformat(), day_role=ws.day_role,
        logs=logs, surveys=surveys, notes=notes)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_session_logs.py'`
Expected: PASS (4 tests). Then the full suite: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'` — all green (additive change; only `test_session_logs.py` needed updating).

- [ ] **Step 6: Commit**

```bash
cd ~/projects/IronLog-V2 && git add ironlog/api/app.py tests/test_session_logs.py
git commit -m "feat(api): logs endpoint returns rpe/felt-peak per set + surveys + notes"
```

---

### Task 2: Client — DTOs + pure render helpers (+ tests)

**Files:**
- Modify: `app/src/main/java/com/jauschua/ironlogv2/data/api/dto/GenerateModels.kt`
- Create: `app/src/main/java/com/jauschua/ironlogv2/ui/screens/history/HistoryDetailLogic.kt`
- Test: `app/src/test/java/com/jauschua/ironlogv2/ui/history/HistoryDetailLogicTest.kt`
- Test: `app/src/test/java/com/jauschua/ironlogv2/data/dto/HistoryDtoBackCompatTest.kt`

**Interfaces:**
- Consumes: the Task 1 server response (field names verbatim).
- Produces (used by Task 3 rendering):
  - `LoggedSet` gains `rpe_numeric: Double? = null`, `felt_peak: Double? = null`.
  - `data class SurveyOut(movement_id, movement_name, asymmetry_flag: Boolean? = null, technique_flag: Boolean? = null, sticking_point: String? = null)`
  - `data class NoteOut(movement_id: Int? = null, text: String)`
  - `LoggedSetsResponse` gains `surveys: List<SurveyOut> = emptyList()`, `notes: List<NoteOut> = emptyList()`.
  - Pure helpers in `HistoryDetailLogic.kt`: `formatSetLine(LoggedSet): String`, `tapIndicator(String?): String?`, `surveyFor(List<SurveyOut>, Int): SurveyOut?`, `notesFor(List<NoteOut>, Int): List<NoteOut>`, `sessionNoteText(List<NoteOut>): String?`, `flagBadges(SurveyOut?): List<String>`.

- [ ] **Step 1: Write the failing tests**

Create `app/src/test/java/com/jauschua/ironlogv2/ui/history/HistoryDetailLogicTest.kt`:

```kotlin
package com.jauschua.ironlogv2.ui.history

import com.jauschua.ironlogv2.data.api.dto.LoggedSet
import com.jauschua.ironlogv2.data.api.dto.NoteOut
import com.jauschua.ironlogv2.data.api.dto.SurveyOut
import com.jauschua.ironlogv2.ui.screens.history.flagBadges
import com.jauschua.ironlogv2.ui.screens.history.formatSetLine
import com.jauschua.ironlogv2.ui.screens.history.notesFor
import com.jauschua.ironlogv2.ui.screens.history.sessionNoteText
import com.jauschua.ironlogv2.ui.screens.history.surveyFor
import com.jauschua.ironlogv2.ui.screens.history.tapIndicator
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class HistoryDetailLogicTest {
    private fun set(
        load: Double? = 165.0, reps: Int? = 8, tap: String? = "ON_TARGET",
        rpe: Double? = null, peak: Double? = null, warmup: Boolean = false,
    ) = LoggedSet(movement_id = 1, movement_name = "Bench", set_index = 0,
        reps = reps, load = load, tap = tap, is_warmup = warmup,
        rpe_numeric = rpe, felt_peak = peak)

    @Test fun tap_indicator_maps_each_value() {
        assertEquals("✓", tapIndicator("ON_TARGET"))
        assertEquals("↓ easy", tapIndicator("TOO_EASY"))
        assertEquals("↑ hard", tapIndicator("TOO_HARD"))
        assertNull(tapIndicator(null))
    }

    @Test fun format_set_line_load_reps_only() {
        assertEquals("165×8 ✓", formatSetLine(set()))
    }

    @Test fun format_set_line_drops_trailing_zero_and_adds_rpe_and_peak() {
        assertEquals("205×8 @8 ✓ peak~250", formatSetLine(set(load = 205.0, rpe = 8.0, peak = 250.0)))
    }

    @Test fun format_set_line_missing_fields_render_dashes_and_omit_absent() {
        // no tap, no rpe, no peak → "—×—" with nothing appended
        assertEquals("—×—", formatSetLine(set(load = null, reps = null, tap = null)))
    }

    @Test fun format_set_line_warmup_suffix() {
        assertEquals("135×5 ✓ (warmup)", formatSetLine(set(load = 135.0, reps = 5, warmup = true)))
    }

    @Test fun survey_for_matches_by_movement_id() {
        val surveys = listOf(SurveyOut(movement_id = 1, movement_name = "Bench", asymmetry_flag = true),
                             SurveyOut(movement_id = 2, movement_name = "Row"))
        assertEquals(1, surveyFor(surveys, 1)?.movement_id)
        assertNull(surveyFor(surveys, 99))
    }

    @Test fun flag_badges_from_survey() {
        assertEquals(listOf("⚠ L/R", "⚠ tech"),
            flagBadges(SurveyOut(1, "Bench", asymmetry_flag = true, technique_flag = true)))
        assertEquals(listOf("⚠ L/R"),
            flagBadges(SurveyOut(1, "Bench", asymmetry_flag = true, technique_flag = false)))
        assertEquals(emptyList<String>(), flagBadges(SurveyOut(1, "Bench")))
        assertEquals(emptyList<String>(), flagBadges(null))
    }

    @Test fun notes_partition_session_vs_movement() {
        val notes = listOf(NoteOut(movement_id = null, text = "day"),
                           NoteOut(movement_id = 1, text = "bench note"),
                           NoteOut(movement_id = 2, text = "row note"))
        assertEquals("day", sessionNoteText(notes))
        assertEquals(listOf("bench note"), notesFor(notes, 1).map { it.text })
        assertNull(sessionNoteText(listOf(NoteOut(movement_id = 1, text = "x"))))
    }
}
```

Create `app/src/test/java/com/jauschua/ironlogv2/data/dto/HistoryDtoBackCompatTest.kt`:

```kotlin
package com.jauschua.ironlogv2.data.dto

import com.jauschua.ironlogv2.data.api.dto.LoggedSetsResponse
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class HistoryDtoBackCompatTest {
    private val json = Json { ignoreUnknownKeys = true }

    @Test fun old_response_without_new_fields_deserializes_with_defaults() {
        // A pre-upgrade cached body: no surveys/notes, no rpe_numeric/felt_peak.
        val body = """
            {"session_id":7,"date":"2026-07-01","day_role":"D1 Upper Push",
             "logs":[{"movement_id":1,"movement_name":"Bench","set_index":0,
                      "reps":8,"load":165.0,"tap":"ON_TARGET","is_warmup":false}]}
        """.trimIndent()
        val resp = json.decodeFromString<LoggedSetsResponse>(body)
        assertTrue(resp.surveys.isEmpty())
        assertTrue(resp.notes.isEmpty())
        assertNull(resp.logs[0].rpe_numeric)
        assertNull(resp.logs[0].felt_peak)
        assertEquals(165.0, resp.logs[0].load!!, 0.001)
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/projects/IronLog-V2-Client && ./gradlew :app:testDebugUnitTest --tests "*HistoryDetailLogicTest*" --tests "*HistoryDtoBackCompatTest*"`
Expected: COMPILE FAILURE — the new DTO fields/classes and the helper functions don't exist yet.

- [ ] **Step 3: Extend the DTOs**

In `GenerateModels.kt`, replace the `LoggedSet` + `LoggedSetsResponse` declarations with:

```kotlin
@Serializable data class LoggedSet(
    val movement_id: Int, val movement_name: String, val set_index: Int,
    val reps: Int? = null, val load: Double? = null, val tap: String? = null,
    val is_warmup: Boolean,
    val rpe_numeric: Double? = null, val felt_peak: Double? = null,
)
@Serializable data class SurveyOut(
    val movement_id: Int, val movement_name: String,
    val asymmetry_flag: Boolean? = null, val technique_flag: Boolean? = null,
    val sticking_point: String? = null,
)
@Serializable data class NoteOut(val movement_id: Int? = null, val text: String)
@Serializable data class LoggedSetsResponse(
    val session_id: Int, val date: String, val day_role: String,
    val logs: List<LoggedSet>,
    val surveys: List<SurveyOut> = emptyList(),
    val notes: List<NoteOut> = emptyList(),
)
```

- [ ] **Step 4: Create the pure helpers**

Create `app/src/main/java/com/jauschua/ironlogv2/ui/screens/history/HistoryDetailLogic.kt`:

```kotlin
package com.jauschua.ironlogv2.ui.screens.history

import com.jauschua.ironlogv2.data.api.dto.LoggedSet
import com.jauschua.ironlogv2.data.api.dto.NoteOut
import com.jauschua.ironlogv2.data.api.dto.SurveyOut

/** Drop a trailing ".0" so 165.0 → "165" but 162.5 → "162.5". */
private fun fmtNum(v: Double): String =
    if (v == v.toLong().toDouble()) v.toLong().toString() else v.toString()

/** Short glyph for a feedback tap; null when there's no tap. */
fun tapIndicator(tap: String?): String? = when (tap) {
    "ON_TARGET" -> "✓"
    "TOO_EASY" -> "↓ easy"
    "TOO_HARD" -> "↑ hard"
    else -> null
}

/**
 * One-line set summary: "load×reps" + optional " @rpe", tap glyph, " peak~felt", "(warmup)".
 * Absent load/reps render as "—"; absent rpe/peak/tap are omitted.
 */
fun formatSetLine(set: LoggedSet): String {
    val load = set.load?.let(::fmtNum) ?: "—"
    val reps = set.reps?.toString() ?: "—"
    val parts = mutableListOf("$load×$reps")
    set.rpe_numeric?.let { parts.add("@${fmtNum(it)}") }
    tapIndicator(set.tap)?.let { parts.add(it) }
    set.felt_peak?.let { parts.add("peak~${fmtNum(it)}") }
    if (set.is_warmup) parts.add("(warmup)")
    return parts.joinToString(" ")
}

/** The survey for a movement, or null. */
fun surveyFor(surveys: List<SurveyOut>, movementId: Int): SurveyOut? =
    surveys.firstOrNull { it.movement_id == movementId }

/** Notes attached to a specific movement (excludes the session note). */
fun notesFor(notes: List<NoteOut>, movementId: Int): List<NoteOut> =
    notes.filter { it.movement_id == movementId }

/** The session-level note text (movement_id == null), or null. */
fun sessionNoteText(notes: List<NoteOut>): String? =
    notes.firstOrNull { it.movement_id == null }?.text

/** Flag badge labels for a movement's survey. */
fun flagBadges(survey: SurveyOut?): List<String> {
    if (survey == null) return emptyList()
    val out = mutableListOf<String>()
    if (survey.asymmetry_flag == true) out.add("⚠ L/R")
    if (survey.technique_flag == true) out.add("⚠ tech")
    return out
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./gradlew :app:testDebugUnitTest --tests "*HistoryDetailLogicTest*" --tests "*HistoryDtoBackCompatTest*"`
Expected: PASS (all). The existing `setSummary` in `HistoryDetailScreen.kt` is unused after Task 3 but still compiles now; leave it for Task 3 to replace.

- [ ] **Step 6: Commit**

```bash
cd ~/projects/IronLog-V2-Client
git add app/src/main/java/com/jauschua/ironlogv2/data/api/dto/GenerateModels.kt \
        app/src/main/java/com/jauschua/ironlogv2/ui/screens/history/HistoryDetailLogic.kt \
        app/src/test/java/com/jauschua/ironlogv2/ui/history/HistoryDetailLogicTest.kt \
        app/src/test/java/com/jauschua/ironlogv2/data/dto/HistoryDtoBackCompatTest.kt
git commit -m "feat(history): rich logs DTOs + pure render helpers"
```

---

### Task 3: Client — enriched `HistoryDetailScreen` render

**Files:**
- Modify: `app/src/main/java/com/jauschua/ironlogv2/ui/screens/history/HistoryDetailScreen.kt`

**Interfaces:**
- Consumes: Task 2 helpers (`formatSetLine`, `surveyFor`, `notesFor`, `sessionNoteText`, `flagBadges`), `LoggedSetsResponse.surveys/notes`, existing `groupLogsByMovement`/`MovementLogs`.
- Produces: no new exported symbol (screen rendering only). Compile/build is the gate.

- [ ] **Step 1: Rewrite the render body**

In `HistoryDetailScreen.kt`, replace `DetailBody`, `MovementCard`, `SetRow`, and the private `setSummary` with the following (keep the top `HistoryDetailScreen` composable, imports, and the `groupLogsByMovement` call). Add imports as needed (`androidx.compose.foundation.layout.Row`, `androidx.compose.foundation.layout.Arrangement`, `androidx.compose.material3.*` symbols already largely present).

```kotlin
@Composable
private fun DetailBody(session: LoggedSetsResponse) {
    val groups = groupLogsByMovement(session.logs)
    val sessionNote = sessionNoteText(session.notes)
    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item(key = "header") {
            Text(
                text = "${session.date} · ${session.day_role}",
                style = MaterialTheme.typography.titleLarge,
            )
        }
        if (sessionNote != null) {
            item(key = "session-note") {
                Text(
                    text = "“$sessionNote”",
                    style = MaterialTheme.typography.bodyMedium,
                    fontStyle = androidx.compose.ui.text.font.FontStyle.Italic,
                )
            }
        }
        if (groups.isEmpty()) {
            item(key = "empty") { Text("No logged sets.") }
        }
        items(groups, key = { it.movementId }) { group ->
            MovementCard(
                group = group,
                survey = surveyFor(session.surveys, group.movementId),
                notes = notesFor(session.notes, group.movementId),
            )
        }
    }
}

@Composable
private fun MovementCard(
    group: MovementLogs,
    survey: com.jauschua.ironlogv2.data.api.dto.SurveyOut?,
    notes: List<com.jauschua.ironlogv2.data.api.dto.NoteOut>,
) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(
            modifier = Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(group.movementName, style = MaterialTheme.typography.titleMedium)
                flagBadges(survey).forEach { badge ->
                    Text(
                        text = badge,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            }
            group.sets.forEach { set ->
                Text(formatSetLine(set), style = MaterialTheme.typography.bodyMedium)
            }
            notes.forEach { note ->
                Text(
                    text = "“${note.text}”",
                    style = MaterialTheme.typography.bodySmall,
                    fontStyle = androidx.compose.ui.text.font.FontStyle.Italic,
                )
            }
        }
    }
}
```

Remove the now-unused `SetRow` and `setSummary` functions and the unused `LoggedSet` import if it becomes unused (keep it if still referenced). Ensure `Row`/`Arrangement`/`Alignment` are imported.

- [ ] **Step 2: Build**

Run: `cd ~/projects/IronLog-V2-Client && ./gradlew :app:assembleDebug`
Expected: BUILD SUCCESSFUL. (Rendering has no unit test; the pure logic is covered in Task 2, the build is the compile gate.)

- [ ] **Step 3: Run the full unit suite (no regression)**

Run: `./gradlew :app:testDebugUnitTest`
Expected: all green (Task 2 tests + existing suite).

- [ ] **Step 4: Commit**

```bash
git add app/src/main/java/com/jauschua/ironlogv2/ui/screens/history/HistoryDetailScreen.kt
git commit -m "feat(history): render RPE/tap/felt-peak, flag badges, session + exercise notes"
```

---

## On-device smoke (deferred — phone off-network)

When reachable (`adb -s 192.168.1.17:<port> install -r app/build/outputs/apk/debug/app-debug.apk`): open a completed session in History → confirm set lines show RPE/tap/felt-peak, a movement with a flagged survey shows the ⚠ badges, the session note shows below the header, and per-exercise notes show under their movement.

## Routing Plan

| Task | Repo | Deliverable | Route |
|---|---|---|---|
| Task 1 | server | additive `/logs` fields + surveys/notes + pytest | Claude Code Agent subagent (codex read-only → subagent applies+tests via `ssh myflix`) |
| Task 2 | client | DTOs + pure helpers + unit tests | Claude Code Agent subagent (workstation) |
| Task 3 | client | HistoryDetailScreen render + build | Claude Code Agent subagent (workstation) |

**Delegation ratio: 3/3 tasks delegated (100%).** Tier A writes no implementation code — dispatches a fresh implementer per task, runs the two-verdict review gate between tasks, and the final whole-branch review. Consensus workers unused (Python server via subagent because codex can't apply/test; Kotlin client via subagent).

## Self-Review

**Spec coverage:** rpe/felt-peak on sets → Task 1 model+handler, Task 2 DTO, Task 3 render ✓. surveys[]/notes[] additive read → Task 1 ✓. session note (null movement) below header → Task 2 `sessionNoteText`, Task 3 render ✓. per-exercise flag badges → Task 2 `flagBadges`, Task 3 ✓. per-exercise notes under movement → Task 2 `notesFor`, Task 3 ✓. tap indicator mapping → Task 2 `tapIndicator` ✓. DTO back-compat defaults → Task 2 test ✓. No schema/migration/engine; additive response; existing suite green (Task 1 step 5); History list untouched; sticking_point null, not rendered ✓.

**Placeholder scan:** the `<port>` in the install command is an environment value (varies), intentionally deferred to install time; no build/test step depends on it. All code steps carry complete code; no TBD/TODO.

**Type consistency:** DTO field names (`rpe_numeric`, `felt_peak`, `asymmetry_flag`, `technique_flag`, `sticking_point`, `movement_id`, `text`) match verbatim between the server models (Task 1) and the Kotlin `@Serializable` DTOs (Task 2). Helper names (`formatSetLine`, `tapIndicator`, `surveyFor`, `notesFor`, `sessionNoteText`, `flagBadges`) are consistent between the Task 2 test imports, the Task 2 definitions, and the Task 3 render calls. `SurveyOut`/`NoteOut`/`MovementLogs` shapes align across tasks.
