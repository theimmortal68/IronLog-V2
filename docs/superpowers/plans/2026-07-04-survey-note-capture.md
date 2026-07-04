# Survey + Note Capture UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add in-gym UI to capture per-exercise asymmetry/technique flags + free-text notes, closing the last curl-free gap in the session loop.

**Architecture:** Client-only (Kotlin/Compose, `~/projects/IronLog-V2-Client`, branch `feat/survey-note-capture` off client `main` 62d45ce). The Room drafts, `CaptureRepo.submit()` batching, and server persistence already exist — this adds DAO upsert/query methods, thin repo methods, a ViewModel trigger + save/skip/reopen, and a `ModalBottomSheet`. A group-review sheet auto-appears when the cursor advances past a group's last set; on Save it writes one `SurveyDraft` per exercise + an optional anchor-attached group note; a session-level note is written on Finish. No server changes; no new dependency.

**Tech Stack:** Kotlin, Jetpack Compose + Material3 (`ModalBottomSheet` already present), Room, Ktor client, kotlinx.serialization. Tests: JUnit4 + Robolectric + Room in-memory + Ktor `MockEngine` (existing style).

**Spec:** `~/projects/IronLog-V2/docs/superpowers/specs/2026-07-04-survey-note-capture-design.md` (server repo, commit 4bb735d).

**Build/verify (workstation):** `cd ~/projects/IronLog-V2-Client && ./gradlew :app:testDebugUnitTest` (unit) and `./gradlew :app:assembleDebug` (build). Install later when phone is on-network: `adb -s 192.168.1.17:<port> install -r app/build/outputs/apk/debug/app-debug.apk`.

## Global Constraints

- **Client only. No server changes. No new Gradle dependency.** `SERVER_BASE_URL` in `app/build.gradle.kts` stays local-uncommitted (do not commit that file).
- **Write-before-advance is untouched:** the survey trigger reads state only AFTER `repo.logSet` commits and the cursor advances; it never gates or delays the set write.
- **Surveys/notes are optional:** Skip is always available; nothing blocks Finish & Submit.
- **`sticking_point` is always written `null`** this chunk.
- **`SurveyDraft` unchecked toggle = `false`** on Save (an explicit "reviewed, fine"); **Skip writes nothing** (stays unanswered).
- **Idempotent re-edit:** saving a group review or session note deletes the prior rows for that scope first, then inserts — re-opening and re-saving never duplicates.
- Follow existing patterns: files under `app/src/main/java/com/jauschua/ironlogv2/`, tests under `app/src/test/java/com/jauschua/ironlogv2/`. Match the existing 4-space Kotlin style.
- **Every `CaptureDao` implementation must stay in sync:** `CaptureViewModelTest.kt` contains a hand-written `FakeGatedDao : CaptureDao`. Any new interface method must get an override there (and in any other fake) or the test file won't compile.

---

## File Structure

- `data/local/CaptureDao.kt` — MODIFY: add scoped delete + query methods (surveys by movement list; group note by movement; session note by NULL movement).
- `data/repo/CaptureRepo.kt` — MODIFY: add `saveGroupReview`, `reviewDraftsFor`, `saveSessionNote`; change `submit` is NOT needed (already batches).
- `ui/screens/capture/CaptureViewModel.kt` — MODIFY: `lastSetIdByGroup` precompute, `pendingReview` state, trigger in `logWorkingSet`, `openReview`/`dismissReview`/`saveReview`, `finish(sessionNote)`; add a `GroupReview` model + a pure `groupIsComplete` helper.
- `ui/screens/capture/GroupReviewSheet.kt` — CREATE: the `ModalBottomSheet` composable + a pure `initialFlags` mapper.
- `ui/screens/capture/CaptureScreen.kt` — MODIFY: observe `pendingReview` → show sheet; reopen affordance on completed groups; session-note field on Finish; pass note to `vm.finish`.
- Tests: `data/local/CaptureReviewDaoTest.kt` (new), `data/repo/CaptureRepoReviewTest.kt` (new), `ui/capture/CaptureViewModelReviewTest.kt` (new), `ui/capture/GroupReviewLogicTest.kt` (new). Plus update `FakeGatedDao` in `ui/capture/CaptureViewModelTest.kt`.

---

### Task 1: DAO scoped upsert/query methods

**Files:**
- Modify: `app/src/main/java/com/jauschua/ironlogv2/data/local/CaptureDao.kt`
- Modify (compile-fix): `app/src/test/java/com/jauschua/ironlogv2/ui/capture/CaptureViewModelTest.kt` (add overrides to `FakeGatedDao`)
- Test: `app/src/test/java/com/jauschua/ironlogv2/data/local/CaptureReviewDaoTest.kt`

**Interfaces:**
- Consumes: existing `SurveyDraft(sessionId, movementId, stickingPoint?, asymmetryFlag?, techniqueFlag?)`, `NoteDraft(sessionId, movementId?, text)`.
- Produces (new `CaptureDao` methods):
  - `suspend fun deleteSurveysForMovements(sessionId: Int, movementIds: List<Int>)`
  - `suspend fun surveysForMovements(sessionId: Int, movementIds: List<Int>): List<SurveyDraft>`
  - `suspend fun deleteNoteForMovement(sessionId: Int, movementId: Int)`
  - `suspend fun noteForMovement(sessionId: Int, movementId: Int): NoteDraft?`
  - `suspend fun deleteSessionNote(sessionId: Int)` — the `movementId IS NULL` note
  - `suspend fun sessionNote(sessionId: Int): NoteDraft?`

- [ ] **Step 1: Write the failing test**

Create `app/src/test/java/com/jauschua/ironlogv2/data/local/CaptureReviewDaoTest.kt`:

