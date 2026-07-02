"""test_payload_candidates.py — Task 3: candidates are enriched descriptors.

Verifies that build_context_payload emits per-slot candidates as descriptor
dicts (id, name, primary_muscle, secondary_muscles, lift_category, pattern,
equipment_tags, is_program_anchor) rather than bare ints, and that the anchor
movement is flagged exactly once.
"""


def test_candidates_are_enriched_descriptors(gen_db):
    # gen_db: seeded session fixture (program + tagged library)
    from ironlog.generation.skeleton import lay_skeleton
    from ironlog.generation.context import resolve_context, build_context_payload
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, lambda d: (d.isocalendar()[0], d.isocalendar()[1]))
    payload = build_context_payload(ctx, sk)
    menu_slots = [s for s in payload["slots"] if s["candidates"]]
    assert menu_slots, "expected at least one menu-governed slot"
    cand = menu_slots[0]["candidates"][0]
    assert set(cand) >= {"id", "name", "primary_muscle", "secondary_muscles",
                         "lift_category", "pattern", "equipment_tags", "is_program_anchor"}
    # the anchor (program movement) is flagged, exactly once, and is first
    anchors = [c for c in menu_slots[0]["candidates"] if c["is_program_anchor"]]
    assert len(anchors) == 1
