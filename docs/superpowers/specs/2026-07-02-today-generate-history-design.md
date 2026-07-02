# Today / Generate + History — Design

**Date:** 2026-07-02
**Repos:** server `~/projects/IronLog-V2` (FastAPI — 3 additive read endpoints); client `~/projects/IronLog-V2-Client` (Kotlin/Compose — Today tab + History)
**Status:** Approved design → spec for implementation planning

## Goal

Make the app self-sufficient for daily use, fixing two gaps found in real phone use (2026-07-02):
1. **No in-app generate.** After the wizard's Start, the app says "no planned session" — the client never calls the server's `/generate` + `/approve` (sessions were created server-side as a workaround). Add: pick a training day → generate → **review** the proposed workout → **Approve** (or **Regenerate**) → hand off to Capture.
2. **No history.** A COMPLETED session vanishes from Capture (which only reads `/sessions/today` = most-recent PLANNED+unanalyzed). Add a History screen listing past completed sessions and their **logged actuals** (what you actually did).

## Scope line

| THIS chunk | NOT this chunk (separate/later) |
|---|---|
| Client Today tab: pick-day → generate → review → approve → Capture | Auto-suggest "next day" (needs a schedule cursor — deferred; day-picker for now) |
| Client History: list + logged-actuals detail | Planned-vs-actual comparison view (actuals only for v1) |
| 3 additive **read** server endpoints + candidate preview | Editing/deleting past sessions |
| Reuse existing `/generate` + `/approve` (no change to write path) | The progression engine (separate chunk, spec handoff-ready) |
| No DB schema change, no new writers | Warmups/finishers/Z2 (v0.7) |

**Two-writer boundary preserved:** every new endpoint is READ-only. The one writer touched is the *existing* `/approve` (`commit_session`, Fork 7c) — unchanged. No migration (no schema change).

**Locked decisions (from design):** new **Today** home tab (not folded into Capture); **review-then-approve** with Regenerate (not one-tap); **full logged actuals** in History; **day-picker** (user picks D1/D2/D4/D5/D6 — supports doing days out of order, an explicit user need).

---

## Server (IronLog-V2) — 3 additive endpoints

### S1 — Candidate preview on `/generate` (the one non-trivial task)

Today `POST /generate` returns `GenerateResponse{candidate_id, day_role, exhausted, attempts, scope}` — the candidate's actual workout is only in-memory (`_candidates[candidate_id]` = the `GenerationOutcome` whose `.assembled` is an `AssembledSession`), never serialized. To review before approving, add:

- **`GenerateResponse.preview: Optional[SessionDetailResponse]`** — the candidate serialized into the **same shape** `/sessions/{id}` returns. Populated when `outcome.assembled` is present; `null` when `exhausted` (client then shows an error + Regenerate).
- The existing serializer `_serialize_session(ws, db)` reads from the **DB** (queries groups/sets by session_id). The candidate is **not persisted** (Fork 7c: nothing written until approve). So this needs a serializer that walks the **in-memory** `AssembledSession` object graph (`assembled.session` + its groups/exercises/sets held in memory). Preferred: refactor `_serialize_session` to serialize from the in-memory relationship graph (the object already carries groups→exercises→sets), so one serializer serves both the DB path and the candidate path. If the in-memory graph lacks DB ids, emit **display-only provisional ids** (e.g. enumerate) — the review screen is read-only (no logging, no cursor); the real ids arrive after approve via `/sessions/today`.
- **No DB write.** Regenerate = call `/generate` again → new `candidate_id` + new preview. Approve is unchanged.

### S2 — `GET /sessions` (history list)

Return past COMPLETED sessions, most-recent-first, as summaries:

```
SessionSummary: { id: int, date: str, day_role: str, phase: str, status: str }
GET /sessions -> List[SessionSummary]
```

Filter `status == COMPLETED`, `ORDER BY id DESC` (or date desc). No pagination for v1 (single user, modest history) — return all completed.

### S3 — `GET /sessions/{id}/logs` (logged actuals)

Return the `SetLog` actuals for a completed session, flat with movement name, in performed order; the client groups by movement for display:

```
LoggedSet: { movement_id: int, movement_name: str, set_index: int,
             reps: int | null, load: float | null, tap: str | null, is_warmup: bool }
LoggedSetsResponse: { session_id: int, date: str, day_role: str, logs: List[LoggedSet] }
GET /sessions/{id}/logs -> LoggedSetsResponse   # 404 if session not found
```

Source: `SetLog` rows for the session joined to `Movement` for the name. Order by movement appearance then set_index.

### S4 — `GET /programs/{id}/days` (populate the day-picker)

Return the program's **training** day_roles (exclude rest days), in program order:

```
GET /programs/{id}/days -> List[str]   # e.g. ["D1 Upper Push","D2 Lower A","D4 Upper B/Pull","D5 Lower B","D6 Weak Points"]
```

