"""Publish a Program's authoring state as an immutable ProgramRevision.

Validate → materialize → hash → commit, atomically.
See docs/adr/0001-program-library-architecture.md §Revision Execution Model.

NO from __future__ import annotations (project-wide constraint).
"""
from typing import Optional

from sqlmodel import Session, select, func

from ironlog.engine.program_revision_hash import (
    compute_revision_prescription_hash,
    compute_revision_topology_hash,
)
from ironlog.models.library import Movement
from ironlog.models.program import (
    MesoRotation,
    MicrocycleParityRotation,
    Program,
    ProgramDay,
    Tier,
    TierExercise,
)
from ironlog.models.program_revision import (
    ProgramRevision,
    ProgramRevisionDay,
    ProgramRevisionEquipmentRequirement,
    ProgramRevisionExercise,
    ProgramRevisionMesoRotation,
    ProgramRevisionParityRotation,
    ProgramRevisionTier,
    RevisionOrigin,
)


class ProgramValidationError(Exception):
    """Raised when a Program's authoring graph is invalid for publishing."""


def publish_program_revision(
    program: Program,
    db: Session,
) -> ProgramRevision:
    """Validate -> materialize -> hash -> commit, atomically.

    Idempotent by content: returns the existing latest revision if its hash
    matches what publishing now would produce.

    Raises ProgramValidationError if the live authoring graph is invalid.
    """
    # ------------------------------------------------------------------
    # 1. Validate the live authoring graph
    # ------------------------------------------------------------------
    _validate_program(program, db)

    # ------------------------------------------------------------------
    # 2. Compute hashes over the live authoring tables
    # ------------------------------------------------------------------
    prescription_hash = compute_revision_prescription_hash(program)
    topology_hash = compute_revision_topology_hash(program)

    # ------------------------------------------------------------------
    # 3. Idempotency check: does the latest revision already match?
    # ------------------------------------------------------------------
    latest = db.exec(
        select(ProgramRevision)
        .where(ProgramRevision.program_id == program.id)
        .order_by(ProgramRevision.revision_number.desc())  # type: ignore[union-attr]
        .limit(1)
    ).first()

    if (
        latest is not None
        and latest.prescription_hash == prescription_hash
        and latest.topology_hash == topology_hash
    ):
        return latest

    # ------------------------------------------------------------------
    # 4. Materialize a new revision atomically
    # ------------------------------------------------------------------
    next_number = _next_revision_number(program.id, db)

    revision = ProgramRevision(
        program_id=program.id,
        revision_number=next_number,
        revision_origin=RevisionOrigin.PUBLISHED,
        prescription_hash=prescription_hash,
        topology_hash=topology_hash,
    )
    db.add(revision)
    db.flush()  # assigns revision.id

    _materialize_graph(program, revision, db)

    db.commit()
    db.refresh(revision)
    return revision


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_program(program: Program, db: Session) -> None:
    """Validate the live authoring graph is complete and consistent."""
    program_id = program.id
    if program_id is None:
        raise ProgramValidationError("Program has no id (not yet persisted)")

    # Collect valid movement IDs for FK checks
    valid_movement_ids: set[int] = set(
        db.exec(select(Movement.id)).all()  # type: ignore[arg-type]
    )

    days = db.exec(
        select(ProgramDay).where(ProgramDay.program_id == program_id)
    ).all()
    if not days:
        raise ProgramValidationError(
            f"Program {program_id} has no ProgramDay rows"
        )

    for day in days:
        tiers = db.exec(
            select(Tier).where(Tier.program_day_id == day.id)
        ).all()

        # Rest days may have zero tiers — that's fine.
        for tier in tiers:
            exercises = db.exec(
                select(TierExercise).where(TierExercise.tier_id == tier.id)
            ).all()
            if not exercises and not day.is_rest:
                raise ProgramValidationError(
                    f"Tier {tier.id} ('{tier.tier_label}', day_index={day.day_index}) "
                    f"has no TierExercise rows"
                )

            for te in exercises:
                if te.movement_id not in valid_movement_ids:
                    raise ProgramValidationError(
                        f"TierExercise {te.id} (slot '{te.slot_id}') references "
                        f"nonexistent movement_id={te.movement_id}"
                    )

                # Validate MesoRotation movement_ids
                mrs = db.exec(
                    select(MesoRotation).where(
                        MesoRotation.tier_exercise_id == te.id
                    )
                ).all()
                for mr in mrs:
                    if mr.movement_id not in valid_movement_ids:
                        raise ProgramValidationError(
                            f"MesoRotation {mr.id} (tier_exercise_id={te.id}, "
                            f"meso_number={mr.meso_number}) references "
                            f"nonexistent movement_id={mr.movement_id}"
                        )

                # Validate MicrocycleParityRotation movement_ids
                wprs = db.exec(
                    select(MicrocycleParityRotation).where(
                        MicrocycleParityRotation.tier_exercise_id == te.id
                    )
                ).all()
                for wpr in wprs:
                    if wpr.movement_id not in valid_movement_ids:
                        raise ProgramValidationError(
                            f"MicrocycleParityRotation {wpr.id} "
                            f"(tier_exercise_id={te.id}, "
                            f"week_parity='{wpr.week_parity}') references "
                            f"nonexistent movement_id={wpr.movement_id}"
                        )


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------

