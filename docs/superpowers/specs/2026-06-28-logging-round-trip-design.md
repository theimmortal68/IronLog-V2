# IronLog-V2 — Logging Round-Trip Design (the loop-closer)

**Status:** design closed, awaiting spec-review gate → writing-plans
**Date:** 2026-06-28
**Parent context:** closes the 1.0-beta loop — **generate → follow → LOG (per-set tap) → adapt.** v0.6 built generation; this builds the capture half. Server write-side AND client capture-side, designed together (one chunk).
**Out of scope:** prod reseed (separate ops step); the approve/regenerate client surface (the client-polish chunk); the Notes classify-and-apply config-mutation flow (deferred — see Fork 4).

---

## 0. What this is

The data model (`SetLog`/`ExerciseSurvey`/`Note`/`PlannedSet`, spec 05) and the analyze-at-log seam (`run_analysis`, fired by `/sessions/{id}/log`) already exist from v0.5/v0.6. The **gap** is the middle of the lifecycle: nothing writes SetLogs, captures surveys, or marks a session COMPLETED. This chunk builds the capture write path + the client capture screen that feed the seam v0.6 already expects.

**Design anchor:** the capture layer's shape is dictated by **what the analyzers consume** — `run_analysis` keys e1RM off the best *tapped working set*; `feedback_tap` (mandatory), the planned-vs-logged delta, and RPE feed stall/calibration. We design to that appetite, which v0.6 already specified.

**Lower judgment-density than v0.6** — mostly deterministic plumbing. The few load-bearing decisions are pinned below; two of them (Fork 2 lost-ack idempotency, Fork 5 write-before-advance) have a fragile-nearby-version that passes a fast test and fails in the real gym, and get hard named tests.

---

## 1. The capture write model (Fork 1) — offline-first batch submit [FOUNDATIONAL]

The user trains in a basement with spotty wifi, so the workout **must** run with zero connectivity — the client holds the entire in-session state locally regardless. Given that, the model is:

> **Per-set writes to LOCAL DURABLE storage (Room); BATCH write to the SERVER at completion.** The "batch" is the *network* boundary, NOT the persistence boundary.

- Each completed set's tap persists to **Room immediately** (survives backgrounding, screen-sleep, app-kill, process-death — which *will* happen across a 90-minute session). The Room write is the in-session source of truth.
- The **server** gets one atomic, retryable submit at completion (Fork 2).
- **Rejected — incremental live server writes (A):** the offline constraint forces local state anyway; per-set server writes add a network dependency *on top of* the local store, failing at the worst moment (mid-set), and force server-side partial-session management for data no consumer uses until completion (`run_analysis` only runs at completion). A is B + a failure mode.
- **Rejected — hybrid opportunistic sync (C):** YAGNI; the client must hold local state regardless, and mid-session server data feeds nothing until a cross-device-resume consumer exists (post-beta, if ever).

**Consequences (locked):**
- **IN_PROGRESS is a client-side (Room) state; the server transitions PLANNED→COMPLETED atomically at submit.** No server-side partial-session management, no orphans. (Cross-device resume would need a server IN_PROGRESS record — YAGNI now.)
- **Abandoned session = discard local; the server's PLANNED session stays harmlessly stale.** Generation reads *logged history*, not un-acted plans, so a stale PLANNED is inert. No abandon-handling machinery.
- **Mandatory `feedback_tap` enforced at BOTH ends** (the §6 capture invariant): the client UI won't advance a working set without a tap; the server `/submit` rejects a batch containing a tapless working SetLog. A recovered session retains its taps because local writes are per-set.

---

## 2. Submit endpoint shape + idempotency (Fork 2) [FOUNDATIONAL]

