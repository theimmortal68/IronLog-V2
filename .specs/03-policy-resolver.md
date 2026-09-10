# 03 — Deterministic policy resolver (posture → BodyComp → Recovery → Deload)

## Objective
Build the pure, deterministic resolver described in design doc §2 and §8: given a `Microcycle`'s `planned_posture`, the current `BodyCompState`, the current `RecoveryStatus`, and any active `DeloadState`, compute the effective session envelope (RPE cap, volume multiplier, progression eligibility, optional-work eligibility) plus a human-readable trace of how each axis modified it — following this repo's existing "rules dispose" pattern (`ironlog/engine/progression.py`, `ironlog/engine/validator.py`: pure functions, no DB writes, no LLM calls).

## File targets
- `ironlog/engine/periodization_resolver.py` (new) — the resolver itself
- `ironlog/models/periodization.py` — read-only reference to the enums/tables from spec 01 (no schema changes in this spec)
- `tests/test_periodization_resolver.py` (new)

## Changes

Read `docs/superpowers/specs/2026-09-03-long-range-periodization-design.md` §2 and §8 in full before writing this — the exact worked example in §8 (`Base(PUSH) → BodyCompState=CUT → RecoveryStatus=CAUTION → DeloadState=NONE → Effective`) is the acceptance shape for this module's output, reproduce it as a real test case.

**Public function** (exact name/shape is your call, but must be a single pure entry point, e.g.):
```python
def resolve_envelope(
    planned_posture: str,
    body_comp_state: str,       # "CUT" | "MAINTENANCE" | "GAIN"
    recovery_status: str,       # "NORMAL" | "CAUTION" | "POOR"
    deload_active: bool,
    deload_trigger_reason: Optional[str] = None,
) -> ResolvedEnvelope:
    ...
```

`ResolvedEnvelope` (dataclass or Pydantic model, your call, but must include):
- `rpe_cap: float`
- `volume_multiplier: float`
- `progression_mode: str` (e.g. `ACTIVE | HOLD_IF_BORDERLINE | SUPPRESSED` — pick a small explicit set, this feeds `should_attempt_progression`-style logic in `progression.py` later, don't invent something incompatible with the existing `Objective`/progression vocabulary there — read `ironlog/engine/progression.py` first)
- `optional_work_eligible: bool`
- `trace: List[TraceStep]` — each step: `{axis: str, before: dict, after: dict}` or equivalent, in resolution order (Base → BodyCompState → RecoveryStatus → DeloadState → Effective), matching design doc §8's worked example shape closely enough that it could be rendered as that example verbatim.

**Policy tables** (the actual per-posture/per-state numbers): define these as data structures inside this module (e.g. dicts keyed by `(posture, body_comp_state)` for the baseline+bodycomp layer, keyed by `recovery_status` for the recovery layer). The design doc explicitly defers exact numbers to implementation (§ "Open questions carried forward") — use reasonable placeholder values consistent with the worked example in §8 (PUSH baseline: rpe_cap=8.5, volume_multiplier=1.0, progression=ACTIVE; CUT modifier: rpe_cap -0.5, volume_multiplier ×0.9; CAUTION modifier: progression→HOLD_IF_BORDERLINE, optional_work→False) and flag in a module docstring that these are initial values pending real-data tuning, not locked constants — but they must still be a complete, internally consistent table covering every `(posture, body_comp_state)` combination you define in `MesocycleTemplate`'s posture vocabulary (ESTABLISH/BUILD/PUSH/CONSOLIDATE/INTENSIFY/PEAK/DELOAD) × 3 BodyCompStates, not just the PUSH/CUT example — a missing combination must raise a clear error, never silently fall through to a default.

**DeloadState override:** when `deload_active=True`, it overrides the resolved-so-far envelope per design doc §5 (reduced volume/intensity, no progression attempts) — this is a deterministic policy too (a fixed "deload envelope"), not itself computed from evidence (the evidence/triggering logic — "should deload be active" — is explicitly out of scope for this spec; it consumes `deload_active` as a given boolean, doesn't compute it). Note this boundary clearly in the module docstring so a future spec building the trigger logic knows where the line is.

**LLM context handoff:** this spec does not touch `ironlog/generation/generation.py` or the LLM proposer — that wiring (passing `ResolvedEnvelope` + trace as context, and enforcing the LLM can't loosen it) is spec 04's job. This spec only needs to produce a serializable result (the whole point of the `ResolvedEnvelope`/`trace` shape is that spec 04 can hand it to the LLM as JSON and store it in `Session.prescription_snapshot` verbatim).

## Edge cases
- Unknown `planned_posture` string not in the policy table → raise, don't default silently (this is a `rules dispose` invariant — an unrecognized posture must be a loud error, not a quiet fallback to some default envelope).
- `deload_active=True` with `body_comp_state`/`recovery_status` also both "bad" (e.g. CUT + POOR + deload active) — the override must still fully replace the prior layers' output, not stack further reductions on top (design doc: deload is an override, not an additional multiplier layer).
- `recovery_status="NORMAL"` must be a true no-op on the BodyCompState-modified envelope (i.e. NORMAL doesn't itself loosen anything CUT already tightened).

## Dependencies
`.specs/01-periodization-data-model.md` (uses the enums/vocabulary the models establish, though this spec adds no schema itself — it's a pure logic module and can be developed in parallel with spec 02, both depend only on 01).

## Verification
- New `tests/test_periodization_resolver.py`: reproduce design doc §8's exact worked example as one test case, asserting the exact `Effective` values shown there.
- One test per BodyCompState × posture combination at minimum for a `NORMAL`/no-deload baseline, proving the policy table is complete (no missing-combination errors).
- One test proving deload override fully replaces rather than stacks (per Edge cases above).
- One test proving an unrecognized posture raises rather than silently defaulting.
- `pytest -q` fully green, no regressions (this is a pure new module, should not be able to break anything existing — if it does, that's a signal something imported incorrectly).
