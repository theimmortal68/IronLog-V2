"""tests/test_notes_endpoints.py — /notes/review + confirm + dismiss."""
from datetime import datetime

from fastapi.testclient import TestClient
from sqlmodel import Session as DbSession, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

import ironlog.models  # register tables
from ironlog.api.app import app, get_session
from ironlog.models.enums import NoteClass
from ironlog.models.library import Movement, MovementState
from ironlog.models.program import Program, ProgramDay, Tier, TierExercise, TierKind
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
                   classification_meta={"proposed_change": {"movement": "Bench"}, "confidence": 0.9,
                                        "action_type": "LOAD_INCREASE"}))
        s.add(Note(text="add more back work", classification=NoteClass.PROGRAMMING_REQUEST,
                   classification_meta={"proposed_change": None, "confidence": 0.6}))
        s.add(Note(text="felt strong", classification=NoteClass.JOURNAL))            # not in review
        s.add(Note(text="shoulder sore", classification=NoteClass.TRANSIENT_FLAG))   # not in review
        s.commit()
        ids = [n.id for n in s.exec(select(Note)).all()]
    return ids


def _persist(db, *objs):
    for obj in objs:
        db.add(obj)
    db.commit()
    for obj in objs:
        db.refresh(obj)


def _seed_resolvable_review_program(db):
    program = Program(name="Review Resolver Phase", phase="P1", duration_weeks=4)
    _persist(db, program)

    day = ProgramDay(program_id=program.id, day_index=1, day_role="D1 Upper Push")
    _persist(db, day)

    tier = Tier(
        program_day_id=day.id,
        tier_label="T1",
        tier_order=1,
        tier_kind=TierKind.T1_STRAIGHT,
    )
    _persist(db, tier)

    movement = Movement(
        name="Bench Press [PB]",
        base_name="Bench Press",
        load_floor=45.0,
    )
    _persist(db, movement)

    tier_exercise = TierExercise(
        tier_id=tier.id,
        slot_id="d1_t1",
        movement_id=movement.id,
        exercise_order=1,
        tier_role="anchor",
    )
    state = MovementState(
        movement_id=movement.id,
        day_id="D1 Upper Push",
        current_load=185.0,
        confirmed_at=datetime.utcnow(),
    )
    _persist(db, tier_exercise, state)

    return {"movement": movement, "tier_exercise": tier_exercise}


def _add_review_note(db, text, action_type, proposed_change):
    meta = {"proposed_change": proposed_change, "confidence": 0.9}
    if action_type is not None:
        meta["action_type"] = action_type
    note = Note(
        text=text,
        classification=NoteClass.CONFIG_CHANGE,
        classification_meta=meta,
    )
    _persist(db, note)
    return note


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


def test_review_surfaces_action_type_for_deterministic_apply_routing():
    """/notes/review must surface classification_meta.action_type so the client
    routes Apply deterministically (LOAD/MOVEMENT/REPS) instead of degrading to
    keyword-matching. A note whose meta lacks action_type returns null (no crash)."""
    client, engine = _client()
    _seed_notes(engine)
    body = client.get("/notes/review").json()
    cc = next(r for r in body if r["classification"] == "CONFIG_CHANGE")
    assert cc["action_type"] == "LOAD_INCREASE"
    # PROGRAMMING_REQUEST seed has no action_type in its meta → null, not a crash.
    pr = next(r for r in body if r["classification"] == "PROGRAMMING_REQUEST")
    assert pr["action_type"] is None
    app.dependency_overrides.clear()


def test_review_returns_resolved_proposals_for_resolvable_config_change():
    client, engine = _client()
    with DbSession(engine) as s:
        ctx = _seed_resolvable_review_program(s)
        note = _add_review_note(
            s,
            "bump bench ten pounds",
            "LOAD_INCREASE",
            {"movement": "Bench Press", "action": "bump", "params": "+10"},
        )
        note_id = note.id
        tier_exercise_id = ctx["tier_exercise"].id

    body = client.get("/notes/review").json()
    row = next(r for r in body if r["id"] == note_id)
    proposals = row["resolved_proposals"]

    assert len(proposals) == 1
    assert set(proposals[0]) == {
        "tier_exercise_id",
        "day_role",
        "slot_label",
        "override_type",
        "override_movement_id",
        "load_delta",
        "load_absolute",
        "rep_low",
        "rep_high",
        "override_order",
        "valid",
        "validation_note",
        "summary",
    }
    assert proposals[0]["tier_exercise_id"] == tier_exercise_id
    assert proposals[0]["day_role"] == "D1 Upper Push"
    assert proposals[0]["slot_label"] == "T1"
    assert proposals[0]["override_type"] == "LOAD"
    assert proposals[0]["load_delta"] == 10.0
    assert proposals[0]["valid"] is True
    app.dependency_overrides.clear()


def test_review_skips_resolver_for_other_and_missing_action_type(monkeypatch):
    calls = []

    def tracking_resolver(note, db):
        calls.append(note.id)
        return []

    monkeypatch.setattr("ironlog.api.app.resolve_note", tracking_resolver)
    client, engine = _client()
    with DbSession(engine) as s:
        other = _add_review_note(
            s,
            "observe bench setup",
            "OTHER",
            {"movement": "Bench Press", "action": "observe", "params": None},
        )
        old = _add_review_note(
            s,
            "old note with no action type",
            None,
            {"movement": "Bench Press", "action": "bump", "params": "+10"},
        )
        ids = {other.id, old.id}

    body = [row for row in client.get("/notes/review").json() if row["id"] in ids]

    assert len(body) == 2
    assert calls == []
    assert all(row["resolved_proposals"] == [] for row in body)
    app.dependency_overrides.clear()


def test_review_resolver_exception_degrades_that_note_to_empty_list(monkeypatch):
    from ironlog.notes.resolver import resolve_note as real_resolve_note

    def failing_resolver(note, db):
        if note.text == "malformed reorder note":
            raise ValueError("malformed proposed_change")
        return real_resolve_note(note, db)

    monkeypatch.setattr("ironlog.api.app.resolve_note", failing_resolver)
    client, engine = _client()
    with DbSession(engine) as s:
        ctx = _seed_resolvable_review_program(s)
        good = _add_review_note(
            s,
            "bump bench ten pounds",
            "LOAD_INCREASE",
            {"movement": "Bench Press", "action": "bump", "params": "+10"},
        )
        broken = _add_review_note(
            s,
            "malformed reorder note",
            "REORDER",
            {"movement": "Bench Press", "before_movement": {"unexpected": "shape"}},
        )
        good_id = good.id
        broken_id = broken.id
        tier_exercise_id = ctx["tier_exercise"].id

    response = client.get("/notes/review")

    assert response.status_code == 200
    rows = {r["id"]: r for r in response.json() if r["id"] in {good_id, broken_id}}
    assert rows[broken_id]["resolved_proposals"] == []
    assert rows[good_id]["resolved_proposals"][0]["tier_exercise_id"] == tier_exercise_id
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