**`POST /sessions/{id}/submit`** — the single client-facing completion endpoint. In one transaction it:
1. **Validates** every working (`WORKING`/`TOP`/`BACKOFF`) SetLog carries a `feedback_tap` — rejects the whole batch (422) if any is missing (server-side §6 enforcement).
2. **Writes** all SetLogs (+ ExerciseSurveys + raw Notes — Fork 4).
3. **Transitions** the session PLANNED→COMPLETED.
4. **Fires `run_analysis`** (the analyze-at-log seam; reuses v0.6's `_week_keyer` + the `session.analyzed_at` guard).

**Idempotent on `session_id`** — lost-ack retry is the *norm* on gym wifi, so this is load-bearing (same silent-corruption class as v0.6 Fork 5d "analysis idempotency must be correct"):
- If the session is **already COMPLETED**, the submit is a **complete no-op** that returns the existing result — **no duplicate SetLogs, no duplicate analysis advance, no duplicate `e1rmhistory` row.**
- The client retries until it gets an ack; a lost-ack retry of a *succeeded* submit returns the same success and writes nothing new.

`/sessions/{id}/log` (fires `run_analysis` only) stays as the **internal seam / manual re-analysis** path; `/submit` is the primary round-trip endpoint and calls the same seam. *(Rejected: two calls write-then-log — two failure points, no benefit.)*

---

## 3. Capture payload + prescription-load reads (Fork 3)

**Capture DTO mirrors `SetLog`, keyed by `planned_set_id`** (the planned-vs-logged link — the delta is the signal): `actual_load`, `actual_reps`, `feedback_tap`, `rpe_numeric` (optional; surfaced only on primaries during calibration, per spec), `is_warmup`, + assisted (`actual_unassisted_reps`/`actual_assisted_reps`) + HT (`actual_plates`/`band_pair_id`/`felt_peak`). Extra/unplanned sets → `planned_set_id=null` (the model allows it). Mechanical — mirrors exactly what `run_analysis` consumes.

**Read endpoints are load-bearing (the client's read-side contract — the capture screen is built on them):**
- **`GET /sessions/{id}`** → the full Session graph (groups → exercises → PlannedSets). Drives the capture UI ("do this" + capture actuals against it) and is the reload-after-backgrounding path. (No session-read endpoint exists today.)
- **`GET /sessions/today`** (or `?status=PLANNED`) → the client's entry point: locate the approved session to capture against. **Deterministic semantics (pinned):** zero approved-unlogged PLANNED sessions → empty (client shows "generate one"); multiple → the most recent approved PLANNED session (or the one dated today). The entry point must not guess.

---

## 4. Input-stream scope (Fork 4) — A core / B in-batch / C text-now-classify-later

The capture spec (§7) has three streams:
- **(A) Per-set tap → `SetLog`: CORE.** The loop-closer. Required.
- **(B) `ExerciseSurvey` → in the submit batch.** A single tap at exercise conclusion (sticking-point + asymmetry/technique flags); completes the COMPLETED lifecycle and feeds the weak-point analysis v0.6 already built (real consumer, L1-dormant early). Cheap — include it.
- **(C) `Note` → capture raw text NOW; defer the classify-and-apply machinery.** Capture freeform note text in the batch now, **stored unclassified (JOURNAL)** — cheap, and it *preserves early-beta notes* so the later classify/apply flow has historical data to test against. **Deferred:** server classification + the CONFIG_CHANGE confirm→re-baseline apply flow (a config-mutation feature, not the loop-closer). The split is: keep the data, defer the mutation flow.

---

## 5. Client capture architecture (Fork 5) [the durability detail is FOUNDATIONAL]

`CaptureScreen` + `CaptureViewModel` + `CaptureRepo`, mirroring the existing `Autoregulate{Screen,ViewModel}` / `AutoregRepo` + `AppContainer` DI pattern (consistency; no novel patterns).

- **Room** as the durable per-set local store (structured, survives process death). *Not DataStore* (prefs only — wrong tool).
- **Flow:** load today's approved PLANNED session (`GET /sessions/today` → `GET /sessions/{id}`) → render the prescription per group/exercise/set → capture actuals + tap per set → **Room per set** → finish → `CaptureRepo` batch-submits (retryable, idempotent) → clear local on ack.
- **THE DURABILITY ORDERING (pinned — this is the load-bearing refinement):** "Room per set" means **tap → Room write → write COMMITTED → *then* the UI advances.** Synchronous-before-advance, NOT fire-and-forget-async. The ordering *is* the durability guarantee: if the UI advances while the Room write is still pending and Android kills the app, that set is lost. The fragile advance-then-write-async version passes a fast test and loses the last set under real process death.
- **Autoregulation stays separable:** `CaptureRepo` fetches `/autoregulate/next-set` when online, else computes the next-set suggestion from local state — live load suggestions offline.

---

## 6. Named test targets (the "make drift impossible" gates)

1. **PROCESS-DEATH survival (keystone — not backgrounding):** log sets → **kill and recreate the process** (not just background/foreground) → relaunch → assert the session and all logged taps are recovered from Room and the session is submittable. Backgrounding-only passes even the fragile in-memory version and proves nothing; the test must simulate actual process kill, the case where *only durable storage survives*. This is the gym-failure-mode gate.
2. **Mandatory tap both ends:** the client UI cannot advance a working set without a tap; the server `/submit` rejects a batch with a tapless working SetLog (422).
3. **Submit idempotency — the lost-ack case specifically:** submit succeeds → ack lost → client retries → the second submit returns the same success and writes nothing new (no duplicate SetLogs, no duplicate analysis advance, no duplicate `e1rmhistory` row). Not just "call submit twice" — the real-world lost-ack path.
4. **Planned-vs-logged delta:** a submitted SetLog links to its `PlannedSet`; `run_analysis` reads the correct anchor (the best tapped working set) — the delta is the signal.
5. **Offline submit retry:** the submit is queued/retried until connectivity, then succeeds idempotently.

Server tests run on myflix (`ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'`). Client tests via the Android/Gradle path (process-death via the instrumentation/`SavedStateHandle`/Room-recreation harness, or a Room-DAO durability unit test that asserts commit-before-return). NO `from __future__ import annotations` (server).

---

## 7. Settled-permanently vs settled-for-beta

| Decision | Status |
|---|---|
| Fork 1 offline-first batch submit (per-set local-durable + batch-server) | **Foundational-locked** |
| Fork 1 IN_PROGRESS client-side; server PLANNED→COMPLETED atomic; abandoned=discard-local | **Foundational-locked** |
| Fork 2 single idempotent `/submit` (atomic validate→write→complete→analyze; idempotent on session_id) | **Foundational-locked** |
| Fork 3 capture DTO mirrors SetLog keyed by planned_set_id | **Foundational-locked** |
| Fork 3 `GET /sessions/{id}` + `GET /sessions/today` (deterministic) | **Foundational-locked** |
| Fork 4 A SetLog core / B ExerciseSurvey in-batch | **Foundational-locked** |
| Fork 4 C raw note text captured (JOURNAL), classify/apply deferred | **Beta — text now, mutation flow later** |
| Fork 5 CaptureScreen+Repo (mirror Autoregulate) + Room durable per-set | **Foundational-locked** |
| Fork 5 Room-write-COMMITTED-before-UI-advance | **Foundational-locked (the durability guarantee)** |
| Fork 5 autoregulation online-fetch / offline-compute | **Beta** |

---

## 8. Out of scope / carry-forwards
- **Notes classify-and-apply** (CONFIG_CHANGE confirm→re-baseline) — raw text captured now; the mutation flow is a later chunk.
- **Cross-device resume** (server-side IN_PROGRESS record + opportunistic sync) — YAGNI until a real consumer.
- **Approve/regenerate client surface** — the separate client-polish chunk (overlaps this UI but is its own design).
- **Prod reseed** — separate, user-owned, backup-first (pull device DB + WAL before any push), with the MovementState seed-all-vs-lazy decision made explicit.
- **Conditioning logging** (Z2 lightweight log) — spec 05 §188 splits it out; main-work SetLog capture is this chunk, Z2 logging can follow.

---

## Composition

The batch submit *is* the `/log` trigger that fires `run_analysis` (the v0.6 analyze-at-log seam); the per-set captured fields are exactly what `run_analysis` consumes (designing to the consumer's appetite). Two principles carry from v0.6: **make-it-impossible-by-construction** (the durability ordering and the idempotency keying are construction-level guarantees, not vigilance) and **the silent-fail-in-the-gym risk** (process-death + lost-ack are the two places the fragile-nearby-version passes tests and fails in real use — hence the hard named gates). This closes the generation→capture loop: the app stops being "a workout I read" and becomes "a workout I log, and tomorrow's adapts."
