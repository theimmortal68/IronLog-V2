# Muscle-Aware Reasoned Deviation Payload — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the runtime proposer enough information and explicit instructions to make the best possible deviation decision, instead of choosing blind from bare integer IDs with no guidance.

**Architecture:** Add a muscle taxonomy (enum + two `Movement` columns) and tag the library; overhaul `build_context_payload` to emit enriched candidate descriptors, a typed/severity/limiter stall record, phase-intent, and per-slot rep-scheme (all sourced from an enriched `GenerationContext` so the payload builder stays pure data-in/data-out); give `GeminiProposer` a coaching `systemInstruction` and dynamic thinking. Validate with the free agy cross-vendor slate against the real enriched payload.

**Tech Stack:** Python, SQLModel/SQLAlchemy, FastAPI, pytest. Migrations via `ironlog/migrate.py` + `deploy/migrations/NNN_*.sql`. Gemini Generative Language API (`generateContent`).

## Global Constraints

- NO `from __future__ import annotations` (project-wide).
- BUILD-AND-TEST-ONLY: never run `python -m ironlog.seed` against prod; tests run in-memory / on myflix; the live DB is touched only by gated, backup-first migrations.
- Migration rule: single-statement-atomic OR idempotent (`IF NOT EXISTS` / guarded `WHERE`); update the parity keystone test `tests/test_migrations.py::test_chain_matches_create_all`.
- Two-writer boundary: this work is read-only context construction + a library schema/data addition; it must NOT write `current_load` or outcome fields.
- Tests on myflix ONLY: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'`.
- Baseline: 266 tests passing (branch `feat/payload-enrichment` off `main` @ 336ca58).
- Selections-only boundary preserved: the proposer picks movements/variants/technique; it never computes loads or reps.

---

### Task 1: Muscle taxonomy — enum + Movement columns + migration

**Files:**
- Modify: `ironlog/models/enums.py` (add `Muscle`)
- Modify: `ironlog/models/library.py` (Movement: `primary_muscle`, `secondary_muscles`)
- Create: `deploy/migrations/010_add_movement_muscles.sql`
- Test: `tests/test_migrations.py` (parity), `tests/test_library_muscle_fields.py` (new)

**Interfaces:**
- Produces: `Muscle` enum (str values); `Movement.primary_muscle: Optional[Muscle]`; `Movement.secondary_muscles: List[str]` (JSON, mirrors `equipment_tags`).

- [ ] **Step 1: Write the failing test** — `tests/test_library_muscle_fields.py`

```python
from ironlog.models.enums import Muscle
from ironlog.models.library import Movement


def test_movement_has_muscle_fields_defaulting_empty():
    m = Movement(name="X [DB]", base_name="X")
    assert m.primary_muscle is None
    assert m.secondary_muscles == []


def test_muscle_enum_has_expected_members():
    expected = {
        "UPPER_CHEST", "MID_LOWER_CHEST", "LATS", "MID_BACK", "UPPER_TRAPS",
        "FRONT_DELT", "SIDE_DELT", "REAR_DELT", "BICEPS", "TRICEPS", "FOREARMS",
        "QUADS", "HAMSTRINGS", "GLUTES", "ADDUCTORS", "CALVES", "ABS",
        "SPINAL_ERECTORS",
    }
    assert {m.value for m in Muscle} == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_library_muscle_fields.py -q'`
Expected: FAIL (`ImportError: cannot import name 'Muscle'`).

- [ ] **Step 3: Add the `Muscle` enum** to `ironlog/models/enums.py`

```python
class Muscle(str, Enum):
    UPPER_CHEST = "UPPER_CHEST"
    MID_LOWER_CHEST = "MID_LOWER_CHEST"
    LATS = "LATS"
    MID_BACK = "MID_BACK"
    UPPER_TRAPS = "UPPER_TRAPS"
    FRONT_DELT = "FRONT_DELT"
    SIDE_DELT = "SIDE_DELT"
    REAR_DELT = "REAR_DELT"
    BICEPS = "BICEPS"
    TRICEPS = "TRICEPS"
    FOREARMS = "FOREARMS"
    QUADS = "QUADS"
    HAMSTRINGS = "HAMSTRINGS"
    GLUTES = "GLUTES"
    ADDUCTORS = "ADDUCTORS"
    CALVES = "CALVES"
    ABS = "ABS"
    SPINAL_ERECTORS = "SPINAL_ERECTORS"
```

