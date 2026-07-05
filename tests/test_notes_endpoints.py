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


def test_confirm_sets_applied_true():
    # Confirm is a terminal action: it must set BOTH confirmed and applied True.
    # applied==False is what context.py keys on to flag a movement-scoped note to
    # the proposer forever — a confirmed note must stop flagging (like dismiss/apply).
    client, engine = _client()
    _seed_notes(engine)
    cc_id = client.get("/notes/review").json()[0]["id"]
    assert client.post(f"/notes/{cc_id}/confirm").status_code == 200
    with DbSession(engine) as s:
        n = s.get(Note, cc_id)
        assert n.confirmed is True
        assert n.applied is True
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


def test_dismiss_sets_applied_true():
    # A dismissed note must be marked applied=True so it stops flagging the
    # movement as deviation-eligible (Task 2 fix — dismiss previously only
    # reclassified to JOURNAL without setting applied).
    client, engine = _client()
    _seed_notes(engine)
    cc_id = client.get("/notes/review").json()[0]["id"]
    assert client.post(f"/notes/{cc_id}/dismiss").status_code == 200
    with DbSession(engine) as s:
        n = s.get(Note, cc_id)
        assert n.applied is True
        assert n.classification == NoteClass.JOURNAL
    app.dependency_overrides.clear()


def test_confirm_dismiss_404():
    client, _ = _client()
    assert client.post("/notes/999999/confirm").status_code == 404
    assert client.post("/notes/999999/dismiss").status_code == 404
    app.dependency_overrides.clear()
