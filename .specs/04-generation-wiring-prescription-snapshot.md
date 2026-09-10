# 04 — Wire the resolver into session generation + `prescription_snapshot`

## Objective
Replace `GenerationContext`'s `ctx.phase`/`ctx.phase_policy` usage (currently reading the old `PhasePolicy`/`EngineState.current_phase`) with the new `resolve_envelope()` from spec 03, driven by the caller's current `Microcycle`/`BodyCompState`/`RecoveryStatus`/`DeloadState`, and persist its full output to `Session.prescription_snapshot` (added in spec 01) at generation time — per design doc §7.

## File targets
- `ironlog/generation/context.py` — `GenerationContext` (class at line 83), the LLM-payload dict builder (~line 474)
- `ironlog/generation/assembler.py` — the deterministic-knob usages at lines ~258, ~482, and the `WorkoutSession(...)` construction at ~line 720
- `ironlog/generation/repair.py` — `phase_hard_cap=ctx.phase_policy.hard_cap` usage (~line 76)
- `tests/test_generation_context.py`, `tests/test_generation_assembler.py` (existing files — extend, don't replace)

## Changes

**Do not delete `ctx.phase`/`ctx.phase_policy` in this spec.** Per design doc §10, the actual `Phase`/`PhasePolicy` retirement is a migration-time cutover (spec 06), not something to do piecemeal inside the generation loop. This spec adds the NEW resolver-driven path **alongside** the existing one, wired so both can coexist until spec 06's cutover flips which one is authoritative — mirrors the additive, non-breaking approach spec 02 already used for the resolution chain.

1. **`GenerationContext`** (`context.py:83`): add fields for the resolver's inputs and output — e.g. `current_microcycle: Optional[Microcycle]`, `resolved_envelope: Optional[ResolvedEnvelope]` (from spec 03's `resolve_envelope()`). How `GenerationContext` acquires "what is the current Microcycle for today" is **in scope for this spec** — read the class's existing construction path (where does it currently get `ctx.phase` from? Likely `EngineState` — follow that same query pattern but for `Microcycle`: the microcycle whose `planned_start_date <= today <= planned_end_date`, or the most recent `ACTIVE` one if drift has pushed it past its planned end — match design doc §3's drift tolerance, don't just do a strict date-range query that fails during a drifted week). If no active Microcycle exists at all (new install, or before spec 06's cutover seeds one), `resolved_envelope` must be `None` and existing `ctx.phase_policy`-driven behavior must be completely unaffected — this spec must be a no-op until a Microcycle actually exists.
2. **LLM payload** (`context.py` ~line 474, the dict currently containing `"phase": ctx.phase, "objective": ..., "rpe_band": [...], "volume_posture": ...`): when `ctx.resolved_envelope` is present, add the resolver's trace/envelope to this same payload dict (e.g. a `"training_posture"` key + condensed trace, additive alongside the existing phase keys, not replacing them yet) — this is the "LLM receives posture as context" requirement from design doc §2/§8.
3. **Deterministic knob usages** (`assembler.py:258,482`, `repair.py:76`): when `ctx.resolved_envelope` is present, these should read from it (e.g. `ctx.resolved_envelope.rpe_cap` instead of `ctx.phase_policy.hard_cap`) in preference to the old `ctx.phase_policy` path — but only additively: wrap in `if ctx.resolved_envelope is not None: ... else: <existing behavior>`. Do not remove the `else` branch in this spec.
4. **`prescription_snapshot`** (`WorkoutSession(...)` construction, `assembler.py:~720`): when `ctx.resolved_envelope` is present, serialize it (macrocycle_id, mesocycle_id, microcycle_id, planned_posture, body_comp_state, recovery_status, deload_state, resolved_envelope, a resolver-policy-version string constant) into the new `Session.prescription_snapshot` JSON column per design doc §7's exact field list. When absent, leave `prescription_snapshot=None` — do not error.

## Edge cases
- A session generated with no active Microcycle (pre-cutover, or a gap between mesocycles) must generate exactly as it does today — this spec is strictly additive until spec 06 flips the switch. Write a test proving old behavior is bit-for-bit unchanged when no Microcycle is active.
- `resolved_envelope.progression_mode` must not be silently ignored by the deterministic layer once wired — if you thread it into `assembler.py`'s progression-attempt logic, make sure it can actually suppress/hold progression (matching `progression_mode` semantics from spec 03), not just get recorded and ignored.

## Dependencies
`.specs/01-periodization-data-model.md` (schema), `.specs/03-policy-resolver.md` (the `resolve_envelope` function and `ResolvedEnvelope` shape). Independent of `.specs/02-resolution-chain-microcycle-parity.md` (different files, no overlap) — can merge in either order relative to 02, but must merge after both 01 and 03.

## Verification
- Existing `tests/test_generation_context.py`, `tests/test_generation_assembler.py`, `tests/test_generation_fallback.py`, `tests/test_generation_skeleton.py` all still pass unchanged (proves the no-active-Microcycle no-op path is real).
- New test: with a Microcycle/BodyCompState/RecoveryStatus fixture present, generate a session and assert `Session.prescription_snapshot` is populated with the expected fields and that the deterministic knobs (RPE cap etc.) in the generated session actually reflect the resolver's output, not the old `PhasePolicy` values.
- New test: the LLM-payload dict includes the posture/trace context when a Microcycle is active.
- `pytest -q` fully green.