- [ ] **Step 4: Add the two columns** to `Movement` in `ironlog/models/library.py` (place beside `equipment_tags`; import `Muscle`)

```python
    primary_muscle: Optional[Muscle] = None
    secondary_muscles: List[str] = Field(default_factory=list, sa_column=Column(JSON))
```

- [ ] **Step 5: Run the new test to verify it passes**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_library_muscle_fields.py -q'`
Expected: PASS.

- [ ] **Step 6: Create the migration** `deploy/migrations/010_add_movement_muscles.sql` (mirrors 009's additive-nullable pattern)

```sql
ALTER TABLE movement ADD COLUMN primary_muscle VARCHAR;
ALTER TABLE movement ADD COLUMN secondary_muscles JSON DEFAULT '[]';
```

- [ ] **Step 7: Run the parity + migration suite**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_migrations.py -q'`
Expected: PASS (`test_chain_matches_create_all` confirms the migration chain matches `create_all`). If it fails on column type/order, align the SQL column type with what SQLModel emits for these fields.

- [ ] **Step 8: Full suite + commit**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q 2>&1 | tail -3'`
Expected: 266 + 2 new pass, 0 failed.

```bash
git add ironlog/models/enums.py ironlog/models/library.py deploy/migrations/010_add_movement_muscles.sql tests/test_library_muscle_fields.py tests/test_migrations.py
git commit -m "feat(library): add Muscle taxonomy + Movement primary/secondary muscle fields (migration 010)"
```

---

### Task 2: Tag the 108 movements (data pass — HUMAN REVIEW GATE)

**Files:**
- Modify: `ironlog/seed.py` (the `MOVEMENTS` list dicts gain `primary_muscle` + `secondary_muscles`; loader at ~`:700` maps them)
- Create: `deploy/migrations/011_backfill_movement_muscles.sql` (generated from the reviewed tags)
- Create: `scripts/propose_muscle_tags.py` (one-off proposer), `scripts/gen_muscle_backfill.py` (sql generator)
- Test: `tests/test_library_muscle_tags.py`

**Interfaces:**
- Consumes: `Muscle` enum (Task 1).
- Produces: every `MOVEMENTS` dict has a `primary_muscle` (valid `Muscle` value) and `secondary_muscles` (list of valid `Muscle` values); migration 011 backfills the live DB idempotently.

> **This task has a human-in-the-loop review gate.** The subagent generates *proposed* tags; the controller surfaces them to the user; the user corrects; only then are tags committed. Do not auto-commit unreviewed tags.

- [ ] **Step 1: Write the failing test** — `tests/test_library_muscle_tags.py`

```python
from ironlog.models.enums import Muscle
from ironlog.seed import MOVEMENTS

_VALID = {m.value for m in Muscle}


def test_every_movement_has_a_valid_primary_muscle():
    missing = [m["name"] for m in MOVEMENTS if not m.get("primary_muscle")]
    assert missing == [], f"untagged: {missing}"
    bad = [m["name"] for m in MOVEMENTS if m["primary_muscle"] not in _VALID]
    assert bad == [], f"invalid primary: {bad}"


def test_secondary_muscles_are_valid_and_listy():
    for m in MOVEMENTS:
        sec = m.get("secondary_muscles", [])
        assert isinstance(sec, list)
        assert all(s in _VALID for s in sec), f"{m['name']}: {sec}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_library_muscle_tags.py -q'`
Expected: FAIL (movements untagged).

- [ ] **Step 3: Generate proposed tags** — `scripts/propose_muscle_tags.py`

Reads each `MOVEMENTS` dict, emits a proposal `{name, base_name, lift_category, pattern, region, proposed_primary, proposed_secondary}` to `scripts/muscle_tags_proposed.json`. Heuristic mapping from `lift_category`/`base_name` keywords (e.g. `ROW`→primary `MID_BACK`, secondary `[LATS, REAR_DELT, BICEPS]`; `BENCH`→`MID_LOWER_CHEST` + `[FRONT_DELT, TRICEPS]`; lateral-raise→`SIDE_DELT`; etc.). This is a *proposal*, not the source of truth.

```bash
ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/python scripts/propose_muscle_tags.py'
```

- [ ] **Step 4: HUMAN REVIEW GATE** — controller surfaces `scripts/muscle_tags_proposed.json` to the user; the user corrects primary/secondary per movement. Apply the corrected tags into each `MOVEMENTS` dict in `ironlog/seed.py` (add `"primary_muscle": "...", "secondary_muscles": [...]`).

- [ ] **Step 5: Wire the loader** — at `ironlog/seed.py:~700` where `Movement(...)` is built, add:

```python
                primary_muscle=m.get("primary_muscle"),
                secondary_muscles=m.get("secondary_muscles", []),
```

- [ ] **Step 6: Run the tag test to verify it passes**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_library_muscle_tags.py -q'`
Expected: PASS (all 108 tagged, all valid).

- [ ] **Step 7: Generate the idempotent backfill migration** — `scripts/gen_muscle_backfill.py` reads `MOVEMENTS` and writes `deploy/migrations/011_backfill_movement_muscles.sql` with one guarded UPDATE per movement:

```sql
UPDATE movement SET primary_muscle='MID_BACK', secondary_muscles='["LATS","REAR_DELT","BICEPS"]'
  WHERE name='Pendlay Row - Narrow [OB]' AND primary_muscle IS NULL;
-- ... one per movement; each idempotent via the IS NULL guard
```

```bash
ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/python scripts/gen_muscle_backfill.py'
```

- [ ] **Step 8: Parity + full suite + commit**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_migrations.py -q && .venv/bin/pytest -q 2>&1 | tail -3'`
Expected: parity green; full suite 0 failed.

```bash
git add ironlog/seed.py scripts/propose_muscle_tags.py scripts/gen_muscle_backfill.py deploy/migrations/011_backfill_movement_muscles.sql tests/test_library_muscle_tags.py
git commit -m "feat(library): tag 108 movements with primary/secondary muscles + idempotent backfill (migration 011)"
```

---

### Task 3: Enriched candidate descriptors in the payload

**Files:**
- Modify: `ironlog/generation/context.py` (`GenerationContext` gains `movements`; `build_context_payload` enriches candidates)
- Test: `tests/test_payload_candidates.py`

**Interfaces:**
- Consumes: `Movement` muscle fields (Task 1); `ctx.candidate_menus: Dict[str, List[int]]`.
- Produces: payload `slots[].candidates` is a list of `{id, name, primary_muscle, secondary_muscles, lift_category, pattern, equipment_tags, is_program_anchor}`. `GenerationContext.movements: Dict[int, Movement]`.

- [ ] **Step 1: Write the failing test** — `tests/test_payload_candidates.py` (use the existing generation fixture that seeds a program + library; pick a day with menu-governed slots)

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_payload_candidates.py -q'`
Expected: FAIL (candidates are bare ints).

- [ ] **Step 3: Store movements on the context** — in `resolve_context` (it already builds `movements = {m.id: m for ...}`), add to the `GenerationContext(...)` return: `movements=movements`. Add the field to the dataclass:

```python
    movements: Dict[int, "Movement"] = field(default_factory=dict)
```

- [ ] **Step 4: Add the enrichment helper + use it** in `context.py`

```python
def _candidate_descriptor(mid: int, slot: SlotSpec, ctx: "GenerationContext") -> dict:
    m = ctx.movements.get(mid)
    return {
        "id": mid,
        "name": m.name if m else str(mid),
        "primary_muscle": m.primary_muscle.value if (m and m.primary_muscle) else None,
        "secondary_muscles": list(m.secondary_muscles) if m else [],
        "lift_category": m.lift_category.value if m else None,
        "pattern": slot.pattern,
        "equipment_tags": list(m.equipment_tags) if m else [],
        "is_program_anchor": mid == slot.program_movement_id,
    }
```

In `build_context_payload`, replace `"candidates": ctx.candidate_menus.get(s.slot_id, [])` with:

```python
                "candidates": [
                    _candidate_descriptor(mid, s, ctx)
                    for mid in ctx.candidate_menus.get(s.slot_id, [])
                ],
```

- [ ] **Step 4b: Verify membership unchanged** — `build_candidate_menu` and `check_menu_membership` still operate on IDs. Grep to confirm no caller reads `payload["slots"][...]["candidates"]` as ints:

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && grep -rn "candidates" ironlog/generation/repair.py ironlog/generation/loop.py'`
Expected: membership uses `ctx.candidate_menus` (IDs), not the payload — no change needed.

- [ ] **Step 5: Run + commit**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_payload_candidates.py -q && .venv/bin/pytest -q 2>&1 | tail -3'`
Expected: new test PASS; full suite 0 failed.

```bash
git add ironlog/generation/context.py tests/test_payload_candidates.py
git commit -m "feat(gen): enrich payload candidates with name+muscles+pattern+anchor flag (gap B)"
```

---

### Task 4: Typed/severity/limiter stall record

**Files:**
- Modify: `ironlog/generation/context.py` (`build_weak_point_hints` return shape; `GenerationContext.weak_point_hints` type)
- Test: `tests/test_stall_record.py`; adjust any existing test asserting the old string shape.

**Interfaces:**
- Consumes: `detect_stall`, `select_progress_window`, `consecutive_failed_progressions`, Movement muscle tags (Task 1/2).
- Produces: `weak_point_hints: Dict[int, dict]` where each value is
  `{"stall_type": "failed"|"trend"|"both", "failed_count": int, "e1rm_window": {"sessions": int, "peak": float|None, "latest": float|None}, "limiter": {"primary_muscle": str|None, "secondary_muscles": [str]}}`.
- Behavior preserved: `slot_has_deviation_signal`/`should_invoke_llm` still fire on *presence* of a record for the slot's `program_movement_id` (they check `mid in ctx.weak_point_hints`, which still works on the dict's keys).

