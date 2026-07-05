# Note-Confirm — Design

**Date:** 2026-07-04
**Repos:** server `~/projects/IronLog-V2` (FastAPI/SQLModel) + client `~/projects/IronLog-V2-Client` (Kotlin/Compose).
**Status:** Approved design → spec for implementation planning.

## Goal

Athletes now capture free-text notes in-gym (session + per-exercise), but the server stamps every `Note` `classification=JOURNAL, confirmed=False, applied=False` with no classification logic behind it. This chunk **classifies** each note (via the existing Gemini integration) into the already-modeled 4-way `NoteClass`, and **surfaces change-proposals for the athlete to confirm or dismiss**. Application of a confirmed change stays **out of scope** (deferred goal-aware-deviation territory; the engine isn't live until the config-seed go-live).

## Scope

| IN | OUT (deferred) |
|---|---|
| Gemini classifies each note → `CONFIG_CHANGE` / `TRANSIENT_FLAG` / `PROGRAMMING_REQUEST` / `JOURNAL` + extracted `{proposed_change, confidence, rationale}` | **Auto-apply** — mutating the live program/MovementState/engine from a confirmed note (goal-aware deviation; engine not live) |
| Background classification on submit (never blocks/breaks the in-gym submit) | Any change to the survey/flag capture or the History screen |
| Review inbox: list unconfirmed `CONFIG_CHANGE` + `PROGRAMMING_REQUEST`; confirm / dismiss | LLM in the consensus/codegen loop (this is a product runtime call, separate) |
| One additive migration (`Note.classification_meta` JSON) | Retraining / prompt-tuning infra |
| Client Review screen | On-device verification (phone off-network — deferred) |

**Privacy note:** note text is sent to Google's Gemini API (the same embedded `GEMINI_API_KEY` path already used for session-generation payloads). This is inherent to the chosen classifier and is the established pattern.

## Classification (reuse the Gemini integration)

`ironlog/generation/gemini.py::GeminiProposer` already POSTs to `generativelanguage v1beta …:generateContent` with `responseMimeType=application/json` + a `responseJsonSchema` (enforced structured output), an injectable `http` client (testable without httpx), and `GEMINI_API_KEY` auth.

- **Extract the shared low-level call** into `gemini_generate_json(api_key, model, system_instruction, user_text, response_schema, http) -> dict` (the POST + `candidates[0].content.parts[0].text` extraction + `json.loads` + the missing-key guard). `GeminiProposer.propose` refactors onto it — **its existing tests must stay green** (the regression guard). Keep `ProposerError` semantics.
- **New `NoteClassifier`** (`ironlog/notes/classify.py`), mirroring `GeminiProposer`'s constructor (`api_key`/`model`/injectable `http`), calls `gemini_generate_json` with:
  - a **classification system-instruction**: "You classify a strength-training athlete's in-gym note into exactly one of CONFIG_CHANGE (proposes a specific, actionable change to a movement/load/scheme), PROGRAMMING_REQUEST (a request about programming direction, not a specific change), TRANSIENT_FLAG (a passing physical/readiness state), or JOURNAL (a log/observation with no request). For CONFIG_CHANGE, extract the proposed change." Keep it deterministic-leaning (low temperature via `thinkingConfig` as the proposer uses).
  - a **`NOTE_CLASSIFICATION_SCHEMA`** (`responseJsonSchema`): `{ classification: enum[4], proposed_change: {movement: str|null, action: str|null, params: str|null} | null, confidence: number, rationale: string }` (required: `classification`, `confidence`, `rationale`).
- `classify(text: str) -> NoteClassification` (a small dataclass/dict) — pure of DB; the caller persists.

## When/how — background on submit

- `submit_session` (`api/app.py`) is unchanged in its response path; it gains a FastAPI `BackgroundTasks` param and schedules `classify_session_notes(session_id)` to run **after the response is sent**.
- `classify_session_notes(session_id)` (`ironlog/notes/classify.py` or a `persistence` seam) opens its **own** `Session(engine)` (the request session is closed post-response), loads the session's notes, and for each: calls `NoteClassifier.classify(note.text)`, sets `note.classification` + `note.classification_meta = {proposed_change, confidence, rationale}`, commits.
- **Graceful degradation:** if `GEMINI_API_KEY` is absent, or the call errors/times out/rate-limits, catch → log → leave the note as `JOURNAL` (its default). The submit already succeeded; the note is never lost. A `NoteClassifier` constructed without a key raises on construction, so the background task guards for that and no-ops. No retry in this slice (a re-classify endpoint can be added later if needed).

## Persistence (one additive migration)

- `Note` gains `classification_meta: Optional[dict]` (JSON column, nullable) — holds `{proposed_change, confidence, rationale}`. `classification` (existing enum col) is set by the classifier.
- **Migration `020_note_classification_meta.sql`:** `ALTER TABLE note ADD COLUMN classification_meta JSON;` — purely additive (ADD COLUMN, nullable) per the README carve-out; the parity keystone `tests/test_migrations.py` (chain-matches-create-all) must stay green.
- `confirmed`/`applied` already exist and need no schema change.

## Surface + endpoints (server)

- **`GET /notes/review`** → list of unconfirmed notes with `classification IN (CONFIG_CHANGE, PROGRAMMING_REQUEST)`, newest first: `{id, session_id, movement_id, created_at, text, classification, proposed_change, confidence}`. This is the athlete's "changes I flagged that I haven't acted on" inbox (cross-session).
- **`POST /notes/{id}/confirm`** → set `confirmed=True` (a confirmed-but-not-applied to-do; `applied` stays `False`). Idempotent. 404 on missing.
- **`POST /notes/{id}/dismiss`** → reclassify to `JOURNAL` (removes it from the review list without a new column). Idempotent. 404 on missing.

## Client

- A lightweight **Review** surface (new screen, reachable from a nav entry or a badge; keep it minimal) that lists `GET /notes/review` items: the note text, its classification, the parsed `proposed_change` (when present), and **Confirm** / **Dismiss** buttons wired to the two endpoints; on action, refresh the list. A pending-count badge is optional (nice-to-have, cut if it adds cost).
- DTOs mirror the server response field names. No new Gradle dependency. `SERVER_BASE_URL` local-uncommitted.

## Error handling & boundaries

- The background task must **never** raise into the request path (submit stays green regardless of Gemini state).
- `NoteClassifier` is pure of DB; the persistence seam owns the DB writes. `engine/` stays pure; the classifier lives under `ironlog/notes/` (not `engine/`), since it does IO (HTTP).
- No Option-C concern (this writes `Note` fields only — never `current_load`/`ht_plates`/`ht_band_config`).
- Confirm/dismiss are the human gate; there is **no automatic mutation** of program/engine state anywhere in this chunk.

## Testing

**Server (pytest, `ssh myflix`), all Gemini calls injected (no live API in tests):**
- `NoteClassifier` with an injected fake `http` returning canned Gemini JSON: asserts each of the 4 classes parses; `CONFIG_CHANGE` extracts `proposed_change`; malformed/short response → `ProposerError`-style handling.
- `gemini_generate_json` refactor: `GeminiProposer`'s existing tests stay green (regression guard).
- Degradation: `classify_session_notes` with a `http` that raises (and with no key) → notes remain `JOURNAL`, no exception escapes.
- Migration parity keystone green after adding column 020.
- Endpoints: `/notes/review` returns only unconfirmed CONFIG_CHANGE/PROGRAMMING_REQUEST with `proposed_change`; confirm flips `confirmed`; dismiss reclassifies to JOURNAL and drops it from review; 404s.
- Background hook: submitting a session with notes schedules classification; simulate the task (call `classify_session_notes` directly with an injected classifier) and assert persistence.
- Full existing suite stays green.

**Client (unit + build):** review-list DTO decode; the confirm/dismiss action wiring (pure-logic where possible); build. On-device deferred (phone off-network).

## Build order (SDD, server-first)

1. **Classifier core:** `gemini_generate_json` extraction (proposer refactor, tests green) + `NoteClassifier` + `NOTE_CLASSIFICATION_SCHEMA` + system-instruction (injected-http tests).
2. **Persist + background:** migration `020` + `Note.classification_meta` + `classify_session_notes` + wire the `BackgroundTasks` hook into `submit_session` (degradation tests).
3. **Endpoints:** `/notes/review` + confirm + dismiss (+ tests).
4. **Client:** Review screen + DTOs + confirm/dismiss wiring + build.

## Global constraints

- Server: NO `from __future__ import annotations`; migration additive + parity keystone green; `engine/` stays pure (classifier lives under `notes/`, does HTTP); the background task never fails the submit; Gemini via the existing `GEMINI_API_KEY` (no new secret, no key echoed); full pytest suite stays green.
- Client: no new Gradle dependency; `SERVER_BASE_URL` local-uncommitted.
- **No auto-apply** anywhere; confirm/dismiss are the only state transitions on a classified note this chunk.
