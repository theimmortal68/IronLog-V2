# Note-Confirm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Classify in-gym notes via the existing Gemini integration into the 4-way `NoteClass`, and let the athlete confirm/dismiss change-proposals. No auto-apply.

**Architecture:** Server-first. Extract a shared `gemini_generate_json` from `GeminiProposer`; a new `NoteClassifier` reuses it with a classification prompt + schema. Classification runs as a FastAPI background task after submit (never blocks/breaks submit). Parsed result persists in a new additive `Note.classification_meta` JSON column. A `/notes/review` inbox + confirm/dismiss endpoints back a lightweight client Review screen.

**Tech Stack:** Python/FastAPI/SQLModel, pytest (via `ssh myflix`); Kotlin/Compose client. Gemini via `GEMINI_API_KEY` (all tests inject a fake HTTP client — no live API).

**Spec:** `~/projects/IronLog-V2/docs/superpowers/specs/2026-07-04-note-confirm-design.md` (commit 61287a4).

## Global Constraints

- Server: NO `from __future__ import annotations`; migration additive (ADD COLUMN) + parity keystone (`tests/test_migrations.py`) green; `engine/` stays pure (the classifier lives under `ironlog/notes/`, it does HTTP); **the background task must never raise into the submit request path**; Gemini via the existing `GEMINI_API_KEY` — no new secret, never echo a key; full pytest suite stays green. Tests run `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'`. BUILD-AND-TEST-ONLY (no live DB / no service restart).
- **No auto-apply** anywhere — confirm (`confirmed=True`) and dismiss (reclassify `JOURNAL`) are the only state transitions on a classified note.
- Client: no new Gradle dependency; `SERVER_BASE_URL` local-uncommitted.

---

## File Structure

- `ironlog/generation/gemini.py` — MODIFY: extract `gemini_generate_json`; refactor `GeminiProposer.propose` onto it (existing tests guard).
- `ironlog/notes/__init__.py`, `ironlog/notes/classify.py` — CREATE: `NoteClassifier`, `NOTE_CLASSIFICATION_SCHEMA`, `NOTE_SYSTEM_INSTRUCTION`, `NoteClassification`, `classify_session_notes`.
- `ironlog/models/session.py` — MODIFY: `Note.classification_meta` JSON column.
- `deploy/migrations/020_note_classification_meta.sql` — CREATE.
- `ironlog/api/app.py` — MODIFY: `BackgroundTasks` hook in `submit_session`; `/notes/review` + confirm + dismiss endpoints + response model.
- Client: DTOs + `NotesRepo` + a `ReviewScreen` + nav entry.
- Tests: `tests/test_note_classifier.py`, `tests/test_note_classify_persist.py`, `tests/test_notes_endpoints.py` (server); client unit + build.

---

### Task 1: Classifier core (`gemini_generate_json` + `NoteClassifier`)

**Files:**
- Modify: `ironlog/generation/gemini.py`
- Create: `ironlog/notes/__init__.py` (empty), `ironlog/notes/classify.py`
- Test: `tests/test_note_classifier.py`

**Interfaces:**
- `gemini_generate_json(api_key, model, system_instruction, user_text, response_schema, http) -> dict` (in `gemini.py`) — POST + extract `candidates[0].content.parts[0].text` + `json.loads`; raises `ProposerError` on bad structure / non-JSON.
- `NoteClassifier(api_key=None, model="gemini-3.1-flash-lite", http=None).classify(text) -> NoteClassification`.
- `NoteClassification(classification: NoteClass, proposed_change: dict|None, confidence: float, rationale: str)`.

- [ ] **Step 1: Write the failing classifier tests**

Create `tests/test_note_classifier.py` (mirrors the `_FakeHTTP`/`_FakeResp`/envelope pattern from `test_generation_gemini.py`):