- [ ] **Step 1: Write the failing test** — `tests/test_stall_record.py`

```python
def test_failed_stall_record_shape(stalled_gen_db):
    # fixture: a movement with consecutive_failed_progressions >= threshold, tagged
    from ironlog.generation.context import build_weak_point_hints
    rec = build_weak_point_hints(stalled_gen_db)
    assert rec, "expected at least one stalled movement"
    mid, r = next(iter(rec.items()))
    assert r["stall_type"] in ("failed", "trend", "both")
    assert isinstance(r["failed_count"], int)
    assert set(r["e1rm_window"]) == {"sessions", "peak", "latest"}
    assert set(r["limiter"]) == {"primary_muscle", "secondary_muscles"}


def test_should_invoke_llm_still_fires_on_record_presence(stalled_gen_db):
    from ironlog.generation.skeleton import lay_skeleton
    from ironlog.generation.context import resolve_context, should_invoke_llm
    sk = lay_skeleton("D1 Upper Push", stalled_gen_db)
    ctx = resolve_context("D1 Upper Push", sk, stalled_gen_db, lambda d: (d.isocalendar()[0], d.isocalendar()[1]))
    assert should_invoke_llm(sk, ctx) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_stall_record.py -q'`
Expected: FAIL (record is a string, has no `stall_type`).