```kotlin
package com.jauschua.ironlogv2.data.local

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class CaptureReviewDaoTest {
    private lateinit var db: CaptureDatabase
    private lateinit var dao: CaptureDao

    @Before fun setup() {
        db = Room.inMemoryDatabaseBuilder(
            ApplicationProvider.getApplicationContext<Context>(), CaptureDatabase::class.java,
        ).allowMainThreadQueries().build()
        dao = db.captureDao()
    }

    @After fun teardown() = db.close()

    @Test fun deleteSurveysForMovements_only_touches_listed_movements() = runBlocking {
        dao.insertSurvey(SurveyDraft(sessionId = 7, movementId = 1, asymmetryFlag = true))
        dao.insertSurvey(SurveyDraft(sessionId = 7, movementId = 2, techniqueFlag = true))
        dao.insertSurvey(SurveyDraft(sessionId = 7, movementId = 3))
        dao.insertSurvey(SurveyDraft(sessionId = 9, movementId = 1))   // other session

        dao.deleteSurveysForMovements(7, listOf(1, 2))

        assertEquals(listOf(3), dao.surveysForSession(7).map { it.movementId })
        assertEquals(1, dao.surveysForSession(9).size)   // other session untouched
    }

    @Test fun surveysForMovements_filters_by_session_and_movement_list() = runBlocking {
        dao.insertSurvey(SurveyDraft(sessionId = 7, movementId = 1, asymmetryFlag = true))
        dao.insertSurvey(SurveyDraft(sessionId = 7, movementId = 5))
        val got = dao.surveysForMovements(7, listOf(1, 2, 3))
        assertEquals(listOf(1), got.map { it.movementId })
    }

    @Test fun group_note_and_session_note_are_independent() = runBlocking {
        dao.insertNote(NoteDraft(sessionId = 7, movementId = 4, text = "group note"))
        dao.insertNote(NoteDraft(sessionId = 7, movementId = null, text = "session note"))

        assertEquals("group note", dao.noteForMovement(7, 4)?.text)
        assertEquals("session note", dao.sessionNote(7)?.text)

        dao.deleteNoteForMovement(7, 4)
        assertNull(dao.noteForMovement(7, 4))
        assertEquals("session note", dao.sessionNote(7)?.text)   // session note survives

        dao.deleteSessionNote(7)
        assertNull(dao.sessionNote(7))
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/projects/IronLog-V2-Client && ./gradlew :app:testDebugUnitTest --tests "*CaptureReviewDaoTest*"`
Expected: COMPILE FAILURE — `deleteSurveysForMovements` / `surveysForMovements` / `noteForMovement` / `deleteNoteForMovement` / `sessionNote` / `deleteSessionNote` are unresolved.

- [ ] **Step 3: Add the DAO methods**

In `CaptureDao.kt`, add inside the `@Dao interface CaptureDao` body (keep existing methods):

```kotlin
    // ── Scoped upsert/query for the group-review sheet + session note ──────────────
    @Query("DELETE FROM survey_draft WHERE sessionId = :sessionId AND movementId IN (:movementIds)")
    suspend fun deleteSurveysForMovements(sessionId: Int, movementIds: List<Int>)

    @Query("SELECT * FROM survey_draft WHERE sessionId = :sessionId AND movementId IN (:movementIds) ORDER BY draftId")
    suspend fun surveysForMovements(sessionId: Int, movementIds: List<Int>): List<SurveyDraft>

    @Query("DELETE FROM note_draft WHERE sessionId = :sessionId AND movementId = :movementId")
    suspend fun deleteNoteForMovement(sessionId: Int, movementId: Int)

    @Query("SELECT * FROM note_draft WHERE sessionId = :sessionId AND movementId = :movementId ORDER BY draftId LIMIT 1")
    suspend fun noteForMovement(sessionId: Int, movementId: Int): NoteDraft?

    @Query("DELETE FROM note_draft WHERE sessionId = :sessionId AND movementId IS NULL")
    suspend fun deleteSessionNote(sessionId: Int)

    @Query("SELECT * FROM note_draft WHERE sessionId = :sessionId AND movementId IS NULL ORDER BY draftId LIMIT 1")
    suspend fun sessionNote(sessionId: Int): NoteDraft?
```

- [ ] **Step 4: Fix the FakeGatedDao compile break**

In `app/src/test/java/com/jauschua/ironlogv2/ui/capture/CaptureViewModelTest.kt`, add these overrides to `private class FakeGatedDao` (no-op / empty is fine — that class only exercises set-log gating):

```kotlin
    override suspend fun deleteSurveysForMovements(sessionId: Int, movementIds: List<Int>) {}
    override suspend fun surveysForMovements(sessionId: Int, movementIds: List<Int>): List<SurveyDraft> = emptyList()
    override suspend fun deleteNoteForMovement(sessionId: Int, movementId: Int) {}
    override suspend fun noteForMovement(sessionId: Int, movementId: Int): NoteDraft? = null
    override suspend fun deleteSessionNote(sessionId: Int) {}
    override suspend fun sessionNote(sessionId: Int): NoteDraft? = null
```