```python
"""tests/test_note_classifier.py — mocked HTTP, no live Gemini call."""
import json
import pytest

from ironlog.generation.gemini import ProposerError
from ironlog.models.enums import NoteClass
from ironlog.notes.classify import NoteClassifier


class _FakeResp:
    def __init__(self, payload):
        self._p = payload
    def json(self):
        return self._p
    def raise_for_status(self):
        pass


class _FakeHTTP:
    def __init__(self, payload, boom=False):
        self._p = payload
        self.boom = boom
        self.last = None
    def post(self, url, **kw):
        self.last = (url, kw)
        if self.boom:
            raise RuntimeError("network down")
        return _FakeResp(self._p)


def _envelope(obj):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(obj)}]}}]}


def test_config_change_extracts_proposed_change():
    obj = {"classification": "CONFIG_CHANGE",
           "proposed_change": {"movement": "Bench Press", "action": "switch", "params": "to incline"},
           "confidence": 0.9, "rationale": "asks to switch bench to incline"}
    clf = NoteClassifier("k", http=_FakeHTTP(_envelope(obj)))
    r = clf.classify("switch flat bench to incline")
    assert r.classification == NoteClass.CONFIG_CHANGE
    assert r.proposed_change["movement"] == "Bench Press"
    assert r.confidence == 0.9


@pytest.mark.parametrize("cls", ["TRANSIENT_FLAG", "PROGRAMMING_REQUEST", "JOURNAL"])
def test_other_classes_parse(cls):
    obj = {"classification": cls, "proposed_change": None, "confidence": 0.7, "rationale": "x"}
    clf = NoteClassifier("k", http=_FakeHTTP(_envelope(obj)))
    r = clf.classify("some note")
    assert r.classification == NoteClass(cls)
    assert r.proposed_change is None


def test_request_sets_structured_output_schema():
    obj = {"classification": "JOURNAL", "confidence": 0.5, "rationale": "log"}
    http = _FakeHTTP(_envelope(obj))
    NoteClassifier("k", http=http).classify("felt good")
    _, kw = http.last
    gc = kw["json"]["generationConfig"]
    assert gc["responseMimeType"] == "application/json"
    assert "responseJsonSchema" in gc


def test_bad_classification_value_raises():
    obj = {"classification": "NONSENSE", "confidence": 0.5, "rationale": "x"}
    with pytest.raises(ProposerError):
        NoteClassifier("k", http=_FakeHTTP(_envelope(obj))).classify("x")


def test_http_failure_propagates_as_exception():
    # classify() does not swallow — the caller (classify_session_notes) degrades.
    with pytest.raises(RuntimeError):
        NoteClassifier("k", http=_FakeHTTP({}, boom=True)).classify("x")


def test_missing_key_raises_value_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError):
        NoteClassifier()
```