- [ ] **Step 3: Rewrite `build_weak_point_hints`** in `context.py`

```python
def build_weak_point_hints(db: Session) -> Dict[int, dict]:
    """Per stalled movement: typed + severity + limiter record (gap D)."""
    records: Dict[int, dict] = {}
    movements = {m.id: m for m in db.exec(select(Movement)).all()}
    states = db.exec(select(MovementState)).all()
    for st in states:
        rows = db.exec(
            select(E1rmHistory).where(E1rmHistory.movement_id == st.movement_id)
        ).all()
        window = select_progress_window(list(rows))
        sig = detect_stall(window, st.consecutive_failed_progressions, Objective.PROGRESS)
        if not sig.stalled:
            continue
        if sig.trend_stalled and sig.failed_stalled:
            stype = "both"
        elif sig.trend_stalled:
            stype = "trend"
        else:
            stype = "failed"
        m = movements.get(st.movement_id)
        records[st.movement_id] = {
            "stall_type": stype,
            "failed_count": st.consecutive_failed_progressions,
            "e1rm_window": {
                "sessions": len(window),
                "peak": max(window) if window else None,
                "latest": window[-1] if window else None,
            },
            "limiter": {
                "primary_muscle": m.primary_muscle.value if (m and m.primary_muscle) else None,
                "secondary_muscles": list(m.secondary_muscles) if m else [],
            },
        }
    return records
```

- [ ] **Step 4: Update the type annotation** on `GenerationContext.weak_point_hints` from `Dict[int, str]` to `Dict[int, dict]`. `slot_has_deviation_signal` (`mid in ctx.weak_point_hints`) is unchanged — confirm by reading it.