Enumerate `ProgramDay` for the program (reuse the `_program_movement_ids` enumeration pattern), filter to training days (non-rest), return `day_role` in order.

---

## Client (IronLog-V2-Client)

### C1 — API/repo methods

Add to the repo layer (new `GenerateRepo` + extend `CaptureRepo`, mirroring `WizardRepo`'s `runCatchingApi` pattern):
- `generate(dayRole: String): Result<GenerateResponse>` (with `preview`)
- `approve(candidateId: String): Result<ApproveResponse>` (→ `session_id`)
- `programDays(programId: Int): Result<List<String>>`
- `pastSessions(): Result<List<SessionSummary>>`
- `sessionLogs(id: Int): Result<LoggedSetsResponse>`

New DTOs (Kotlin, field-for-field with the Pydantic models — the crossing contract): `GenerateResponse` (+ `preview: SessionDetailResponse?`), `ApproveResponse`, `SessionSummary`, `LoggedSet`, `LoggedSetsResponse`. Reuse the existing `SessionDetailResponse`/`GroupOut`/`ExerciseOut`/`PlannedSetOut` for the preview.

### C2 — "Today" tab (new home/landing tab)

New bottom-nav tab **Today** (make it the start destination; leftmost). `TodayViewModel` state machine:
- **Loading** → check `/sessions/today`.
- **HasPlanned(session)** — a PLANNED session exists → show its header + **"Continue workout"** → navigate to **Capture**.
- **NoSession** — show a **day picker** (from `GET /programs/{id}/days`) + **Generate**.
- **Generating** → `POST /generate(dayRole)`.
- **Preview(candidate)** — render the `preview` graph read-only (reuse Capture's set/group rendering, no inputs) + **Approve** and **Regenerate**.
  - **Approve** → `POST /sessions/{candidate_id}/approve` → `session_id` → navigate to **Capture** (which loads it via `/sessions/today`).
  - **Regenerate** → back to Generating (new `/generate`).
- **Exhausted/Error** — `preview == null` or API error → message + Regenerate/retry.
- A **History** entry point (button/link) → History route.

Program id = the active program (same one the wizard uses; beta = single program).

### C3 — History screen

- `HistoryScreen` (route reached from Today, not a bottom tab): list from `pastSessions()` — rows `date · day_role`. Empty state when none.
- Tap a row → `HistoryDetailScreen`: `sessionLogs(id)` → group `logs` by movement (in first-appearance order) → render actuals per movement (`Bench: 165×8, 165×8, 165×7`). Read-only.

---

## Data flow

```
Today(pick day) → POST /generate → review preview
  → POST /sessions/{cid}/approve → session_id → Capture(existing logging)
  → POST /sessions/{id}/submit → COMPLETED
  → History: GET /sessions → GET /sessions/{id}/logs (actuals)
```

## Crossing contract (locked artifacts)

`GenerateResponse.preview` (= `SessionDetailResponse`, already shared), `SessionSummary`, `LoggedSetsResponse`/`LoggedSet` — Pydantic (`schemas_capture.py` / `app.py`) ↔ Kotlin DTOs must match field-for-field, verified like the existing capture contract.

## Build order

Server-stable-before-client (DTOs are the crossing artifact, like the in-gym chunk):
S1 (preview) → S2 (list) → S3 (logs) → S4 (days) → server pytest green → C1 (repo/DTOs) → C2 (Today tab) → C3 (History) → build + install + phone test.

## Verification

- **Server pytest:** preview serialization matches `/sessions/{id}` shape (same fields) and requires no DB write (candidate not persisted after `/generate`); `/sessions` returns only COMPLETED, newest-first; `/sessions/{id}/logs` returns the logged actuals; `/programs/{id}/days` returns training day_roles in order (excludes rest). Run on myflix via ssh.
- **Client:** unit tests for the Today state machine (loading→noSession→generating→preview→approve→hasPlanned; regenerate; exhausted) and history mapping (flat logs → grouped-by-movement). Build on workstation gradlew + `adb -s 192.168.1.17:36231 install -r`.
- **Phone re-test checklist:** Today tab shows Generate when no session; pick a day → Generate → preview shows the right workout (loads/reps) → Approve → lands in Capture with the session → log + submit → History lists it → tap shows logged actuals. Regenerate returns a fresh candidate. Doing a day out of order works (pick any day).

## Global constraints

- Server: NO `from __future__ import annotations`. All new endpoints READ-only; two-writer boundary intact; **no schema change → no migration**.
- Server tests on myflix (`ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'`); baseline is current `main` post-in-gym-merge.
- Client build on workstation gradlew; `SERVER_BASE_URL=http://192.168.1.7:8000` is a local-uncommitted change (leave it).
- Two-repo: server built/tested on myflix before client; the DTOs are the crossing artifact.
- Substrate: Claude Code Agent subagents apply+test; codex/gemini read-only.