- [ ] **Step 2: Run to verify it fails**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_note_classifier.py'`
Expected: import error / fail (`ironlog.notes.classify` doesn't exist).

- [ ] **Step 3: Extract `gemini_generate_json` in `gemini.py`**

Add the helper (above the `GeminiProposer` class) and refactor `propose` + drop the now-inlined `_parse`:

```python
def gemini_generate_json(api_key, model, system_instruction, user_text, response_schema, http) -> dict:
    """POST a structured-output request to Gemini and return the parsed JSON object.
    Raises ProposerError on unexpected response structure or non-JSON text."""
    url = f"{_GEMINI_V1BETA}/{model}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": response_schema,
            "thinkingConfig": {"thinkingBudget": -1},
        },
    }
    headers = {"x-goog-api-key": api_key}
    resp = http.post(url, json=body, headers=headers)
    resp.raise_for_status()
    raw = resp.json()
    try:
        text = raw["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProposerError(f"Unexpected Gemini response structure: {exc!r}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProposerError(f"Gemini returned non-JSON text: {exc!r}") from exc
```

Replace `GeminiProposer.propose` body and remove `_parse`:

```python
    def propose(self, payload: dict) -> Selections:
        """POST *payload* to Gemini and return parsed ``Selections``."""
        if self._http is None:
            self._http = _default_http_client()
        obj = gemini_generate_json(
            self._api_key, self._model, PROPOSER_SYSTEM_INSTRUCTION,
            json.dumps(payload), SELECTIONS_JSON_SCHEMA, self._http)
        missing = _REQUIRED_KEYS - obj.keys()
        if missing:
            raise ProposerError(f"Gemini response missing required keys: {missing!r}")
        return selections_from_dict(obj)
```

- [ ] **Step 4: Create `ironlog/notes/classify.py`** (and empty `ironlog/notes/__init__.py`)

```python
"""classify.py — NoteClassifier: Gemini adapter that classifies an in-gym note
into the 4-way NoteClass + extracts a proposed change. Reuses gemini_generate_json.

NO from __future__ import annotations (project-wide constraint).
"""
import os
from dataclasses import dataclass
from typing import Optional

from ..generation.gemini import (
    ProposerError, _default_http_client, gemini_generate_json,
)
from ..models.enums import NoteClass

NOTE_SYSTEM_INSTRUCTION = (
    "You classify a strength-training athlete's in-gym note into EXACTLY ONE class:\n"
    "- CONFIG_CHANGE: proposes a specific, actionable change to a movement/load/scheme "
    "(e.g. 'switch flat bench to incline', 'drop OHP to 3x8', 'bump belt squat +10').\n"
    "- PROGRAMMING_REQUEST: a request about programming direction, not a single specific "
    "change (e.g. 'can we add more back work').\n"
    "- TRANSIENT_FLAG: a passing physical/readiness state (e.g. 'shoulder tweaked today').\n"
    "- JOURNAL: a log/observation with no request (e.g. 'felt strong').\n"
    "For CONFIG_CHANGE, extract the proposed change (movement, action, params). "
    "Return confidence 0..1 and a one-line rationale."
)

NOTE_CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "classification": {
            "type": "string",
            "enum": ["CONFIG_CHANGE", "PROGRAMMING_REQUEST", "TRANSIENT_FLAG", "JOURNAL"],
        },
        "proposed_change": {
            "type": ["object", "null"],
            "properties": {
                "movement": {"type": ["string", "null"]},
                "action": {"type": ["string", "null"]},
                "params": {"type": ["string", "null"]},
            },
        },
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
    },
    "required": ["classification", "confidence", "rationale"],
}


@dataclass
class NoteClassification:
    classification: NoteClass
    proposed_change: Optional[dict]
    confidence: float
    rationale: str


class NoteClassifier:
    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-3.1-flash-lite", http=None):
        if api_key is None:
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("api_key not provided and GEMINI_API_KEY env var is not set")
        self._api_key = api_key
        self._model = model
        self._http = http

    def classify(self, text: str) -> NoteClassification:
        if self._http is None:
            self._http = _default_http_client()
        obj = gemini_generate_json(
            self._api_key, self._model, NOTE_SYSTEM_INSTRUCTION, text,
            NOTE_CLASSIFICATION_SCHEMA, self._http)
        try:
            cls = NoteClass(obj["classification"])
        except (KeyError, ValueError) as exc:
            raise ProposerError(f"bad classification value: {exc!r}") from exc
        return NoteClassification(
            classification=cls,
            proposed_change=obj.get("proposed_change"),
            confidence=float(obj.get("confidence", 0.0)),
            rationale=obj.get("rationale", ""),
        )
```

- [ ] **Step 5: Run classifier tests + the proposer regression**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_note_classifier.py tests/test_generation_gemini.py'`
Expected: PASS (new classifier tests + the existing 6 GeminiProposer tests still green — the `gemini_generate_json` refactor preserved behavior).

- [ ] **Step 6: Commit**

```bash
cd ~/projects/IronLog-V2 && git add ironlog/generation/gemini.py ironlog/notes/ tests/test_note_classifier.py
git commit -m "feat(notes): NoteClassifier via shared gemini_generate_json (4-way NoteClass)"
```

---

### Task 2: Persist classification + background hook

**Files:**
- Modify: `ironlog/models/session.py` (`Note.classification_meta`)
- Create: `deploy/migrations/020_note_classification_meta.sql`
- Modify: `ironlog/notes/classify.py` (add `classify_session_notes`)
- Modify: `ironlog/api/app.py` (`BackgroundTasks` in `submit_session`)
- Test: `tests/test_note_classify_persist.py`

**Interfaces:**
- `Note.classification_meta: Optional[dict]` (JSON, nullable).
- `classify_session_notes(session_id: int, classifier=None) -> None` — opens its own `Session(engine)`; classifies each note; degrades to `JOURNAL` on any failure; the `classifier` param injects a fake in tests.

- [ ] **Step 1: Write the failing persistence tests**

Create `tests/test_note_classify_persist.py`:

```python
"""tests/test_note_classify_persist.py — background classification persistence + degradation."""
from datetime import date

from sqlmodel import SQLModel, Session as DBSession, create_engine, select
from sqlmodel.pool import StaticPool

import ironlog.models  # register tables
from ironlog.models.enums import NoteClass
from ironlog.models.session import Note, Session as WorkoutSession
from ironlog.notes.classify import NoteClassification, classify_session_notes


class _FakeClassifier:
    def __init__(self, result=None, boom=False):
        self._result = result
        self.boom = boom
    def classify(self, text):
        if self.boom:
            raise RuntimeError("gemini down")
        return self._result


def _seed_session_with_note(engine, text):
    with DBSession(engine) as db:
        ws = WorkoutSession(date=date(2026, 7, 1), day_role="D1 Upper Push", phase="P1")
        db.add(ws); db.commit(); db.refresh(ws)
        db.add(Note(session_id=ws.id, text=text)); db.commit()
        return ws.id


def _engine(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    import ironlog.db as dbmod
    monkeypatch.setattr(dbmod, "engine", eng)
    return eng


def test_classify_session_notes_persists_classification_and_meta(monkeypatch):
    eng = _engine(monkeypatch)
    sid = _seed_session_with_note(eng, "switch flat bench to incline")
    result = NoteClassification(NoteClass.CONFIG_CHANGE,
                                {"movement": "Bench", "action": "switch", "params": "incline"}, 0.9, "r")
    classify_session_notes(sid, classifier=_FakeClassifier(result))
    with DBSession(eng) as db:
        n = db.exec(select(Note)).one()
        assert n.classification == NoteClass.CONFIG_CHANGE
        assert n.classification_meta["proposed_change"]["movement"] == "Bench"
        assert n.classification_meta["confidence"] == 0.9


def test_classify_degrades_to_journal_on_failure(monkeypatch):
    eng = _engine(monkeypatch)
    sid = _seed_session_with_note(eng, "felt strong")
    classify_session_notes(sid, classifier=_FakeClassifier(boom=True))   # must not raise
    with DBSession(eng) as db:
        n = db.exec(select(Note)).one()
        assert n.classification is None or n.classification == NoteClass.JOURNAL   # unchanged default
```

- [ ] **Step 2: Run to verify it fails**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_note_classify_persist.py'`
Expected: fail (`classification_meta` attr missing / `classify_session_notes` undefined).

- [ ] **Step 3: Add the JSON column + migration**

In `ironlog/models/session.py`, add to `Note` (mirror the existing JSON-column pattern used by `MovementState.ht_band_config`; add `from sqlalchemy import Column, JSON` if not present):

```python
    classification_meta: Optional[dict] = Field(default=None, sa_column=Column(JSON))
```

Create `deploy/migrations/020_note_classification_meta.sql`:

```sql
-- 020_note_classification_meta.sql — note-confirm: parsed classification metadata
-- {proposed_change, confidence, rationale}. Purely-additive (ADD COLUMN nullable JSON)
-- -> allowed per the deploy/migrations/README.md carve-out.
ALTER TABLE note ADD COLUMN classification_meta JSON;
```

- [ ] **Step 4: Add `classify_session_notes` to `ironlog/notes/classify.py`**

```python
def classify_session_notes(session_id: int, classifier=None) -> None:
    """Background: classify every Note in a session via Gemini and persist the result.
    Opens its OWN DB session (the request session is closed post-response). Degrades to
    leaving a note as JOURNAL on any per-note failure or a missing key; never raises."""
    from sqlmodel import Session, select  # noqa: PLC0415
    from ..db import engine  # noqa: PLC0415
    from ..models.session import Note  # noqa: PLC0415

    if classifier is None:
        try:
            classifier = NoteClassifier()
        except ValueError:
            return  # no GEMINI_API_KEY -> leave notes JOURNAL

    with Session(engine) as db:
        notes = db.exec(select(Note).where(Note.session_id == session_id)).all()
        for note in notes:
            try:
                result = classifier.classify(note.text)
            except Exception:
                continue  # degrade: leave this note as-is (JOURNAL default)
            note.classification = result.classification
            note.classification_meta = {
                "proposed_change": result.proposed_change,
                "confidence": result.confidence,
                "rationale": result.rationale,
            }
            db.add(note)
        db.commit()
```

- [ ] **Step 5: Wire the background hook into `submit_session`**

In `ironlog/api/app.py`: import `BackgroundTasks` from `fastapi` and `classify_session_notes`; add the param + schedule the task right before the `return`:

```python
from fastapi import BackgroundTasks
from ..notes.classify import classify_session_notes
```
```python
def submit_session(session_id: int, req: SubmitRequest, background_tasks: BackgroundTasks,
                   db: Session = Depends(get_session)):
    ...  # existing body unchanged (write set_logs/surveys/notes, refine, run_analysis)
    background_tasks.add_task(classify_session_notes, session_id)
    return SubmitResponse(...)
```
(FastAPI injects `BackgroundTasks` by type; place the param before the `db` default. The `already_completed` short-circuit path may return earlier — schedule only on the fresh-submit path, i.e. alongside the existing refine/run_analysis calls, not on the idempotent re-submit.)

- [ ] **Step 6: Run tests + migration parity + full suite**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_note_classify_persist.py tests/test_migrations.py'`
Expected: PASS (persistence + degradation; migration chain-matches-create-all green with column 020). Then full suite: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'` → green (existing submit tests unaffected — the background task is scheduled, not run inline; if a submit test breaks, verify the `BackgroundTasks` param didn't change the signature contract).