- [ ] **Step 5: Fix any existing test** asserting the old string. Grep and update:

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && grep -rn "weak_point_hints\|bias accessory volume" tests/'`
Update assertions that expected the string to assert the dict shape instead.

- [ ] **Step 6: Run + commit**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_stall_record.py -q && .venv/bin/pytest -q 2>&1 | tail -3'`
Expected: new tests PASS; full suite 0 failed.

```bash
git add ironlog/generation/context.py tests/
git commit -m "feat(gen): typed/severity/limiter stall record replacing generic hint string (gap D)"
```

---

### Task 5: Phase-intent + per-slot rep-scheme

**Files:**
- Modify: `ironlog/generation/context.py` (`resolve_context` builds `slot_rep_schemes`; `build_context_payload` adds `phase_intent` + per-slot `rep_scheme`); `GenerationContext` gains `slot_rep_schemes`
- Test: `tests/test_payload_phase_rep.py`

**Interfaces:**
- Consumes: `ctx.phase_policy` (already present), `TierExercise.rep_low/rep_high/scheme`, `slot.program_movement_id`.
- Produces: payload `phase_intent: {objective, rpe_band: [low, high], volume_posture}`; each `slots[]` gains `rep_scheme: {rep_low, rep_high, scheme}` (or `None` if no matching TierExercise). `GenerationContext.slot_rep_schemes: Dict[str, dict]`.

- [ ] **Step 1: Write the failing test** — `tests/test_payload_phase_rep.py`

```python
def test_payload_has_phase_intent_and_slot_rep_scheme(gen_db):
    from ironlog.generation.skeleton import lay_skeleton
    from ironlog.generation.context import resolve_context, build_context_payload
    sk = lay_skeleton("D1 Upper Push", gen_db)
    ctx = resolve_context("D1 Upper Push", sk, gen_db, lambda d: (d.isocalendar()[0], d.isocalendar()[1]))
    p = build_context_payload(ctx, sk)
    assert set(p["phase_intent"]) == {"objective", "rpe_band", "volume_posture"}
    assert len(p["phase_intent"]["rpe_band"]) == 2
    assert all("rep_scheme" in s for s in p["slots"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_payload_phase_rep.py -q'`
Expected: FAIL (`KeyError: 'phase_intent'`).

- [ ] **Step 3: Build `slot_rep_schemes` in `resolve_context`** (after menus). Look up the `TierExercise` for each adaptive slot by `program_movement_id` within the active program:

```python
    from ..models.program import TierExercise
    te_by_mid = {
        te.movement_id: te
        for te in db.exec(select(TierExercise)).all()
    }
    slot_rep_schemes: Dict[str, dict] = {}
    for slot in skeleton.adaptive_slots:
        te = te_by_mid.get(slot.program_movement_id)
        if te is not None:
            slot_rep_schemes[slot.slot_id] = {
                "rep_low": te.rep_low, "rep_high": te.rep_high, "scheme": te.scheme,
            }
```

Add `slot_rep_schemes=slot_rep_schemes` to the `GenerationContext(...)` return and the dataclass field:

```python
    slot_rep_schemes: Dict[str, dict] = field(default_factory=dict)
```

- [ ] **Step 4: Add `phase_intent` + per-slot `rep_scheme` to `build_context_payload`**

```python
        "phase_intent": {
            "objective": ctx.phase_policy.default_objective.value,
            "rpe_band": [ctx.phase_policy.rpe_band_low, ctx.phase_policy.rpe_band_high],
            "volume_posture": ctx.phase_policy.volume_posture,
        },
```

and inside each slot dict:

```python
                "rep_scheme": ctx.slot_rep_schemes.get(s.slot_id),
```

- [ ] **Step 5: Run + commit**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_payload_phase_rep.py -q && .venv/bin/pytest -q 2>&1 | tail -3'`
Expected: new test PASS; full suite 0 failed.

```bash
git add ironlog/generation/context.py tests/test_payload_phase_rep.py
git commit -m "feat(gen): add phase_intent + per-slot rep_scheme to payload (gap G; informational only)"
```

---

### Task 6: Coaching instruction prompt + proposer call (gaps A + C)

