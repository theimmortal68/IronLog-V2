"""ht_refine.py — Task 5: single-band felt-peak refines the band model.

When a logged HT (Hip Thrust band-composite) set used a SINGLE band, its
felt-peak reading is a clean signal for that band's true peak resistance —
`observed = felt_peak - actual_plates` isolates the band's contribution with
no other unknowns in the way. A multi-band (stacked) reading can't be
decomposed into per-band contributions, so those sets are skipped entirely.

refine_from_logged_ht(session_id, db) is called from the submit path
(api/app.py submit_session) after that session's SetLogs are committed. It
nudges each qualifying band's BandPair.peak_lb toward the observed value via
an EMA (peak_lb = round(0.7*peak_lb + 0.3*observed, 1)), and — once a band
has accumulated N=3 qualifying single-band readings across ALL sessions ever
logged (not just this one) — flips BandPair.calibration_status from MODELED
to MEASURED. The reading count is derived by re-scanning history each call
(no new counter column / migration needed): a SetLog qualifies for a band iff
it has a non-null felt_peak and its resolved config is that single band.

Band-config resolution per SetLog: PlannedSet.band_config (the Task-1
multi-band JSON list) if present and non-empty; else PlannedSet.band_pair_id
(the pre-band_config single-band field) wrapped as a one-element list; else
SetLog.band_pair_id (for unlinked/planned_set_id-less logs) wrapped the same
way. A config is "single-band" iff it resolves to exactly one band id.

Never touches current_load/ht_plates/ht_band_config — those are Option-C's
generation-time (assembler/commit_session) fields, a completely different
concern from BandPair.peak_lb (inventory calibration).

NO from __future__ import annotations (project-wide constraint).
"""
from typing import List, Optional

from sqlmodel import Session as DBSession
from sqlmodel import col, select

from ..models.enums import BandCalStatus
from ..models.library import BandPair
from ..models.session import PlannedSet, SetLog

CONSISTENT_READINGS_TO_MEASURE = 3


def _resolved_band_config(sl: SetLog, ps: Optional[PlannedSet]) -> Optional[List[int]]:
    """The set's band configuration, PlannedSet.band_config first, falling
    back to the older singular band_pair_id fields. None if unresolvable."""
    if ps is not None and ps.band_config:
        return ps.band_config
    if ps is not None and ps.band_pair_id is not None:
        return [ps.band_pair_id]
    if sl.band_pair_id is not None:
        return [sl.band_pair_id]
    return None


def _load_planned_sets(db: DBSession, set_logs: List[SetLog]) -> dict:
    planned_set_ids = [sl.planned_set_id for sl in set_logs if sl.planned_set_id is not None]
    planned_sets: dict = {}
    if planned_set_ids:
        for ps in db.exec(
            select(PlannedSet).where(col(PlannedSet.id).in_(planned_set_ids))
        ).all():
            planned_sets[ps.id] = ps
    return planned_sets


def _count_single_band_readings(db: DBSession, band_id: int) -> int:
    """How many logged sets (across ALL sessions ever) resolve to a
    single-band config equal to `band_id` and carry a non-null felt_peak."""
    all_logs = db.exec(select(SetLog).where(col(SetLog.felt_peak).is_not(None))).all()
    planned_sets = _load_planned_sets(db, all_logs)
    count = 0
    for sl in all_logs:
        ps = planned_sets.get(sl.planned_set_id) if sl.planned_set_id else None
        config = _resolved_band_config(sl, ps)
        if config is not None and len(config) == 1 and config[0] == band_id:
            count += 1
    return count


def refine_from_logged_ht(session_id: int, db: DBSession) -> None:
    """Refine BandPair.peak_lb from this session's single-band HT logs.

    For each logged SetLog in `session_id` with a non-null felt_peak whose
    resolved band config has exactly one band: pull actual_plates (falling
    back to the PlannedSet's target_plates), compute the observed peak, and
    nudge that BandPair's peak_lb toward it via an EMA. Multi-band sets, and
    sets with no resolvable config, are skipped — can't isolate the signal.
    Bands touched this call are then checked against the N=3 threshold for a
    MODELED -> MEASURED calibration_status flip.
    """
    set_logs = db.exec(select(SetLog).where(SetLog.session_id == session_id)).all()
    ht_logs = [sl for sl in set_logs if sl.felt_peak is not None]
    if not ht_logs:
        return

    planned_sets = _load_planned_sets(db, ht_logs)

    touched_band_ids: set = set()
    for sl in ht_logs:
        ps = planned_sets.get(sl.planned_set_id) if sl.planned_set_id else None
        config = _resolved_band_config(sl, ps)
        if config is None or len(config) != 1:
            continue  # multi-band (or unresolvable): can't isolate, skip

        band_id = config[0]
        band = db.get(BandPair, band_id)
        if band is None:
            continue

        actual_plates = sl.actual_plates
        if actual_plates is None and ps is not None:
            actual_plates = ps.target_plates
        if actual_plates is None:
            continue  # no plates reference at all: can't compute observed peak

        observed = sl.felt_peak - actual_plates
        band.peak_lb = round(0.7 * band.peak_lb + 0.3 * observed, 1)
        db.add(band)
        touched_band_ids.add(band_id)

    if not touched_band_ids:
        return

    db.flush()
    for band_id in touched_band_ids:
        if _count_single_band_readings(db, band_id) >= CONSISTENT_READINGS_TO_MEASURE:
            band = db.get(BandPair, band_id)
            if band.calibration_status != BandCalStatus.MEASURED:
                band.calibration_status = BandCalStatus.MEASURED
                db.add(band)

    db.commit()