(Also scan the repo for any other `: CaptureDao` implementer with `grep -rn "CaptureDao" app/src/test` and add the same overrides if present.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `./gradlew :app:testDebugUnitTest --tests "*CaptureReviewDaoTest*"`
Expected: PASS (3 tests). Then run the whole capture package to confirm no regression: `./gradlew :app:testDebugUnitTest --tests "*capture*"` → all green.

- [ ] **Step 6: Commit**

```bash
git add app/src/main/java/com/jauschua/ironlogv2/data/local/CaptureDao.kt \
        app/src/test/java/com/jauschua/ironlogv2/data/local/CaptureReviewDaoTest.kt \
        app/src/test/java/com/jauschua/ironlogv2/ui/capture/CaptureViewModelTest.kt
git commit -m "feat(capture): scoped survey/note DAO upsert+query methods"
```

---

### Task 2: Repo review methods (idempotent replace + prefill + session note)

**Files:**
- Modify: `app/src/main/java/com/jauschua/ironlogv2/data/repo/CaptureRepo.kt`
- Test: `app/src/test/java/com/jauschua/ironlogv2/data/repo/CaptureRepoReviewTest.kt`

**Interfaces:**
- Consumes: Task 1 DAO methods; `SurveyDraft`, `NoteDraft`.
- Produces:
  - `data class ReviewPrefill(val surveys: List<SurveyDraft>, val noteText: String?)` (top-level in `CaptureRepo.kt`)
  - `suspend fun saveGroupReview(sessionId: Int, surveys: List<SurveyDraft>, anchorMovementId: Int, noteText: String?)`
  - `suspend fun reviewDraftsFor(sessionId: Int, movementIds: List<Int>, anchorMovementId: Int): ReviewPrefill`
  - `suspend fun saveSessionNote(sessionId: Int, text: String?)`

- [ ] **Step 1: Write the failing test**

Create `app/src/test/java/com/jauschua/ironlogv2/data/repo/CaptureRepoReviewTest.kt`:

```kotlin
package com.jauschua.ironlogv2.data.repo

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import com.jauschua.ironlogv2.data.api.ApiClient
import com.jauschua.ironlogv2.data.local.CaptureDatabase
import com.jauschua.ironlogv2.data.local.NoteDraft
import com.jauschua.ironlogv2.data.local.SurveyDraft
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.http.HttpStatusCode
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class CaptureRepoReviewTest {
    private fun repo(): Pair<CaptureRepo, CaptureDatabase> {
        val db = Room.inMemoryDatabaseBuilder(
            ApplicationProvider.getApplicationContext<Context>(), CaptureDatabase::class.java,
        ).allowMainThreadQueries().build()
        // No network call is made by the review methods; a never-called engine is fine.
        val engine = MockEngine { respond("", HttpStatusCode.OK) }
        return CaptureRepo(ApiClient(engine = engine), db.captureDao()) to db
    }

    @Test fun saveGroupReview_writes_one_survey_per_exercise_plus_anchor_note() = runBlocking {
        val (repo, db) = repo()
        val surveys = listOf(
            SurveyDraft(sessionId = 7, movementId = 10, asymmetryFlag = true, techniqueFlag = false),
            SurveyDraft(sessionId = 7, movementId = 11, asymmetryFlag = false, techniqueFlag = false),
        )
        repo.saveGroupReview(7, surveys, anchorMovementId = 10, noteText = "rotator felt off")

        val dao = db.captureDao()
        assertEquals(listOf(10, 11), dao.surveysForSession(7).map { it.movementId })
        assertEquals("rotator felt off", dao.noteForMovement(7, 10)?.text)
        db.close()
    }

    @Test fun saveGroupReview_is_idempotent_replace() = runBlocking {
        val (repo, db) = repo()
        repo.saveGroupReview(7,
            listOf(SurveyDraft(sessionId = 7, movementId = 10, asymmetryFlag = true)),
            anchorMovementId = 10, noteText = "first")
        // Re-save the SAME group with different values → replace, not duplicate.
        repo.saveGroupReview(7,
            listOf(SurveyDraft(sessionId = 7, movementId = 10, asymmetryFlag = false)),
            anchorMovementId = 10, noteText = null)

        val dao = db.captureDao()
        val surveys = dao.surveysForSession(7)
        assertEquals(1, surveys.size)
        assertEquals(false, surveys.single().asymmetryFlag)
        assertNull(dao.noteForMovement(7, 10))   // cleared note on re-save with blank
        db.close()
    }

    @Test fun reviewDraftsFor_returns_prefill() = runBlocking {
        val (repo, db) = repo()
        repo.saveGroupReview(7,
            listOf(SurveyDraft(sessionId = 7, movementId = 10, techniqueFlag = true)),
            anchorMovementId = 10, noteText = "note10")
        val prefill = repo.reviewDraftsFor(7, listOf(10, 11), anchorMovementId = 10)
        assertEquals(true, prefill.surveys.single { it.movementId == 10 }.techniqueFlag)
        assertEquals("note10", prefill.noteText)
        db.close()
    }

    @Test fun saveSessionNote_upserts_null_movement_note() = runBlocking {
        val (repo, db) = repo()
        repo.saveSessionNote(7, "felt strong")
        assertEquals("felt strong", db.captureDao().sessionNote(7)?.text)
        repo.saveSessionNote(7, "   ")               // blank → delete
        assertNull(db.captureDao().sessionNote(7))
        db.close()
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew :app:testDebugUnitTest --tests "*CaptureRepoReviewTest*"`
Expected: COMPILE FAILURE — `saveGroupReview` / `reviewDraftsFor` / `saveSessionNote` / `ReviewPrefill` unresolved.

- [ ] **Step 3: Add the repo methods**

In `CaptureRepo.kt`, add a top-level data class and the three methods inside the `CaptureRepo` class:

```kotlin
data class ReviewPrefill(val surveys: List<SurveyDraft>, val noteText: String?)
```
(add `import com.jauschua.ironlogv2.data.local.SurveyDraft` and `import com.jauschua.ironlogv2.data.local.NoteDraft` at the top.)

Inside `class CaptureRepo`:

```kotlin
    /**
     * Save one group's review: one SurveyDraft per exercise + an optional note anchored to the
     * group's first exercise. Idempotent — deletes the group's prior survey rows and the anchor's
     * prior note first, so re-opening and re-saving replaces rather than duplicates. Local only.
     */
    suspend fun saveGroupReview(
        sessionId: Int,
        surveys: List<SurveyDraft>,
        anchorMovementId: Int,
        noteText: String?,
    ) {
        val movementIds = surveys.map { it.movementId }
        dao.deleteSurveysForMovements(sessionId, movementIds)
        dao.deleteNoteForMovement(sessionId, anchorMovementId)
        surveys.forEach { dao.insertSurvey(it) }
        val trimmed = noteText?.trim().orEmpty()
        if (trimmed.isNotEmpty()) {
            dao.insertNote(NoteDraft(sessionId = sessionId, movementId = anchorMovementId, text = trimmed))
        }
    }

    /** Prefill for reopening a group's review sheet. */
    suspend fun reviewDraftsFor(
        sessionId: Int,
        movementIds: List<Int>,
        anchorMovementId: Int,
    ): ReviewPrefill = ReviewPrefill(
        surveys = dao.surveysForMovements(sessionId, movementIds),
        noteText = dao.noteForMovement(sessionId, anchorMovementId)?.text,
    )

    /** Upsert the session-level (movement_id = null) note; blank text clears it. Local only. */
    suspend fun saveSessionNote(sessionId: Int, text: String?) {
        dao.deleteSessionNote(sessionId)
        val trimmed = text?.trim().orEmpty()
        if (trimmed.isNotEmpty()) {
            dao.insertNote(NoteDraft(sessionId = sessionId, movementId = null, text = trimmed))
        }
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./gradlew :app:testDebugUnitTest --tests "*CaptureRepoReviewTest*"`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/com/jauschua/ironlogv2/data/repo/CaptureRepo.kt \
        app/src/test/java/com/jauschua/ironlogv2/data/repo/CaptureRepoReviewTest.kt
git commit -m "feat(capture): repo saveGroupReview/reviewDraftsFor/saveSessionNote (idempotent)"
```

---

### Task 3: ViewModel trigger + save/skip/reopen + session note on finish

**Files:**
- Modify: `app/src/main/java/com/jauschua/ironlogv2/ui/screens/capture/CaptureViewModel.kt`
- Test: `app/src/test/java/com/jauschua/ironlogv2/ui/capture/CaptureViewModelReviewTest.kt`

**Interfaces:**
- Consumes: Task 2 `CaptureRepo.saveGroupReview/reviewDraftsFor/saveSessionNote`, `ReviewPrefill`; existing `flattenPrescription`, `GroupOut`, `ExerciseOut`, `SurveyDraft`.
- Produces:
  - top-level `data class GroupReview(val group: GroupOut, val surveys: List<SurveyDraft>, val noteText: String?)`
  - top-level `fun groupIsComplete(group: GroupOut, pastIds: Set<Int>): Boolean`
  - VM: `val pendingReview: StateFlow<GroupReview?>`; `fun openReview(group: GroupOut)`, `fun dismissReview()`, `fun saveReview(group: GroupOut, flags: Map<Int, Pair<Boolean, Boolean>>, noteText: String?)`; changed `fun finish(sessionNote: String? = null)`.

**Context:** groups are contiguous in `flattenPrescription` output, so each group's cursor-order LAST set id marks its completion. `logWorkingSet` already has an "advance the cursor" branch (the `else` at `CaptureViewModel.kt:252-268`); the trigger goes at the end of that branch. `pastSetIds` lives in `CaptureScreenLogic.kt` (used by the screen) — `groupIsComplete` is a new pure helper for the reopen affordance.

- [ ] **Step 1: Write the failing test**

Create `app/src/test/java/com/jauschua/ironlogv2/ui/capture/CaptureViewModelReviewTest.kt`:

```kotlin
package com.jauschua.ironlogv2.ui.capture

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import com.jauschua.ironlogv2.data.api.ApiClient
import com.jauschua.ironlogv2.data.api.dto.ExerciseOut
import com.jauschua.ironlogv2.data.api.dto.GroupOut
import com.jauschua.ironlogv2.data.api.dto.PlannedSetOut
import com.jauschua.ironlogv2.data.local.CaptureDatabase
import com.jauschua.ironlogv2.data.repo.CaptureRepo
import com.jauschua.ironlogv2.ui.screens.capture.CaptureViewModel
import com.jauschua.ironlogv2.ui.screens.capture.GroupReview
import com.jauschua.ironlogv2.ui.screens.capture.groupIsComplete
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.http.HttpStatusCode
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class CaptureViewModelReviewTest {

    private fun ps(id: Int, idx: Int) = PlannedSetOut(id = id, set_index = idx, set_role = "WORKING", is_warmup = false)
    private fun ex(id: Int, mid: Int, name: String, sets: List<PlannedSetOut>) =
        ExerciseOut(id = id, movement_id = mid, movement_name = name, order_index = 0,
            scheme = "STRAIGHT", objective = "HYP", planned_sets = sets)

    private fun deps(): Pair<CaptureRepo, CaptureDatabase> {
        val db = Room.inMemoryDatabaseBuilder(
            ApplicationProvider.getApplicationContext<Context>(), CaptureDatabase::class.java,
        ).allowMainThreadQueries().build()
        val engine = MockEngine {
            respond("""{"session_id":7,"status":"COMPLETED","set_logs_written":0,"already_completed":false}""",
                HttpStatusCode.OK, io.ktor.http.headersOf(io.ktor.http.HttpHeaders.ContentType, "application/json"))
        }
        return CaptureRepo(ApiClient(engine = engine), db.captureDao()) to db
    }

    // A single-exercise STRAIGHT group (2 sets) then a 2-exercise GIANT_SET (2 rounds).
    private fun groups() = listOf(
        GroupOut(id = 1, order_index = 0, group_type = "STRAIGHT", rounds = 2, exercises = listOf(
            ex(100, 10, "Bench", listOf(ps(1000, 0), ps(1001, 1))),
        )),
        GroupOut(id = 2, order_index = 1, group_type = "GIANT_SET", rounds = 2, exercises = listOf(
            ex(200, 20, "Pendlay", listOf(ps(2000, 0), ps(2001, 1))),
            ex(201, 21, "InclineDB", listOf(ps(2100, 0), ps(2101, 1))),
        )),
    )

    private suspend fun log(vm: CaptureViewModel, plannedSetId: Int, movementId: Int) =
        vm.logWorkingSet(plannedSetId = plannedSetId, movementId = movementId, setIndex = 0,
            setRole = "WORKING", actualLoad = 100.0, actualReps = 8, tap = "ON_TARGET")

    @Test fun straight_group_fires_review_after_its_last_set() = runBlocking {
        val (repo, db) = deps()
        val vm = CaptureViewModel(repo, sessionId = 7)
        vm.initPrescriptionForTestFromGroups(groups())

        log(vm, 1000, 10)                       // first Bench set — not complete yet
        assertNull(vm.pendingReview.value)
        log(vm, 1001, 10)                       // last Bench set — group complete
        assertEquals(1, vm.pendingReview.value?.group?.id)
        db.close()
    }

    @Test fun giant_set_fires_only_after_final_round_last_exercise() = runBlocking {
        val (repo, db) = deps()
        val vm = CaptureViewModel(repo, sessionId = 7)
        vm.initPrescriptionForTestFromGroups(groups())
        // finish the STRAIGHT group first, then clear its review
        log(vm, 1000, 10); log(vm, 1001, 10); vm.dismissReview()
        // round-major order for the giant set: 2000, 2100, 2001, 2101
        log(vm, 2000, 20); assertNull(vm.pendingReview.value)
        log(vm, 2100, 21); assertNull(vm.pendingReview.value)
        log(vm, 2001, 20); assertNull(vm.pendingReview.value)   // still one exercise left this round
        log(vm, 2101, 21)                                       // final entry of the group
        assertEquals(2, vm.pendingReview.value?.group?.id)
        db.close()
    }

    @Test fun saveReview_writes_one_survey_per_exercise_flags_default_false() = runBlocking {
        val (repo, db) = deps()
        val vm = CaptureViewModel(repo, sessionId = 7)
        vm.initPrescriptionForTestFromGroups(groups())
        val giant = groups()[1]
        // only movement 20 gets asymmetry; 21 left unchecked → false/false
        vm.saveReview(giant, mapOf(20 to (true to false)), noteText = "grip slipped")

        val dao = db.captureDao()
        val surveys = dao.surveysForSession(7).sortedBy { it.movementId }
        assertEquals(listOf(20, 21), surveys.map { it.movementId })
        assertEquals(true, surveys[0].asymmetryFlag)
        assertEquals(false, surveys[0].techniqueFlag)
        assertEquals(false, surveys[1].asymmetryFlag)
        assertNull(vm.pendingReview.value)                       // sheet dismissed
        assertEquals("grip slipped", dao.noteForMovement(7, 20)?.text)  // anchored to first ex
        db.close()
    }

    @Test fun skip_writes_nothing() = runBlocking {
        val (repo, db) = deps()
        val vm = CaptureViewModel(repo, sessionId = 7)
        vm.initPrescriptionForTestFromGroups(groups())
        log(vm, 1000, 10); log(vm, 1001, 10)
        vm.dismissReview()
        assertEquals(0, db.captureDao().surveysForSession(7).size)
        db.close()
    }

    @Test fun finish_writes_session_note_before_submit() = runBlocking {
        val (repo, db) = deps()
        val vm = CaptureViewModel(repo, sessionId = 7)
        vm.initPrescriptionForTestFromGroups(groups())
        vm.finish(sessionNote = "solid day")
        // submit clears drafts on success; the session note was written+batched, so post-submit it's gone
        assertEquals("COMPLETED", vm.submitResult.value)
        db.close()
    }

    @Test fun groupIsComplete_true_only_when_all_group_sets_are_past() {
        val giant = groups()[1]
        assertTrue(!groupIsComplete(giant, setOf(2000, 2100, 2001)))   // missing 2101
        assertTrue(groupIsComplete(giant, setOf(2000, 2100, 2001, 2101)))
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew :app:testDebugUnitTest --tests "*CaptureViewModelReviewTest*"`
Expected: COMPILE FAILURE — `pendingReview`, `saveReview`, `dismissReview`, `finish(sessionNote=)`, `GroupReview`, `groupIsComplete` unresolved.

- [ ] **Step 3: Add the VM state, model, helper, and trigger**

In `CaptureViewModel.kt`:

(a) Add imports near the top: `import com.jauschua.ironlogv2.data.api.dto.ExerciseOut` (if not present) and `import com.jauschua.ironlogv2.data.local.SurveyDraft`.

(b) Add top-level declarations (below `flattenPrescription`/`unilateralPlannedSetIds`, above the class):

```kotlin
/** The group whose review sheet is open, plus any existing drafts to prefill it. */
data class GroupReview(
    val group: GroupOut,
    val surveys: List<SurveyDraft>,
    val noteText: String?,
)

/**
 * Map each group's cursor-order LAST planned-set id → that group. Groups are contiguous in
 * [flattenPrescription], so a group's final flattened entry marks its completion; when that set
 * is logged and the cursor advances past it, the group is done.
 */
internal fun lastSetIdByGroup(groups: List<GroupOut>): Map<Int, GroupOut> =
    groups.mapNotNull { g ->
        flattenPrescription(listOf(g)).lastOrNull()?.let { it.id to g }
    }.toMap()

/** True iff every planned set in [group] is in [pastIds] (used for the reopen affordance). */
fun groupIsComplete(group: GroupOut, pastIds: Set<Int>): Boolean =
    group.exercises.flatMap { it.planned_sets }.map { it.id }.all { it in pastIds }
```

(c) Add the state field + its precompute. Add a private field near `restContextBySetId` (line ~125):

```kotlin
    private var lastSetIdByGroup: Map<Int, GroupOut> = emptyMap()

    private val _pendingReview = MutableStateFlow<GroupReview?>(null)
    val pendingReview: StateFlow<GroupReview?> = _pendingReview.asStateFlow()
```

Populate `lastSetIdByGroup` in BOTH `load()` (after `restContextBySetId = ...`, line ~153) and `initPrescriptionForTestFromGroups()` (after `restContextBySetId = ...`, line ~188):

```kotlin
        lastSetIdByGroup = lastSetIdByGroup(session.groups)   // in load(): session.groups
```
```kotlin
        lastSetIdByGroup = lastSetIdByGroup(groups)           // in initPrescriptionForTestFromGroups(groups)
```

(d) Add the trigger at the END of the cursor-advance branch in `logWorkingSet` (the `else` block, right after the `restContextBySetId[plannedSetId]?.let { ... }` block, still inside the `else`):

```kotlin
            // Group-review trigger: if the set just logged was this group's LAST cursor entry,
            // open the review sheet (prefilled from any existing drafts). Reads state AFTER the
            // Room commit + cursor advance — never gates the write.
            lastSetIdByGroup[plannedSetId]?.let { group ->
                val prefill = repo.reviewDraftsFor(
                    sessionId,
                    group.exercises.map { it.movement_id },
                    anchorMovementId = group.exercises.first().movement_id,
                )
                _pendingReview.value = GroupReview(group, prefill.surveys, prefill.noteText)
            }
```

(e) Add the open/dismiss/save methods + change `finish`. Replace the existing `finish()` (lines ~305-311) and add the new methods:

```kotlin
    /** Reopen a completed group's review sheet, prefilled from existing drafts. */
    fun openReview(group: GroupOut) {
        viewModelScope.launch {
            val prefill = repo.reviewDraftsFor(
                sessionId,
                group.exercises.map { it.movement_id },
                anchorMovementId = group.exercises.first().movement_id,
            )
            _pendingReview.value = GroupReview(group, prefill.surveys, prefill.noteText)
        }
    }

    /** Skip / close the review sheet without writing anything. */
    fun dismissReview() { _pendingReview.value = null }

    /**
     * Persist a group's review. [flags] maps movement_id → (asymmetry, technique); a missing
     * movement defaults to (false, false). Writes one SurveyDraft per exercise + an optional
     * note anchored to the group's first exercise. Idempotent (repo replaces prior rows).
     */
    fun saveReview(group: GroupOut, flags: Map<Int, Pair<Boolean, Boolean>>, noteText: String?) {
        viewModelScope.launch {
            val surveys = group.exercises.map { e ->
                val (asym, tech) = flags[e.movement_id] ?: (false to false)
                SurveyDraft(
                    sessionId = sessionId, movementId = e.movement_id,
                    stickingPoint = null, asymmetryFlag = asym, techniqueFlag = tech,
                )
            }
            repo.saveGroupReview(
                sessionId, surveys,
                anchorMovementId = group.exercises.first().movement_id,
                noteText = noteText,
            )
            _pendingReview.value = null
        }
    }

    /** Batch-submit all pending drafts. Writes the session note (if any) first, then submits. */
    fun finish(sessionNote: String? = null) {
        viewModelScope.launch {
            repo.saveSessionNote(sessionId, sessionNote)
            repo.submit(sessionId)
                .onSuccess { _submitResult.value = it.status }
                .onFailure { _submitResult.value = "RETRY" }
        }
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./gradlew :app:testDebugUnitTest --tests "*CaptureViewModelReviewTest*"`
Expected: PASS (6 tests). Then `./gradlew :app:testDebugUnitTest --tests "*capture*"` → all green (the existing `CaptureViewModelTest` still passes; `finish()` gained an optional param with a default, so its no-arg callers are unaffected).

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/com/jauschua/ironlogv2/ui/screens/capture/CaptureViewModel.kt \
        app/src/test/java/com/jauschua/ironlogv2/ui/capture/CaptureViewModelReviewTest.kt
git commit -m "feat(capture): group-review trigger + save/skip/reopen + session note on finish"
```

---

### Task 4: GroupReviewSheet composable + screen wiring + session-note field

**Files:**
- Create: `app/src/main/java/com/jauschua/ironlogv2/ui/screens/capture/GroupReviewSheet.kt`
- Modify: `app/src/main/java/com/jauschua/ironlogv2/ui/screens/capture/CaptureScreen.kt`
- Test: `app/src/test/java/com/jauschua/ironlogv2/ui/capture/GroupReviewLogicTest.kt`

**Interfaces:**
- Consumes: Task 3 `GroupReview`, `groupIsComplete`, `pendingReview`, `openReview`, `dismissReview`, `saveReview`, `finish(sessionNote)`; existing `pastSetIds` in `CaptureScreenLogic.kt`; `GroupOut`/`ExerciseOut`.
- Produces:
  - pure `fun initialFlags(review: GroupReview): Map<Int, Pair<Boolean, Boolean>>` (top-level in `GroupReviewSheet.kt`) — seeds toggle state from existing survey drafts (missing → false/false).
  - `@Composable fun GroupReviewSheet(review: GroupReview, onSave: (Map<Int, Pair<Boolean, Boolean>>, String?) -> Unit, onSkip: () -> Unit)`.

- [ ] **Step 1: Write the failing test**

Create `app/src/test/java/com/jauschua/ironlogv2/ui/capture/GroupReviewLogicTest.kt`:

```kotlin
package com.jauschua.ironlogv2.ui.capture

import com.jauschua.ironlogv2.data.api.dto.ExerciseOut
import com.jauschua.ironlogv2.data.api.dto.GroupOut
import com.jauschua.ironlogv2.data.api.dto.PlannedSetOut
import com.jauschua.ironlogv2.data.local.SurveyDraft
import com.jauschua.ironlogv2.ui.screens.capture.GroupReview
import com.jauschua.ironlogv2.ui.screens.capture.initialFlags
import org.junit.Assert.assertEquals
import org.junit.Test

class GroupReviewLogicTest {
    private fun ex(mid: Int) = ExerciseOut(
        id = mid, movement_id = mid, movement_name = "M$mid", order_index = 0,
        scheme = "STRAIGHT", objective = "HYP", planned_sets = listOf(
            PlannedSetOut(id = mid * 10, set_index = 0, set_role = "WORKING", is_warmup = false)))

    private val group = GroupOut(id = 1, order_index = 0, group_type = "GIANT_SET", rounds = 1,
        exercises = listOf(ex(10), ex(11)))

    @Test fun initialFlags_seeds_from_existing_drafts_missing_defaults_false() {
        val review = GroupReview(
            group = group,
            surveys = listOf(SurveyDraft(sessionId = 7, movementId = 10, asymmetryFlag = true, techniqueFlag = false)),
            noteText = "x",
        )
        val flags = initialFlags(review)
        assertEquals(true to false, flags[10])       // from the draft
        assertEquals(false to false, flags[11])       // no draft → default
    }

    @Test fun initialFlags_covers_every_exercise_in_the_group() {
        val flags = initialFlags(GroupReview(group, emptyList(), null))
        assertEquals(setOf(10, 11), flags.keys)
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew :app:testDebugUnitTest --tests "*GroupReviewLogicTest*"`
Expected: COMPILE FAILURE — `initialFlags` unresolved.

- [ ] **Step 3: Create the sheet composable + `initialFlags`**

Create `GroupReviewSheet.kt`:

```kotlin
package com.jauschua.ironlogv2.ui.screens.capture

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Checkbox
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.Button
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp

/** Seed toggle state from existing survey drafts; every exercise present, missing → (false,false). */
fun initialFlags(review: GroupReview): Map<Int, Pair<Boolean, Boolean>> =
    review.group.exercises.associate { e ->
        val d = review.surveys.firstOrNull { it.movementId == e.movement_id }
        e.movement_id to ((d?.asymmetryFlag ?: false) to (d?.techniqueFlag ?: false))
    }

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun GroupReviewSheet(
    review: GroupReview,
    onSave: (Map<Int, Pair<Boolean, Boolean>>, String?) -> Unit,
    onSkip: () -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    // Mutable per-exercise flag state, seeded once from the (prefilled) review.
    val flags = remember(review) {
        mutableStateMapOf<Int, Pair<Boolean, Boolean>>().apply { putAll(initialFlags(review)) }
    }
    var note by remember(review) { mutableStateOf(review.noteText ?: "") }

    ModalBottomSheet(onDismissRequest = onSkip, sheetState = sheetState) {
        Column(
            Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                text = "Quick check — ${review.group.label ?: review.group.group_type}",
                modifier = Modifier.fillMaxWidth(), textAlign = TextAlign.Center,
            )
            review.group.exercises.forEach { e ->
                val (asym, tech) = flags[e.movement_id] ?: (false to false)
                Text(e.movement_name)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = asym, onCheckedChange = { flags[e.movement_id] = it to tech })
                    Text("L/R asymmetry", Modifier.padding(end = 16.dp))
                    Checkbox(checked = tech, onCheckedChange = { flags[e.movement_id] = asym to it })
                    Text("Technique broke down")
                }
            }
            OutlinedTextField(
                value = note, onValueChange = { note = it },
                label = { Text("Note (optional)") }, modifier = Modifier.fillMaxWidth(),
            )
            Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                OutlinedButton(onClick = onSkip) { Text("Skip") }
                Button(onClick = { onSave(flags.toMap(), note.ifBlank { null }) }) { Text("Save") }
            }
        }
    }
}
```

- [ ] **Step 4: Run the logic test to verify it passes**

Run: `./gradlew :app:testDebugUnitTest --tests "*GroupReviewLogicTest*"`
Expected: PASS (2 tests).

- [ ] **Step 5: Wire the sheet + reopen affordance + session-note field into CaptureScreen**

In `CaptureScreen.kt`:

(a) In `CaptureScreen(...)` (the outer composable, near the other `collectAsStateWithLifecycle` calls ~line 57-61), add:

```kotlin
    val pendingReview by vm.pendingReview.collectAsStateWithLifecycle()
```
and render the sheet (place after the existing content, inside the same composable scope that shows `SessionContent`):

```kotlin
    pendingReview?.let { review ->
        GroupReviewSheet(
            review = review,
            onSave = { flags, note -> vm.saveReview(review.group, flags, note) },
            onSkip = { vm.dismissReview() },
        )
    }
```

(b) Reopen affordance: in `SessionContent`, inside the `session.groups.forEachIndexed { gi, group -> ... }` loop, after the `GroupHeader(...)` item, add a small review button for completed groups. `pastIds` is already computed (line ~109). Add an item:

```kotlin
                if (groupIsComplete(group, pastIds)) {
                    item(key = "review-$gi") {
                        androidx.compose.material3.TextButton(onClick = { vm.openReview(group) }) {
                            androidx.compose.material3.Text("✎ Review flags / note")
                        }
                    }
                }
```
Ensure `SessionContent` receives `vm` (or a callback) — it already takes the callbacks it needs; if `SessionContent` does not currently have a reference to call `openReview`, pass an `onOpenReview: (GroupOut) -> Unit = { vm.openReview(it) }` parameter from `CaptureScreen` and call `onOpenReview(group)` instead. Match the existing parameter-passing style used for `logWorkingSet`/`finish`.

(c) Session-note field on Finish: near the Finish button (lines ~335-348), hoist a note state at the top of `SessionContent`:

```kotlin
    var sessionNote by remember(session.id) { mutableStateOf("") }
```
Add a field just above the Finish button:

```kotlin
                androidx.compose.material3.OutlinedTextField(
                    value = sessionNote, onValueChange = { sessionNote = it },
                    label = { androidx.compose.material3.Text("Session note (optional)") },
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
                )
```
and change the Finish button's onClick to pass the note. Find the call to `vm.finish()` (or the `onFinish` callback) and change it to `vm.finish(sessionNote.ifBlank { null })` (or thread `sessionNote` through the existing `onFinish` callback — if `SessionContent` calls an `onFinish: () -> Unit`, change it to `onFinish: (String?) -> Unit` and pass `sessionNote.ifBlank { null }`, with `CaptureScreen` forwarding to `vm.finish(it)`).

(Use fully-qualified `androidx.compose.material3.*` or add imports at the top — match the file's existing import style; it already imports many `material3` symbols.)

- [ ] **Step 6: Build the app (compile the Compose changes)**

Run: `./gradlew :app:assembleDebug`
Expected: BUILD SUCCESSFUL. (Compose wiring has no unit test; the logic pieces — `initialFlags`, `groupIsComplete`, the VM trigger/save — are covered in Tasks 3-4. The build is the compile gate for the screen wiring.)

- [ ] **Step 7: Run the full capture test suite**

Run: `./gradlew :app:testDebugUnitTest --tests "*capture*"`
Expected: all green (DAO, repo, VM, logic).

- [ ] **Step 8: Commit**

```bash
git add app/src/main/java/com/jauschua/ironlogv2/ui/screens/capture/GroupReviewSheet.kt \
        app/src/main/java/com/jauschua/ironlogv2/ui/screens/capture/CaptureScreen.kt \
        app/src/test/java/com/jauschua/ironlogv2/ui/capture/GroupReviewLogicTest.kt
git commit -m "feat(capture): group-review bottom sheet + reopen affordance + session-note field"
```

---

## On-device smoke (deferred — phone off-network)

When the phone is reachable (`adb -s 192.168.1.17:<port> install -r app/build/outputs/apk/debug/app-debug.apk`):
- Finish a STRAIGHT exercise's last set → sheet slides up with that one exercise; toggle a flag + type a note + Save.
- Finish a GIANT_SET's final round → one sheet lists all 2-3 exercises; Save.
- Tap "✎ Review flags / note" on a completed group → sheet reopens prefilled.
- Skip a sheet → no crash, Finish still available.
- Type a Session note on the Finish screen → Finish & Submit → success; drafts cleared (re-enter Capture shows a fresh/empty session or the completed state).

## Routing Plan

| Task | Deliverable | Route |
|---|---|---|
| Task 1 | DAO scoped upsert/query methods + test | Claude Code Agent subagent (client apply+test) |
| Task 2 | Repo review methods + test | Claude Code Agent subagent |
| Task 3 | VM trigger + save/skip/reopen + session note + test | Claude Code Agent subagent |
| Task 4 | GroupReviewSheet + screen wiring + build | Claude Code Agent subagent |

**Delegation ratio: 4/4 tasks delegated (100%).** Tier A (orchestrator) writes no implementation code — it dispatches each task to a fresh implementer subagent, runs the review gate (spec compliance + code quality) between tasks, and does the final whole-branch review. Codex is read-only (can't apply/test Kotlin), so Claude Code Agent subagents are the apply-and-test substrate per the standing fallback. Consensus workers are not used for this client Kotlin work.

## Self-Review

**Spec coverage:** scope (flags+notes, no sticking_point) → Tasks 3-4 write `stickingPoint=null`, no picker ✓. Trigger on group completion → Task 3 `lastSetIdByGroup` + `logWorkingSet` hook, tested for straight + giant ✓. One sheet per group listing all exercises → Task 4 `GroupReviewSheet` iterates `group.exercises` ✓. Save writes one survey/exercise, unchecked=false, skip writes nothing → Task 3 tests ✓. Group note anchored to first exercise → Task 2/3 `anchorMovementId = exercises.first()` ✓. Session note on Finish (null movement) written before submit → Task 3 `finish(sessionNote)` ✓. Idempotent re-edit → Task 2 delete-then-insert, tested ✓. Re-open completed group prefilled → Task 3 `openReview` + Task 4 affordance + `initialFlags` ✓. Submit/clear unchanged ✓. Write-before-advance untouched (trigger after commit+advance) ✓. No new dep, client-only ✓.

**Placeholder scan:** the `<port>` in the install command is an environment value (phone port varies), intentionally left for install time — no build/test step depends on it. No TBD/TODO in code steps; all code is complete.

**Type consistency:** `GroupReview(group, surveys, noteText)`, `saveReview(group, flags: Map<Int, Pair<Boolean,Boolean>>, noteText: String?)`, `saveGroupReview(sessionId, surveys, anchorMovementId, noteText)`, `reviewDraftsFor(...) : ReviewPrefill(surveys, noteText)`, `finish(sessionNote: String?)`, `initialFlags(review): Map<Int, Pair<Boolean,Boolean>>`, `groupIsComplete(group, pastIds)` — names/signatures match across Tasks 1-4. DAO method names (`deleteSurveysForMovements`, `surveysForMovements`, `deleteNoteForMovement`, `noteForMovement`, `deleteSessionNote`, `sessionNote`) consistent between Task 1 definition and Task 2 use.