**Files:**
- Modify: `ironlog/generation/proposer.py` (add `PROPOSER_SYSTEM_INSTRUCTION`)
- Modify: `ironlog/generation/gemini.py` (`propose` adds `systemInstruction` + dynamic thinking)
- Test: `tests/test_proposer_system_instruction.py`

**Interfaces:**
- Consumes: nothing new (constant + body change).
- Produces: `PROPOSER_SYSTEM_INSTRUCTION: str`; `propose` body carries `systemInstruction.parts[0].text == PROPOSER_SYSTEM_INSTRUCTION` and `generationConfig.thinkingConfig.thinkingBudget == -1`.

- [ ] **Step 1: Write the failing test** — `tests/test_proposer_system_instruction.py` (inject a fake client capturing the posted body; no network)

```python
import json
from ironlog.generation.gemini import GeminiProposer
from ironlog.generation.proposer import PROPOSER_SYSTEM_INSTRUCTION, SELECTIONS_JSON_SCHEMA


class _CapturingClient:
    def __init__(self):
        self.body = None
    def post(self, url, json=None, headers=None):
        self.body = json
        class _R:
            def raise_for_status(self_): pass
            def json(self_):
                return {"candidates": [{"content": {"parts": [{"text":
                    '{"ordering": [], "slots": [], "rationale": "ok"}'}]}}]}
        return _R()


def test_propose_sends_system_instruction_and_dynamic_thinking():
    cap = _CapturingClient()
    gp = GeminiProposer(api_key="x", http=cap)
    gp.propose({"day_role": "D1", "slots": []})
    assert cap.body["systemInstruction"]["parts"][0]["text"] == PROPOSER_SYSTEM_INSTRUCTION
    assert cap.body["generationConfig"]["thinkingConfig"]["thinkingBudget"] == -1


def test_system_instruction_encodes_policy_c_keywords():
    t = PROPOSER_SYSTEM_INSTRUCTION.lower()
    assert "failed" in t and "trend" in t and "limiter" in t
    assert "load" in t  # selections-only boundary mentions never computing loads
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_proposer_system_instruction.py -q'`
Expected: FAIL (`ImportError: PROPOSER_SYSTEM_INSTRUCTION`).

- [ ] **Step 3: Add the constant** to `ironlog/generation/proposer.py`

```python
PROPOSER_SYSTEM_INSTRUCTION = """You are a strength & hypertrophy coach selecting per-slot movements for one training session. You are invoked only when a slot carries a deviation signal (almost always a stall).

DECISION POLICY (apply per signalled slot):
- The program movement (the candidate with is_program_anchor=true) is the default. Keep it unless an alternative in the same slot's candidate menu better addresses the stall's limiter.
- FAILED-progression stall (stall_type "failed"): keep the movement; the engine will adjust loading. Do not swap for its own sake.
- TREND plateau (stall_type "trend"): prefer swapping to a same-pattern alternative that provides a novel stimulus for the limiter muscle.
- "both": treat as trend-dominant unless failed_count is high.
- Scale the response to severity (failed_count, how flat/declining the e1rm_window is).
- When swapping, choose the candidate whose primary_muscle / secondary_muscles best match the stalled movement's limiter.

FIELD GLOSSARY:
- candidates: the only legal picks for a slot; each has name, primary_muscle, secondary_muscles, lift_category, pattern, equipment_tags, is_program_anchor.
- weak_point_hints[movement_id]: the stall record (stall_type, failed_count, e1rm_window, limiter).
- owed: weekly requirements (knee frequency, pull/push ratio).
- phase_intent: the training phase's objective, RPE band, and volume posture.
- rep_scheme: the slot's target rep range (context only).

BOUNDARY: select movements, variants, and technique tags ONLY. Never compute or set loads, weights, or reps — the engine owns all numbers. Output must conform exactly to the provided JSON schema."""
```

- [ ] **Step 4: Wire it into `propose`** in `ironlog/generation/gemini.py` — import the constant; add to `body` and set the thinking budget:

```python
from .proposer import PROPOSER_SYSTEM_INSTRUCTION, SELECTIONS_JSON_SCHEMA, Selections, selections_from_dict
```

```python
        body = {
            "systemInstruction": {"parts": [{"text": PROPOSER_SYSTEM_INSTRUCTION}]},
            "contents": [{"role": "user", "parts": [{"text": json.dumps(payload)}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": SELECTIONS_JSON_SCHEMA,
                "thinkingConfig": {"thinkingBudget": -1},
            },
        }
```

