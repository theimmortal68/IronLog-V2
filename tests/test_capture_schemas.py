from ironlog.api.schemas_capture import SubmitRequest, SetLogIn, SessionDetailResponse


def test_submitrequest_parses_minimal_working_set():
    req = SubmitRequest.model_validate({
        "set_logs": [{
            "planned_set_id": 10, "movement_id": 3, "set_index": 0,
            "set_role": "WORKING", "is_warmup": False,
            "actual_load": 100.0, "actual_reps": 8, "feedback_tap": "ON_TARGET",
        }],
        "surveys": [], "notes": [],
    })
    assert req.set_logs[0].feedback_tap == "ON_TARGET"
    assert req.set_logs[0].planned_set_id == 10
    assert req.surveys == [] and req.notes == []


def test_setlogin_allows_null_planned_set_and_optional_fields():
    s = SetLogIn.model_validate({
        "planned_set_id": None, "movement_id": 3, "set_index": 1,
        "set_role": "WARMUP", "is_warmup": True,
    })
    assert s.planned_set_id is None
    assert s.feedback_tap is None and s.actual_load is None


def test_session_detail_response_nests_groups_exercises_sets():
    resp = SessionDetailResponse.model_validate({
        "id": 1, "date": "2026-07-01", "day_role": "D1 Upper Push",
        "phase": "P1", "status": "PLANNED",
        "groups": [{
            "id": 1, "order_index": 0, "group_type": "STRAIGHT", "rounds": 1,
            "rest_seconds": 180, "label": None,
            "exercises": [{
                "id": 1, "movement_id": 3, "movement_name": "Bench Press [PB]",
                "order_index": 0, "scheme": "TOPSET_BACKOFF", "objective": "PROGRESS",
                "planned_sets": [{
                    "id": 10, "set_index": 0, "set_role": "TOP", "is_warmup": False,
                    "target_load": 100.0, "target_reps_low": 5, "target_reps_high": 8,
                    "target_rpe": 8.0,
                }],
            }],
        }],
    })
    assert resp.groups[0].exercises[0].movement_name == "Bench Press [PB]"
    assert resp.groups[0].exercises[0].planned_sets[0].set_role == "TOP"
