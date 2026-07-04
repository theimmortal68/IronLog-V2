# Survey + Note Capture UI — Design

**Date:** 2026-07-04
**Repo:** client `~/projects/IronLog-V2-Client` (Kotlin/Compose, Android phone). **Server: NO changes.**
**Status:** Approved design → spec for implementation planning.

## Goal

Close the last gap in the in-gym session loop. Generate → approve/regenerate → capture → finish/submit are already wired curl-free; the one missing piece is **survey + note capture**. The whole contract beneath the UI already exists — `SurveyDraft`/`NoteDraft` Room entities, `CaptureDao` insert/query/clear, `CaptureRepo.submit()` already batches `SubmitRequest.surveys`+`notes`, and the server persists `ExerciseSurvey`/`Note` rows on `POST /sessions/{id}/submit` — but there is no UI entry point and no repo/VM write method, so that data is always submitted empty. Add the UI + write methods so per-exercise `asymmetry_flag`/`technique_flag` and free-text notes get captured in-gym and flow to the engine's data store.

## Scope

| IN | OUT (deferred) |
|---|---|
| Per-exercise `asymmetry_flag` + `technique_flag` toggles | Structured `sticking_point` picker (taxonomy seeded for only 4 lifts — BENCH/BACK_SQUAT/OHP/RDL — and has no GET endpoint; revisit when it's worth a server endpoint) |
| Per-group note (in the sheet) + a session-level note (on Finish) | Any engine *consumption* of survey data (the engine reads none of it today; this is forward-capture for human review + future goal-aware deviation) |
| Auto bottom-sheet on group completion + re-open to edit | Server-side changes of any kind (write path is locked) |
| Client Room-draft writes + submit batching (already exists) | Note re-classification UI (server stamps `JOURNAL`) |

**Why sticking_point is out:** `StickingPointTaxonomy` is seeded for BENCH/BACK_SQUAT/OHP/RDL only, most Phase-1 lifts have no options, there is no fetch endpoint, and nothing consumes the value yet — so the structured picker is low value-per-effort now and would pull in server work. The `asymmetry`/`technique` flags are universal and pure-client. `sticking_point` stays `null` on every written `SurveyDraft` (the field and the server contract already tolerate null).

## Trigger & flow

The capture screen is cursor-driven: `_currentPlannedSetId` advances through `flattenedPrescription` (GIANT_SET round-major, STRAIGHT exercise-major). Groups are contiguous in that flat list, so each group has a well-defined **final entry**.

- Precompute `lastSetIdByGroup: Map<Int, GroupOut>` — keyed by each group's final `PlannedSetOut.id` → that `GroupOut` — computed once in `load()`/`initPrescriptionForTestFromGroups`, exactly alongside the existing `restContextBySetId`.
- In `logWorkingSet`, on the branch that **advances the cursor** (bilateral set, or side-2 of a unilateral set — never on side-1 hold), if the just-logged `plannedSetId` is a key in `lastSetIdByGroup`, set `_pendingReview.value = GroupReview(group)`.
- The screen observes `pendingReview` and shows a **modal bottom sheet**:
  - one row per exercise in the group: `[ ] L/R asymmetry   [ ] Technique broke down`
  - one optional multiline **note** field (group-scoped)
  - `[Skip]` and `[Save]`
- Straight group → single-exercise sheet; giant set → 2–3 rows. Warmups need no handling (a group's last entry is always a working set).

Rest countdown and the survey trigger are independent — both can fire off the same advancing set; the sheet does not block or cancel the rest timer.

## Data writes

On **Save** (`saveReview`):
1. **Idempotent replace:** delete existing `survey_draft` rows for `(sessionId, each movementId in the group)` and delete the group's existing note (see below), then insert — so re-opening and re-saving replaces rather than duplicates.
2. **Surveys:** one `SurveyDraft(sessionId, movementId, stickingPoint = null, asymmetryFlag, techniqueFlag)` per exercise in the group. An unchecked toggle writes `false` (an explicit "reviewed, fine" — Save means the athlete looked). 
3. **Group note:** if the note text is non-blank, one `NoteDraft(sessionId, movementId = group's FIRST/anchor exercise, text)`. Attaching to the anchor gives it movement context and makes the re-edit delete targetable by `(sessionId, movementId)`.

On **Skip** (`dismissReview`): write nothing (surveys stay unanswered = `null`); just clear `_pendingReview`.

**Session note** (Finish screen): a single optional note box. On the **Finish tap**, before `submit` runs: if the text is non-blank, upsert one `NoteDraft(sessionId, movementId = null, text)` (delete the prior `(sessionId, movementId IS NULL)` note first, then insert); if blank, delete any prior session note. Then `submit` proceeds and batches it like any other note. Distinct from group notes, which always carry an anchor movement_id — so `movementId IS NULL` uniquely identifies the session note for upsert.

Submit and draft-clear-on-successful-submit are **unchanged** — `CaptureRepo.submit()` already reads `dao.surveysForSession()` + `dao.notesForSession()` and clears all three draft tables on success.

## Re-access

Completed groups in the capture list remain **tappable**; tapping re-emits `pendingReview` for that group with the sheet **prefilled** from existing drafts (`reviewDraftsFor(sessionId, group)`). This is both the edit path and the recovery path after a Skip.

## Components

**ViewModel (`CaptureViewModel`):**
- `lastSetIdByGroup: Map<Int, GroupOut>` (private, computed in `load`/`initPrescriptionForTestFromGroups`).
- `pendingReview: StateFlow<GroupReview?>` + `openReview(group)` (for tap-to-reopen), `dismissReview()`.
- `saveReview(group, perExerciseFlags, noteText)` — builds the `SurveyDraft` list + optional `NoteDraft`, calls the repo, clears `_pendingReview`.
- `sessionNote` handling for the Finish screen (held in state; written on `finish()` before `submit`, or via a repo call then submit).

**Repo (`CaptureRepo`):**
- `saveGroupReview(sessionId, surveys: List<SurveyDraft>, note: NoteDraft?)` — delete-then-insert (idempotent).
- `reviewDraftsFor(sessionId, movementIds): {surveys, note}` — prefill.
- `saveSessionNote(sessionId, text)` — upsert the null-movement note.

**DAO (`CaptureDao`):** add `deleteSurveys(sessionId, movementIds)`, `deleteNotesForMovement(sessionId, movementId)`, `deleteSessionNote(sessionId)` (movement_id IS NULL), `surveysForMovements(...)`, `noteForMovement(...)`. Keep existing insert/query/clear.

**Compose:** `GroupReviewSheet` (ModalBottomSheet) + a small `ReviewUiState`/`GroupReview` model; a session-note field on the Finish content in `CaptureScreen`. No new Gradle dependency (ModalBottomSheet is in the Material3 already used).

**No DTO changes.** `SurveyDraft`, `NoteDraft`, `SubmitRequest`, and the server route are all already correct.

## Testing

**VM unit (JVM, no device), via `initPrescriptionForTestFromGroups`:**
- Group-complete detection fires `pendingReview` at the correct cursor position for a **straight** group AND a **round-major giant set** (fires only after the final round's last exercise, not mid-round).
- Unilateral last set: fires on side-2 (the advancing call), not side-1.
- `saveReview` writes one survey per exercise with the exact flag states; unchecked → `false`; note non-blank → one anchor-attached `NoteDraft`; blank note → no note.
- Skip writes nothing.
- Re-save replaces (no duplicate rows) — assert via a fake/in-memory DAO.

**Repo/DAO (instrumented or Robolectric-style, matching the repo's existing test style):** delete-then-insert idempotency for surveys and the session note.

**Build + install:** `gradlew :app:assembleDebug` on the workstation; `adb -s 192.168.1.17:<port> install -r`. On-device smoke: finish a straight set → sheet appears; finish a giant set → one sheet lists all exercises; Save persists; reopen shows prefilled; Finish shows the session-note box; submit clears drafts.

## Global constraints

- Client only; **no server changes**. No new Gradle dependency. `SERVER_BASE_URL` stays local-uncommitted.
- Follow the existing capture patterns: write-before-advance ordering is untouched (the survey trigger reads state *after* the Room commit + cursor advance, never gates the set write). The mandatory-tap gate is unchanged.
- Surveys/notes are **optional** — Skip is always available; nothing blocks Finish.
- `sticking_point` is always written `null` this chunk.

## Build order

Single subsystem, one client repo. Suggested task order (finalized in the plan): DAO delete/query methods → repo methods (idempotent replace + prefill + session note) → VM (`lastSetIdByGroup` + trigger + `pendingReview` + save/skip/reopen + session-note state) → `GroupReviewSheet` composable + capture-screen wiring + Finish session-note field → build + install + on-device smoke. Each task TDD with its own commit.
