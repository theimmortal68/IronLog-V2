import pytest

from ironlog.engine.periodization_resolver import (
    BODY_COMP_STATES,
    POSTURES,
    PROGRESSION_HOLD_IF_BORDERLINE,
    PROGRESSION_SUPPRESSED,
    resolve_envelope,
)


def test_resolver_matches_design_doc_section_8_push_cut_caution_example():
    resolved = resolve_envelope(
        planned_posture="PUSH",
        body_comp_state="CUT",
        recovery_status="CAUTION",
        deload_active=False,
    )

    assert resolved.volume_multiplier == 0.9
    assert resolved.rpe_cap == 8.0
    assert resolved.progression_mode == PROGRESSION_HOLD_IF_BORDERLINE
    assert resolved.optional_work_eligible is False

    assert [step.axis for step in resolved.trace] == [
        "Base (from planned_posture=PUSH)",
        "BodyCompState=CUT",
        "RecoveryStatus=CAUTION",
        "DeloadState=NONE",
        "Effective",
    ]
    assert resolved.trace[0].after == {
        "volume_multiplier": 1.0,
        "rpe_cap": 8.5,
        "progression": "ACTIVE",
        "optional_work": "ALLOW",
    }
    assert resolved.trace[1].after == {
        "volume_multiplier": 0.9,
        "rpe_cap": 8.0,
        "progression": "ACTIVE",
        "optional_work": "ALLOW",
    }
    assert resolved.trace[2].after == {
        "volume_multiplier": 0.9,
        "rpe_cap": 8.0,
        "progression": "HOLD_IF_BORDERLINE",
        "optional_work": "SUPPRESS",
    }
    assert resolved.trace[-1].after == {
        "volume_multiplier": 0.9,
        "rpe_cap": 8.0,
        "progression": "HOLD_IF_BORDERLINE",
        "optional_work": "SUPPRESS",
    }


@pytest.mark.parametrize("planned_posture", POSTURES)
@pytest.mark.parametrize("body_comp_state", BODY_COMP_STATES)
def test_normal_no_deload_resolves_every_posture_body_comp_combination(
    planned_posture,
    body_comp_state,
):
    resolved = resolve_envelope(
        planned_posture=planned_posture,
        body_comp_state=body_comp_state,
        recovery_status="NORMAL",
        deload_active=False,
    )

    assert isinstance(resolved.rpe_cap, float)
    assert isinstance(resolved.volume_multiplier, float)
    assert resolved.trace[1].axis == "BodyCompState=" + body_comp_state
    assert resolved.trace[2].axis == "RecoveryStatus=NORMAL"
    assert resolved.trace[2].before == resolved.trace[2].after


def test_deload_override_replaces_prior_layers_instead_of_stacking():
    resolved = resolve_envelope(
        planned_posture="PUSH",
        body_comp_state="CUT",
        recovery_status="POOR",
        deload_active=True,
        deload_trigger_reason="persistent suppressed recovery plus missed reps",
    )

    deload_step = resolved.trace[3]
    assert deload_step.before == {
        "volume_multiplier": 0.68,
        "rpe_cap": 7.0,
        "progression": "SUPPRESSED",
        "optional_work": "SUPPRESS",
    }
    assert deload_step.after == {
        "volume_multiplier": 0.5,
        "rpe_cap": 6.5,
        "progression": "SUPPRESSED",
        "optional_work": "SUPPRESS",
    }
    assert resolved.volume_multiplier == 0.5
    assert resolved.rpe_cap == 6.5
    assert resolved.progression_mode == PROGRESSION_SUPPRESSED
    assert resolved.optional_work_eligible is False


def test_unrecognized_posture_raises_loudly():
    with pytest.raises(ValueError, match="Unknown planned_posture"):
        resolve_envelope(
            planned_posture="ACCUMULATION",
            body_comp_state="MAINTENANCE",
            recovery_status="NORMAL",
            deload_active=False,
        )