(If `SELECTIONS_JSON_SCHEMA` was previously imported in gemini.py, keep a single import; don't duplicate.)

- [ ] **Step 5: Run + commit**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest tests/test_proposer_system_instruction.py -q && .venv/bin/pytest -q 2>&1 | tail -3'`
Expected: new tests PASS; full suite 0 failed (existing GeminiProposer mocked tests still green — they inject `http`, so the new body keys don't break them).

```bash
git add ironlog/generation/proposer.py ironlog/generation/gemini.py tests/test_proposer_system_instruction.py
git commit -m "feat(gen): coaching systemInstruction (policy c) + dynamic thinking on proposer call (gaps A+C)"
```

---

### Task 7: Verification — agy cross-vendor slate against the real enriched payload

**Files:**
- Create: `scripts/eval_enriched_slate.py` (eval harness; not shipped logic)

**Interfaces:**
- Consumes: the production `build_context_payload` (Tasks 3–5) + `GeminiProposer` (Task 6).

> This is a verification task, not a TDD unit. It runs the free agy slate against the *real* enriched payload and asserts the quality bar.

- [ ] **Step 1: Build the enriched-payload eval harness** — on myflix, copy `ironlog.db` to a throwaway, seed loads + plant stalls (D1/D4/D6), build the **real** `build_context_payload`, and for each model in {flash-lite (API), Gemini 3.1 Pro, Claude Opus 4.6, Claude Sonnet 4.6, GPT-OSS 120B} produce Selections. flash-lite via `GeminiProposer`; the agy models via `agy --sandbox --model "<model>" -p "<systemInstruction + payload>"` (OAuth, free). Reuse the prior slate script shape; the only change is the payload now comes from production `build_context_payload` (already enriched), and the prompt includes `PROPOSER_SYSTEM_INSTRUCTION`.

- [ ] **Step 2: Run the slate (background; thinking models are slow)**

```bash
ssh myflix 'cd ~/projects/IronLog-V2 && set -a && . ./.env && set +a && .venv/bin/python scripts/eval_enriched_slate.py > /tmp/enriched_slate.txt 2>&1'
```

- [ ] **Step 3: Assert the quality bar** — read the output:
  - flash-lite: on-menu 100%, structurally valid, and its rationale references the stall type + limiter muscle (evidence the enrichment is being used).
  - flash-lite converges with the frontier models on the enriched input (agreement comparable to or better than the pre-enrichment baseline).
  - Record the agreement matrix in the completion report.

- [ ] **Step 4: Full suite green**

Run: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q 2>&1 | tail -3'`
Expected: 0 failed.

- [ ] **Step 5: Commit the harness**

```bash
git add scripts/eval_enriched_slate.py
git commit -m "test(gen): enriched-payload cross-vendor slate verification harness"
```

---

## Routing Plan

| Task | Worker | Why |
|------|--------|-----|
| 1 — Muscle enum + Movement fields + migration | codex (delegate) | bounded single-purpose schema change, clear spec |
| 2 — Tag 108 movements (review gate) | codex proposes tags → **user review** → codex applies | data pass; human gate on accuracy |
| 3 — Enriched candidate descriptors | codex (delegate) | single-file payload change, exact shape given |
| 4 — Typed/severity/limiter stall record | codex (delegate) | single-file logic change, exact shape given |
| 5 — Phase-intent + rep-scheme | codex (delegate) | single-file payload change |
| 6 — systemInstruction + proposer call | codex (delegate) | 2-file change, exact code given |
| 7 — Verification slate | Tier A (orchestrator) | eval harness + judgment; not delegated codegen |

**Delegation ratio:** 6 of 7 tasks delegated (**~86%**). Tier A direct: Task 7 (verification/eval — orchestrator judgment) + the Task 2 human-review gate. If codex is unavailable, fall back to Claude Code Agent subagents (implementer + reviewer) per the standing fallback rule.

## Notes
- Single repo (IronLog-V2 server) — no two-repo contract this time.
- Build order: 1 → 2 → (3, 4, 5) → 6 → 7. Tasks 3/4 depend on tags (Task 2) for *meaningful* output; their *tests* use seeded fixtures, so they can be implemented right after Task 2.
- Deploying the muscle backfill (migration 011) to the live DB is a separate, gated, backup-first apply (user-owned), not part of this build.
