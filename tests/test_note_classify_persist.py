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


def test_classify_no_key_is_noop_not_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    eng = _engine(monkeypatch)
    sid = _seed_session_with_note(eng, "switch flat bench to incline")
    classify_session_notes(sid)   # classifier=None -> NoteClassifier() raises ValueError; must not raise
    with DBSession(eng) as db:
        n = db.exec(select(Note)).one()
        assert n.classification is None or n.classification == NoteClass.JOURNAL   # unchanged default
        assert n.classification_meta is None