def _next_revision_number(program_id: int, db: Session) -> int:
    """Return the next revision_number for this program (1-based)."""
    result = db.exec(
        select(func.max(ProgramRevision.revision_number)).where(
            ProgramRevision.program_id == program_id
        )
    ).first()
    return (result or 0) + 1


def _materialize_graph(
    program: Program,
    revision: ProgramRevision,
    db: Session,
) -> None:
    """Copy the live authoring graph into revision-scoped tables."""
    program_id = program.id

    days = db.exec(
        select(ProgramDay).where(ProgramDay.program_id == program_id)
    ).all()

    for day in days:
        rev_day = ProgramRevisionDay(
            program_revision_id=revision.id,
            day_index=day.day_index,
            day_role=day.day_role,
            is_rest=day.is_rest,
            warmup_config=day.warmup_config,
        )
        db.add(rev_day)
        db.flush()

        tiers = db.exec(
            select(Tier).where(Tier.program_day_id == day.id)
        ).all()

        # Build a mapping from authoring Tier.id -> revision ProgramRevisionTier.id
        # for resolving paired_tier references.
        authoring_tier_id_to_rev_tier: dict[int, ProgramRevisionTier] = {}

        for tier in tiers:
            rev_tier = ProgramRevisionTier(
                program_revision_day_id=rev_day.id,
                # paired_program_revision_tier_id is set in a second pass below
                paired_program_revision_tier_id=None,
                tier_label=tier.tier_label,
                tier_order=tier.tier_order,
                tier_kind=tier.tier_kind,
                rest_seconds=tier.rest_seconds,
                rounds=tier.rounds,
                shoe=tier.shoe,
            )
            db.add(rev_tier)
            db.flush()
            authoring_tier_id_to_rev_tier[tier.id] = rev_tier

        # Second pass: resolve paired_tier references within this day's tiers.
        for tier in tiers:
            if tier.paired_tier_id is not None:
                rev_tier = authoring_tier_id_to_rev_tier[tier.id]
                paired_rev_tier = authoring_tier_id_to_rev_tier.get(
                    tier.paired_tier_id
                )
                if paired_rev_tier is not None:
                    rev_tier.paired_program_revision_tier_id = paired_rev_tier.id
                    db.add(rev_tier)

        # Materialize exercises under each tier.
        for tier in tiers:
            rev_tier = authoring_tier_id_to_rev_tier[tier.id]
            exercises = db.exec(
                select(TierExercise).where(TierExercise.tier_id == tier.id)
            ).all()

            for te in exercises:
                rev_exercise = ProgramRevisionExercise(
                    program_revision_tier_id=rev_tier.id,
                    slot_id=te.slot_id,
                    movement_id=te.movement_id,
                    exercise_order=te.exercise_order,
                    tier_role=te.tier_role,
                    pattern=te.pattern,
                    knee_modality=te.knee_modality,
                    rep_low=te.rep_low,
                    rep_high=te.rep_high,
                    duration_low_seconds=te.duration_low_seconds,
                    duration_high_seconds=te.duration_high_seconds,
                    rpe_cap=te.rpe_cap,
                    scheme=te.scheme,
                    unified_ht_group=te.unified_ht_group,
                    derived_from_unified_group=te.derived_from_unified_group,
                    derive_ratio=te.derive_ratio,
                )
                db.add(rev_exercise)
                db.flush()

                # MesoRotation rows (meso_number-keyed only)
                mrs = db.exec(
                    select(MesoRotation).where(
                        MesoRotation.tier_exercise_id == te.id,
                        MesoRotation.mesocycle_id.is_(None),  # type: ignore[union-attr]
                    )
                ).all()
                for mr in mrs:
                    db.add(ProgramRevisionMesoRotation(
                        program_revision_exercise_id=rev_exercise.id,
                        meso_number=mr.meso_number,
                        movement_id=mr.movement_id,
                        rep_low=mr.rep_low,
                        rep_high=mr.rep_high,
                    ))

                # MicrocycleParityRotation rows
                wprs = db.exec(
                    select(MicrocycleParityRotation).where(
                        MicrocycleParityRotation.tier_exercise_id == te.id,
                    )
                ).all()
                for wpr in wprs:
                    db.add(ProgramRevisionParityRotation(
                        program_revision_exercise_id=rev_exercise.id,
                        week_parity=wpr.week_parity,
                        movement_id=wpr.movement_id,
                        rep_low=wpr.rep_low,
                        rep_high=wpr.rep_high,
                    ))

    db.flush()
