"""
enums.py — every controlled vocabulary from the schema spec.

These are `str, Enum` classes: each member is also a plain string, so it stores
cleanly in the database and serializes cleanly to JSON in the API. Using enums
instead of bare strings means a typo like "MANTAIN" fails immediately instead of
silently corrupting your data.
"""
from enum import Enum


class Region(str, Enum):
    UPPER = "UPPER"
    LOWER = "LOWER"
    CORE = "CORE"
    NONE = "NONE"


class LiftCategory(str, Enum):
    BENCH = "BENCH"
    BACK_SQUAT = "BACK_SQUAT"
    FRONT_SQUAT = "FRONT_SQUAT"
    OHP = "OHP"
    RDL = "RDL"
    DEADLIFT = "DEADLIFT"
    ROW = "ROW"
    HIP_THRUST = "HIP_THRUST"
    REV_HYPER = "REV_HYPER"
    CG_PRESS = "CG_PRESS"
    NONE = "NONE"


class Status(str, Enum):
    ACTIVE = "ACTIVE"        # in the current program
    INACTIVE = "INACTIVE"    # kept but not programmed (curls, future BW dips)
    PREP = "PREP"            # mobility / warmup prep item, never progressed


class ProgressionMode(str, Enum):
    LADDER = "LADDER"
    COMPOSITE = "COMPOSITE"          # Hip Thrust: plates + band
    ASSISTED = "ASSISTED"            # progress by REDUCING assistance
    PROTOCOL = "PROTOCOL"            # bodyweight / reps / tempo
    CONDITIONING = "CONDITIONING"
    NONE = "NONE"


class Scheme(str, Enum):
    STRAIGHT = "STRAIGHT"
    DOUBLE_PROGRESSION = "DOUBLE_PROGRESSION"
    TOPSET_BACKOFF = "TOPSET_BACKOFF"
    UNDULATION = "UNDULATION"
    WAVE = "WAVE"
    REP_RATIO = "REP_RATIO"          # assisted pull-up: shift unassisted:assisted


class Objective(str, Enum):
    MAINTAIN = "MAINTAIN"
    PROGRESS = "PROGRESS"
    MEASURE = "MEASURE"


class AssistSubtype(str, Enum):
    CONTINUOUS = "CONTINUOUS"        # Nordics (degrees), reverse Nordic (cable)
    REP_RATIO = "REP_RATIO"          # pull-up (rep count)


class AssistUnit(str, Enum):
    DEGREES = "DEGREES"
    CABLE_LB = "CABLE_LB"
    TUBE_COUNT = "TUBE_COUNT"
    REP_COUNT = "REP_COUNT"


class LoadUnit(str, Enum):
    LB = "LB"
    LB_PER_HAND = "LB_PER_HAND"
    CABLE_LB = "CABLE_LB"
    DEGREES = "DEGREES"
    TUBE = "TUBE"
    BODYWEIGHT = "BODYWEIGHT"
    NONE = "NONE"


class Phase(str, Enum):
    CALIBRATION = "CALIBRATION"
    CUT = "CUT"
    STAB = "STAB"
    REBUILD = "REBUILD"


class CalibrationStatus(str, Enum):
    INHERITED = "INHERITED"      # carried from V1, RPE-noisy — not yet trusted
    CALIBRATING = "CALIBRATING"  # being measured this block
    MEASURED = "MEASURED"        # clean baseline, engine has authority


class EquipPhase(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class BandCalStatus(str, Enum):
    MODELED = "MODELED"
    MEASURED = "MEASURED"


class KneeModality(str, Enum):
    """Knee-modality classification for cross-session frequency tracking.

    Movements tagged with one of these contribute toward weekly knee-frequency
    targets (Nordic 2×/wk, tib 2×/wk, KOT 2×/wk, sissy 1×/wk per spec §4).
    Most movements have knee_modality == None (not a knee-prioritized lift).
    """
    NORDIC = "NORDIC"   # Nordic hamstring curls
    TIB = "TIB"         # tibialis anterior raises
    KOT = "KOT"         # knees-over-toes (ATG split squat, sissy progressions)
    SISSY = "SISSY"     # sissy squats


# --- session / set-log layer ---

class SessionStatus(str, Enum):
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"


class GroupType(str, Enum):
    STRAIGHT = "STRAIGHT"        # T1 primary, one movement
    GIANT_SET = "GIANT_SET"      # 3 movements, 3 rounds


class SetRole(str, Enum):
    RAMP = "RAMP"
    WARMUP = "WARMUP"
    TOP = "TOP"
    BACKOFF = "BACKOFF"
    WORKING = "WORKING"
    AMRAP = "AMRAP"


class FeedbackTap(str, Enum):
    TOO_EASY = "TOO_EASY"
    ON_TARGET = "ON_TARGET"
    TOO_HARD = "TOO_HARD"


class NoteClass(str, Enum):
    CONFIG_CHANGE = "CONFIG_CHANGE"
    TRANSIENT_FLAG = "TRANSIENT_FLAG"
    PROGRAMMING_REQUEST = "PROGRAMMING_REQUEST"
    JOURNAL = "JOURNAL"
