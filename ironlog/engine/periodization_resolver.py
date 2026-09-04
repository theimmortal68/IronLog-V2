"""
periodization_resolver.py - deterministic long-range policy resolver.

This module consumes already-resolved planning state and returns the effective
session envelope for downstream generation/validation handoff. It is pure
"rules dispose" code: no database writes, no reads, no network, and no LLM
calls.

The policy numbers below are initial implementation values pending real-data
tuning. They are complete, explicit tables for the current posture vocabulary
and BodyCompState axis, not locked training constants.

Boundary: this resolver does not decide whether a deload should be active. The
future trigger/evidence logic owns that decision and passes deload_active here;
when active, DeloadState is a fixed state-agnostic override of the resolved
envelope so far.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple


PROGRESSION_ACTIVE = "ACTIVE"
PROGRESSION_HOLD_IF_BORDERLINE = "HOLD_IF_BORDERLINE"
PROGRESSION_SUPPRESSED = "SUPPRESSED"

POSTURES = (
    "ESTABLISH",
    "BUILD",
    "PUSH",
    "CONSOLIDATE",
    "INTENSIFY",
    "PEAK",
    "DELOAD",
)
BODY_COMP_STATES = ("CUT", "MAINTENANCE", "GAIN")
RECOVERY_STATUSES = ("NORMAL", "CAUTION", "POOR")


@dataclass(frozen=True)
class EnvelopeState:
    rpe_cap: float
    volume_multiplier: float
    progression_mode: str
    optional_work_eligible: bool


@dataclass(frozen=True)
class EnvelopeModifier:
    rpe_delta: float = 0.0
    volume_factor: float = 1.0
    progression_mode: Optional[str] = None
    optional_work_eligible: Optional[bool] = None


@dataclass(frozen=True)
class TraceStep:
    axis: str
    before: Dict[str, object]
    after: Dict[str, object]


@dataclass(frozen=True)
class ResolvedEnvelope:
    rpe_cap: float
    volume_multiplier: float
    progression_mode: str
    optional_work_eligible: bool
    trace: List[TraceStep]


BASE_POSTURE_POLICIES: Dict[str, EnvelopeState] = {
    "ESTABLISH": EnvelopeState(
        rpe_cap=7.5,
        volume_multiplier=0.85,
        progression_mode=PROGRESSION_HOLD_IF_BORDERLINE,
        optional_work_eligible=True,
    ),
    "BUILD": EnvelopeState(
        rpe_cap=8.0,
        volume_multiplier=0.95,
        progression_mode=PROGRESSION_ACTIVE,
        optional_work_eligible=True,
    ),
    "PUSH": EnvelopeState(
        rpe_cap=8.5,
        volume_multiplier=1.0,
        progression_mode=PROGRESSION_ACTIVE,
        optional_work_eligible=True,
    ),
    "CONSOLIDATE": EnvelopeState(
        rpe_cap=8.0,
        volume_multiplier=0.9,
        progression_mode=PROGRESSION_HOLD_IF_BORDERLINE,
        optional_work_eligible=True,
    ),
    "INTENSIFY": EnvelopeState(
        rpe_cap=9.0,
        volume_multiplier=0.85,
        progression_mode=PROGRESSION_ACTIVE,
        optional_work_eligible=True,
    ),
    "PEAK": EnvelopeState(
        rpe_cap=9.0,
        volume_multiplier=0.75,
        progression_mode=PROGRESSION_HOLD_IF_BORDERLINE,
        optional_work_eligible=False,
    ),
    "DELOAD": EnvelopeState(
        rpe_cap=7.0,
        volume_multiplier=0.6,
        progression_mode=PROGRESSION_SUPPRESSED,
        optional_work_eligible=False,
    ),
}


BODY_COMP_POLICIES: Dict[Tuple[str, str], EnvelopeModifier] = {
    ("ESTABLISH", "CUT"): EnvelopeModifier(rpe_delta=-0.5, volume_factor=0.9),
    ("ESTABLISH", "MAINTENANCE"): EnvelopeModifier(),
    ("ESTABLISH", "GAIN"): EnvelopeModifier(volume_factor=1.05),
    ("BUILD", "CUT"): EnvelopeModifier(rpe_delta=-0.5, volume_factor=0.9),
    ("BUILD", "MAINTENANCE"): EnvelopeModifier(),
    ("BUILD", "GAIN"): EnvelopeModifier(volume_factor=1.05),
    ("PUSH", "CUT"): EnvelopeModifier(rpe_delta=-0.5, volume_factor=0.9),
    ("PUSH", "MAINTENANCE"): EnvelopeModifier(),
    ("PUSH", "GAIN"): EnvelopeModifier(volume_factor=1.05),
    ("CONSOLIDATE", "CUT"): EnvelopeModifier(rpe_delta=-0.5, volume_factor=0.9),
    ("CONSOLIDATE", "MAINTENANCE"): EnvelopeModifier(),
    ("CONSOLIDATE", "GAIN"): EnvelopeModifier(volume_factor=1.05),
    ("INTENSIFY", "CUT"): EnvelopeModifier(rpe_delta=-0.5, volume_factor=0.85),
    ("INTENSIFY", "MAINTENANCE"): EnvelopeModifier(),
    ("INTENSIFY", "GAIN"): EnvelopeModifier(volume_factor=1.0),
    ("PEAK", "CUT"): EnvelopeModifier(rpe_delta=-0.5, volume_factor=0.85),
    ("PEAK", "MAINTENANCE"): EnvelopeModifier(),
    ("PEAK", "GAIN"): EnvelopeModifier(volume_factor=1.0),
    ("DELOAD", "CUT"): EnvelopeModifier(),
    ("DELOAD", "MAINTENANCE"): EnvelopeModifier(),
    ("DELOAD", "GAIN"): EnvelopeModifier(),
}


RECOVERY_POLICIES: Dict[str, EnvelopeModifier] = {
    "NORMAL": EnvelopeModifier(),
    "CAUTION": EnvelopeModifier(
        progression_mode=PROGRESSION_HOLD_IF_BORDERLINE,
        optional_work_eligible=False,
    ),
    "POOR": EnvelopeModifier(
        rpe_delta=-1.0,
        volume_factor=0.75,
        progression_mode=PROGRESSION_SUPPRESSED,
        optional_work_eligible=False,
    ),
}


DELOAD_ENVELOPE = EnvelopeState(
    rpe_cap=6.5,
    volume_multiplier=0.5,
    progression_mode=PROGRESSION_SUPPRESSED,
    optional_work_eligible=False,
)


_PROGRESSION_SEVERITY = {
    PROGRESSION_ACTIVE: 0,
    PROGRESSION_HOLD_IF_BORDERLINE: 1,
    PROGRESSION_SUPPRESSED: 2,
}


def _coerce_enum_value(value):
    if isinstance(value, Enum):
        return value.value
    return value


def _state_dict(state: EnvelopeState) -> Dict[str, object]:
    return {
        "volume_multiplier": state.volume_multiplier,
        "rpe_cap": state.rpe_cap,
        "progression": state.progression_mode,
        "optional_work": "ALLOW" if state.optional_work_eligible else "SUPPRESS",
    }


def _strictest_progression(current: str, requested: Optional[str]) -> str:
    if requested is None:
        return current
    if _PROGRESSION_SEVERITY[requested] > _PROGRESSION_SEVERITY[current]:
        return requested
    return current


def _apply_modifier(state: EnvelopeState, modifier: EnvelopeModifier) -> EnvelopeState:
    if modifier.optional_work_eligible is None:
        optional_work_eligible = state.optional_work_eligible
    else:
        optional_work_eligible = modifier.optional_work_eligible
    return EnvelopeState(
        rpe_cap=round(state.rpe_cap + modifier.rpe_delta, 1),
        volume_multiplier=round(state.volume_multiplier * modifier.volume_factor, 2),
        progression_mode=_strictest_progression(
            state.progression_mode,
            modifier.progression_mode,
        ),
        optional_work_eligible=optional_work_eligible,
    )


def _format_expected(values: Tuple[str, ...]) -> str:
    return ", ".join(values)


def _deload_axis(deload_trigger_reason: Optional[str]) -> str:
    if deload_trigger_reason:
        return "DeloadState=ACTIVE: " + deload_trigger_reason
    return "DeloadState=ACTIVE"


def _assert_policy_tables_complete() -> None:
    expected_base = set(POSTURES)
    actual_base = set(BASE_POSTURE_POLICIES)
    if actual_base != expected_base:
        raise RuntimeError(
            "Base posture policy mismatch; missing="
            + repr(sorted(expected_base - actual_base))
            + ", extra="
            + repr(sorted(actual_base - expected_base))
        )

    expected_body_comp = set(
        (posture, state)
        for posture in POSTURES
        for state in BODY_COMP_STATES
    )
    actual_body_comp = set(BODY_COMP_POLICIES)
    if actual_body_comp != expected_body_comp:
        raise RuntimeError(
            "BodyComp policy mismatch; missing="
            + repr(sorted(expected_body_comp - actual_body_comp))
            + ", extra="
            + repr(sorted(actual_body_comp - expected_body_comp))
        )

    expected_recovery = set(RECOVERY_STATUSES)
    actual_recovery = set(RECOVERY_POLICIES)
    if actual_recovery != expected_recovery:
        raise RuntimeError(
            "Recovery policy mismatch; missing="
            + repr(sorted(expected_recovery - actual_recovery))
            + ", extra="
            + repr(sorted(actual_recovery - expected_recovery))
        )


def resolve_envelope(
    planned_posture: str,
    body_comp_state: str,
    recovery_status: str,
    deload_active: bool,
    deload_trigger_reason: Optional[str] = None,
) -> ResolvedEnvelope:
    """Resolve one session envelope from planning axes in deterministic order."""
    posture = _coerce_enum_value(planned_posture)
    body_comp = _coerce_enum_value(body_comp_state)
    recovery = _coerce_enum_value(recovery_status)

    if posture not in BASE_POSTURE_POLICIES:
        raise ValueError(
            "Unknown planned_posture "
            + repr(planned_posture)
            + "; expected one of: "
            + _format_expected(POSTURES)
        )
    if body_comp not in BODY_COMP_STATES:
        raise ValueError(
            "Unknown body_comp_state "
            + repr(body_comp_state)
            + "; expected one of: "
            + _format_expected(BODY_COMP_STATES)
        )
    if recovery not in RECOVERY_POLICIES:
        raise ValueError(
            "Unknown recovery_status "
            + repr(recovery_status)
            + "; expected one of: "
            + _format_expected(RECOVERY_STATUSES)
        )

    trace: List[TraceStep] = []

    state = BASE_POSTURE_POLICIES[posture]
    trace.append(TraceStep(
        axis="Base (from planned_posture=" + posture + ")",
        before={},
        after=_state_dict(state),
    ))

    before = _state_dict(state)
    key = (posture, body_comp)
    if key not in BODY_COMP_POLICIES:
        raise ValueError(
            "Missing BodyComp policy for planned_posture="
            + repr(posture)
            + ", body_comp_state="
            + repr(body_comp)
        )
    state = _apply_modifier(state, BODY_COMP_POLICIES[key])
    trace.append(TraceStep(
        axis="BodyCompState=" + body_comp,
        before=before,
        after=_state_dict(state),
    ))

    before = _state_dict(state)
    state = _apply_modifier(state, RECOVERY_POLICIES[recovery])
    trace.append(TraceStep(
        axis="RecoveryStatus=" + recovery,
        before=before,
        after=_state_dict(state),
    ))

    before = _state_dict(state)
    if deload_active:
        state = DELOAD_ENVELOPE
        deload_axis = _deload_axis(deload_trigger_reason)
    else:
        deload_axis = "DeloadState=NONE"
    trace.append(TraceStep(
        axis=deload_axis,
        before=before,
        after=_state_dict(state),
    ))

    trace.append(TraceStep(
        axis="Effective",
        before=_state_dict(state),
        after=_state_dict(state),
    ))

    return ResolvedEnvelope(
        rpe_cap=state.rpe_cap,
        volume_multiplier=state.volume_multiplier,
        progression_mode=state.progression_mode,
        optional_work_eligible=state.optional_work_eligible,
        trace=trace,
    )


_assert_policy_tables_complete()