- [ ] **Step 7: Commit**

```bash
cd ~/projects/IronLog-V2 && git add ironlog/models/session.py deploy/migrations/020_note_classification_meta.sql \
    ironlog/notes/classify.py ironlog/api/app.py tests/test_note_classify_persist.py
git commit -m "feat(notes): persist classification (migration 020) + background classify on submit"
```

---

### Task 3: Review + confirm + dismiss endpoints

**Files:**
- Modify: `ironlog/api/app.py`
- Test: `tests/test_notes_endpoints.py`

**Interfaces:**
- `GET /notes/review` → `List[NoteReviewOut]` (unconfirmed `CONFIG_CHANGE`/`PROGRAMMING_REQUEST`, newest first).
- `POST /notes/{id}/confirm` → `{id, confirmed: true}` (sets `confirmed=True`). 404 if missing.
- `POST /notes/{id}/dismiss` → `{id, dismissed: true}` (reclassify `JOURNAL`). 404 if missing.

- [ ] **Step 1: Write the failing endpoint tests**

Create `tests/test_notes_endpoints.py` (mirror `test_session_logs.py`'s `_client()` TestClient + StaticPool pattern):

```python
"""tests/test_notes_endpoints.py — /notes/review + confirm + dismiss."""
from datetime import datetime

from fastapi.testclient import TestClient
from sqlmodel import Session as DbSession, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

import ironlog.models  # register tables
from ironlog.api.app import app, get_session
from ironlog.models.enums import NoteClass
from ironlog.models.session import Note


def _client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    def _override():
        with DbSession(engine) as s:
            yield s
    app.dependency_overrides[get_session] = _override
    return TestClient(app), engine


def _seed_notes(engine):
    with DbSession(engine) as s:
        s.add(Note(text="switch bench to incline", classification=NoteClass.CONFIG_CHANGE,
                   classification_meta={"proposed_change": {"movement": "Bench"}, "confidence": 0.9}))
        s.add(Note(text="add more back work", classification=NoteClass.PROGRAMMING_REQUEST,
                   classification_meta={"proposed_change": None, "confidence": 0.6}))
        s.add(Note(text="felt strong", classification=NoteClass.JOURNAL))            # not in review
        s.add(Note(text="shoulder sore", classification=NoteClass.TRANSIENT_FLAG))   # not in review
        s.commit()
        ids = [n.id for n in s.exec(select(Note)).all()]
    return ids


def test_review_lists_only_actionable_unconfirmed():
    client, engine = _client()
    _seed_notes(engine)
    body = client.get("/notes/review").json()
    classes = {r["classification"] for r in body}
    assert classes == {"CONFIG_CHANGE", "PROGRAMMING_REQUEST"}
    cc = next(r for r in body if r["classification"] == "CONFIG_CHANGE")
    assert cc["proposed_change"]["movement"] == "Bench"
    assert cc["confidence"] == 0.9
    app.dependency_overrides.clear()


def test_confirm_removes_from_review():
    client, engine = _client()
    ids = _seed_notes(engine)
    cc_id = client.get("/notes/review").json()[0]["id"]
    assert client.post(f"/notes/{cc_id}/confirm").status_code == 200
    remaining = {r["id"] for r in client.get("/notes/review").json()}
    assert cc_id not in remaining
    app.dependency_overrides.clear()


def test_dismiss_reclassifies_journal():
    client, engine = _client()
    _seed_notes(engine)
    cc_id = client.get("/notes/review").json()[0]["id"]
    assert client.post(f"/notes/{cc_id}/dismiss").status_code == 200
    with DbSession(engine) as s:
        assert s.get(Note, cc_id).classification == NoteClass.JOURNAL
    assert cc_id not in {r["id"] for r in client.get("/notes/review").json()}
    app.dependency_overrides.clear()


def test_confirm_dismiss_404():
    client, _ = _client()
    assert client.post("/notes/999999/confirm").status_code == 404
    assert client.post("/notes/999999/dismiss").status_code == 404
    app.dependency_overrides.clear()
```

- [ ] **Step 2: Run to verify it fails**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_notes_endpoints.py'`
Expected: 404s on the not-yet-defined routes.

- [ ] **Step 3: Add the endpoints**

In `ironlog/api/app.py` (ensure `col` is imported from sqlmodel and `NoteClass` from enums — both already imported per the file):

```python
class NoteReviewOut(BaseModel):
    id: int
    session_id: Optional[int] = None
    movement_id: Optional[int] = None
    created_at: str
    text: str
    classification: str
    proposed_change: Optional[dict] = None
    confidence: Optional[float] = None


@app.get("/notes/review", response_model=List[NoteReviewOut])
def get_notes_review(db: Session = Depends(get_session)):
    """Unconfirmed change-proposals (CONFIG_CHANGE / PROGRAMMING_REQUEST), newest first."""
    from ..models.session import Note
    rows = db.exec(
        select(Note).where(
            Note.confirmed == False,  # noqa: E712
            col(Note.classification).in_([NoteClass.CONFIG_CHANGE, NoteClass.PROGRAMMING_REQUEST]),
        ).order_by(col(Note.id).desc())
    ).all()
    out = []
    for n in rows:
        meta = n.classification_meta or {}
        out.append(NoteReviewOut(
            id=n.id, session_id=n.session_id, movement_id=n.movement_id,
            created_at=n.created_at.isoformat(), text=n.text,
            classification=n.classification.value,
            proposed_change=meta.get("proposed_change"), confidence=meta.get("confidence")))
    return out


@app.post("/notes/{note_id}/confirm")
def confirm_note(note_id: int, db: Session = Depends(get_session)):
    from ..models.session import Note
    n = db.get(Note, note_id)
    if n is None:
        raise HTTPException(404, "note not found")
    n.confirmed = True
    db.add(n); db.commit()
    return {"id": note_id, "confirmed": True}


@app.post("/notes/{note_id}/dismiss")
def dismiss_note(note_id: int, db: Session = Depends(get_session)):
    from ..models.session import Note
    n = db.get(Note, note_id)
    if n is None:
        raise HTTPException(404, "note not found")
    n.classification = NoteClass.JOURNAL
    db.add(n); db.commit()
    return {"id": note_id, "dismissed": True}
```

- [ ] **Step 4: Run tests + full suite**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q tests/test_notes_endpoints.py'` → PASS (4).
Then `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'` → full suite green.

- [ ] **Step 5: Commit**

```bash
cd ~/projects/IronLog-V2 && git add ironlog/api/app.py tests/test_notes_endpoints.py
git commit -m "feat(notes): /notes/review + confirm + dismiss endpoints"
```

---

### Task 4: Client Review screen

**Files (client repo `~/projects/IronLog-V2-Client`):**
- Create: `data/api/dto/NotesModels.kt` (`NoteReviewOut`)
- Create: `data/repo/NotesRepo.kt` (`review()`, `confirm(id)`, `dismiss(id)`)
- Create: `ui/screens/review/ReviewScreen.kt` + `ReviewViewModel.kt`
- Modify: nav (`ui/Nav.kt` + `ui/MainActivity.kt`) — add a Review destination; `AppContainer` wires `NotesRepo`.
- Test: `ui/review/ReviewLogicTest.kt` (+ DTO decode)

**Interfaces:** server contract from Task 3 — `GET /notes/review` → `[{id, session_id, movement_id, created_at, text, classification, proposed_change{movement,action,params}|null, confidence|null}]`; `POST /notes/{id}/confirm`; `POST /notes/{id}/dismiss`.

- [ ] **Step 1: DTOs + repo + a pure display helper (failing test first)**

Create `data/api/dto/NotesModels.kt`:
```kotlin
package com.jauschua.ironlogv2.data.api.dto
import kotlinx.serialization.Serializable

@Serializable data class ProposedChange(
    val movement: String? = null, val action: String? = null, val params: String? = null,
)
@Serializable data class NoteReviewOut(
    val id: Int, val session_id: Int? = null, val movement_id: Int? = null,
    val created_at: String, val text: String, val classification: String,
    val proposed_change: ProposedChange? = null, val confidence: Double? = null,
)
```

Create `ui/screens/review/ReviewLogic.kt` with a pure formatter + its test (`ui/review/ReviewLogicTest.kt`):
```kotlin
// ReviewLogic.kt
package com.jauschua.ironlogv2.ui.screens.review
import com.jauschua.ironlogv2.data.api.dto.NoteReviewOut

/** One-line summary of a proposed change, e.g. "Bench · switch · to incline"; empty if none. */
fun proposedChangeLine(n: NoteReviewOut): String =
    n.proposed_change?.let { pc ->
        listOfNotNull(pc.movement, pc.action, pc.params).joinToString(" · ")
    }.orEmpty()
```
Test asserts: a full proposed_change joins with " · "; a null proposed_change → "" ; a PROGRAMMING_REQUEST (null change) → "".

- [ ] **Step 2: Run the logic test to verify it fails, then implement**

Run: `cd ~/projects/IronLog-V2-Client && ./gradlew :app:testDebugUnitTest --tests "*ReviewLogicTest*"` (fails → add `ReviewLogic.kt` → pass).

- [ ] **Step 3: NotesRepo (Ktor, mirror `GenerateRepo`)**

```kotlin
// NotesRepo.kt
package com.jauschua.ironlogv2.data.repo
import com.jauschua.ironlogv2.data.api.ApiClient
import com.jauschua.ironlogv2.data.api.dto.NoteReviewOut
import com.jauschua.ironlogv2.data.api.runCatchingApi
import io.ktor.client.call.body
import io.ktor.client.request.*

class NotesRepo(private val apiClient: ApiClient) {
    suspend fun review(): Result<List<NoteReviewOut>> = runCatchingApi {
        apiClient.http.get("/notes/review").body()
    }
    suspend fun confirm(id: Int): Result<Unit> = runCatchingApi {
        apiClient.http.post("/notes/$id/confirm"); Unit
    }
    suspend fun dismiss(id: Int): Result<Unit> = runCatchingApi {
        apiClient.http.post("/notes/$id/dismiss"); Unit
    }
}
```
Wire `notesRepo` into `AppContainer` (mirror `generateRepo`).

- [ ] **Step 4: ReviewViewModel + ReviewScreen**

`ReviewViewModel`: `state: StateFlow<UiState<List<NoteReviewOut>>>`, `load()` (calls `review()`), `confirm(id)`/`dismiss(id)` (call repo then `load()` to refresh) — mirror `HistoryViewModel`'s structure (UiState + errorMessage + Factory). `ReviewScreen`: `LazyColumn` of cards — each shows `classification`, `text`, `proposedChangeLine(n)` (when non-empty), and **Confirm** / **Dismiss** buttons; `ErrorRetryBox` on error, `CircularProgressIndicator` on load (reuse the shared components). Add a nav destination + entry point (a bottom-nav item or a button from an existing screen — match the existing `Nav.kt`/`MainActivity.kt` pattern; keep it minimal).

- [ ] **Step 5: Build + full unit suite**

Run: `cd ~/projects/IronLog-V2-Client && ./gradlew :app:assembleDebug` (BUILD SUCCESSFUL) and `./gradlew :app:testDebugUnitTest` (green). Do NOT commit `app/build.gradle.kts`.

- [ ] **Step 6: Commit**

```bash
cd ~/projects/IronLog-V2-Client
git add app/src/main/java/com/jauschua/ironlogv2/data/api/dto/NotesModels.kt \
        app/src/main/java/com/jauschua/ironlogv2/data/repo/NotesRepo.kt \
        app/src/main/java/com/jauschua/ironlogv2/ui/screens/review/ \
        app/src/main/java/com/jauschua/ironlogv2/ui/Nav.kt \
        app/src/main/java/com/jauschua/ironlogv2/ui/MainActivity.kt \
        app/src/main/java/com/jauschua/ironlogv2/IronLogV2Application.kt \
        app/src/test/java/com/jauschua/ironlogv2/ui/review/ReviewLogicTest.kt
git commit -m "feat(notes): client Review screen — confirm/dismiss change-proposals"
```

---

## On-device smoke (deferred — phone off-network)

Write a config-change note in a session → submit → (background classifies) → open Review → the note shows classified with its proposed change → Confirm removes it; Dismiss removes it. Requires the server restarted with the new endpoints + `GEMINI_API_KEY` set.

## Routing Plan

| Task | Repo | Deliverable | Route |
|---|---|---|---|
| 1 | server | `gemini_generate_json` + `NoteClassifier` | Claude Code Agent subagent (ssh myflix pytest) |
| 2 | server | migration 020 + persist + background hook | Claude Code Agent subagent |
| 3 | server | review/confirm/dismiss endpoints | Claude Code Agent subagent |
| 4 | client | Review screen + DTOs + repo | Claude Code Agent subagent (workstation gradlew) |

**Delegation ratio: 4/4 (100%).** Tier A orchestrates + reviews; codex read-only so Claude subagents apply/test.

## Self-Review

**Spec coverage:** Gemini reuse via extracted helper + `NoteClassifier` (Task 1); 4-way NoteClass + extraction (Task 1 schema/prompt); background-on-submit, never breaks submit (Task 2 hook + degradation tests); additive migration `classification_meta` (Task 2); review inbox + confirm + dismiss (Task 3); client Review surface (Task 4); no auto-apply (nowhere mutates program/engine — confirm sets `confirmed`, dismiss reclassifies). Proposer regression guarded (Task 1 Step 5). Migration parity (Task 2 Step 6). Degradation on missing key / http failure (Task 1 + Task 2 tests).

**Placeholder scan:** the `<port>` in the deferred smoke is an environment value; Task 4's nav "match the existing pattern" is a grounded adapt-to-real-code directive (the client's nav structure is stable and the implementer reads it), not a code placeholder. All server code steps carry complete code.

**Type consistency:** `NoteClassification(classification, proposed_change, confidence, rationale)` consistent across classify + persist. `classification_meta = {proposed_change, confidence, rationale}` written in Task 2 and read in Task 3 (`meta.get("proposed_change")`/`meta.get("confidence")`) and the client DTO (`proposed_change`/`confidence`) — field names align server→client. `gemini_generate_json` signature identical between definition (Task 1) and both callers (proposer refactor + `NoteClassifier`). Endpoint response field names (`NoteReviewOut`) match the client `NoteReviewOut` DTO verbatim.
