# STAB-Phase Maintenance Block Redesign — Design

## Source of truth

The athlete-authored content lives at `docs/program/source/2026-08-10-maintenance-block-seed-data.md`
(copied verbatim from the uploaded seed package). This design doc covers the
**technical translation** of that content into IronLog-V2's schema and a safe
rollout plan — it does not re-derive exercise selection, which is already
fully specified in the source doc.

## Background

The athlete is ready to leave the CUT phase and enter STAB (maintenance),
and wants a full program redesign alongside it — new equipment (APEX Bench,
Stryker Pad, Matrix Machine, Nordic Max, Hybrid Board, Ab Trainer, Better
Fly, AbMat pads, Belle Mere BMF Camber Bar), an active right hip/upper-glute
strain driving several exclusions, and an overhead-stability emphasis (3x/wk
vertical press vs. the prior 1x/wk).

An active-injury investigation earlier this session (right-side hip
extension/flexion pain, strength give-way on Hip Thrust, pain-free pure
rotation) independently arrived at holding Hip Thrust and most of D2/D5's
hip-loaded work — the source doc's own injury exclusions (all Hip Thrust
variants removed, bilateral RDL replaced with unilateral Kickstand RDL,
hip-hinge finishers removed) are consistent with and supersede that interim
hold.

**Likely root cause identified during this same investigation**: Ab Wheel
rollout with the lower back sagging into hyperextension instead of staying
braced — a classic mechanism for exactly this injury pattern, and the
athlete's own best guess for the original injury 2 weeks prior. Ab Wheel
does not appear anywhere in the source doc's new day structure (D1's old T4
tier — Seated Cable Row / Ab Wheel / Cross-Body Rear Delt Fly — is dropped
entirely in the new 3-tier D1 layout), so it is already excluded as a side
effect of the broader redesign. Documented here explicitly as an
injury-driven retirement (§8) rather than left as an undocumented
incidental drop.

## Decisions

### 1. Phase transition

`EngineState.current_phase` flips CUT → STAB alongside the reseed (direct
write, same effect as the existing `/engine-state/confirm-phase` endpoint).
This changes RPE band and back-off-set count via the existing `PhasePolicy`
row (`Phase.STAB`, already seeded: objective MAINTAIN, RPE 6–7.5, "+1
backoff vs CUT") — no new phase-policy mechanism needed.

### 2. Equipment translation — no new `Equipment` rows

Every "new" apparatus in the source doc (Stryker Pad, Matrix Machine,
Nordic Max, Hybrid Board, Ab Trainer, Better Fly, AbMat pads, Apex Bench) is
a supporting platform, not the resistance source. They become name/tag
suffixes on movements whose `load_code` is the real load equipment (DBs,
Ares cable, bodyweight/tower) — mirrors the existing `Incline DB Press [DB +
BENCH]` convention. Belle Mere BMF Camber Bar reuses the existing "BMF
Camber Bar" `Equipment` row (id=3, code `SB`) per the source doc's own note
that it's the same physical bar as "BMF Pro Camber Bar" already in the
system — not a new equipment row.

### 3. Movement identity for variants

Camber-bar grip variants (21"/14"/7" — bench, close-grip bench, tricep
extension) become 3 separate `Movement` rows, mirroring the existing Pendlay
Row Narrow/Medium/Wide precedent (one movement per named variant, not a
parametrized grip field — the model has none).

### 4. Progression-rule vocabulary mapping

`rep_ladder_at_cap` (source doc) maps to the engine's existing `REP_LADDER`
`ProgressionRule` (already implemented via `_rule_driven`'s at-cap handoff
and as a standalone rule) — no new rule needed.

### 5. Pull-up architecture (resolved conflict)

The source doc's pull-up section (3 days: D1/D4/D6) conflicted with
in-session changes made earlier this same conversation (D1's 3-band ladder,
D6's neutral-grip-paused unassisted variant) and was further revised live
during this design conversation: wide grip becomes standard everywhere,
dropped to 2 days/week.

- **D4** (`Wide-Grip Pull-up [TOWER]`): unchanged — unassisted, 6-8 reps,
  `pull_up_rolling_max`. Already correct, no seed change needed.
- **D6**: new movement `Wide-Grip Pull-up [TOWER + TUBES]` — assisted via
  sling + single 20lb band, `assist_ladder=[20, 0]`, `ASSISTANCE_REDUCTION`,
  8-12 reps (the higher-rep assisted slot).
- **D1**: pull-ups removed entirely. Replacement: **Better Fly Straight-Arm
  Lat Pulldown** (cable-based, already-planned equipment, direct lat
  target, no hip/back involvement) — fills the "more lat volume instead"
  requirement in D1's T3 GS.
- **Retired** (orphaned in place, not deleted — matches this session's
  established convention for superseded movements): `Pull-up [TOWER +
  TUBES]` (D1's now-dropped 3-band neutral-grip setup, built earlier this
  same conversation) and `Pull-up - Neutral Grip (Paused) [TOWER]` (D6's
  now-superseded unassisted variant, also built earlier this conversation).

### 6. Nordic Curl Max assist mechanism

Source doc specifies multi-band stacked stages (bands snapped in real use,
per the athlete) — superseded live during this conversation. Actual
mechanism: Ares-cable-assisted, starting at 60lb (athlete-verified, does
8-rep sets at this assist today), decreasing = harder.
`assist_ladder=[60, 50, 40, 30, 20, 10, 0]` (10lb steps, a reasonable
default from the one confirmed data point — flagged as correctable if the
real progression should step differently). `ASSISTANCE_REDUCTION`. One
shared `Movement` row used on both D2 and D5, day-scoped `MovementState`
providing the "independent track" the source doc calls for (matches how
`Reverse Nordic Curl [GHR]` already works today, shared movement + per-day
state).

### 7. Belt Squat A/B platform test

Source doc wants a Wk1 test (current Hyper Pro/GHR setup vs. new Hybrid
Board) with "winner becomes primary" — the engine has no A/B-test
mechanism. Resolution: keep the existing Hyper Pro/GHR binding as primary
for Wk1 (known-working `load_code`/floor already exist); the Hybrid Board
comparison happens manually in the gym, athlete reports back, and D2 T1
gets switched then if it wins. No code/seed change needed for this
specifically — it's a manual-decision deferral, not a technical fork.

### 8. Retirement list (orphaned, not deleted)

Per the source doc's "Movements Retired from Program This Block", plus the
pull-up retirements above:

- All Hip Thrust variants (D2, D5, D6)
- `rdl_bilateral` → replaced by Kickstand RDL (new unilateral DB movement)
- KB Swing / Sandbag Load finishers → replaced by Jump Rope
- Old Nordic Curl [Hyper Pro] variants → replaced by Nordic Curl Max
- `Pull-up [TOWER + TUBES]`, `Pull-up - Neutral Grip (Paused) [TOWER]` (see
  §5)
- `Ab Wheel [WHEEL]` (D1 T4) — **injury-driven, confirmed likely root
  cause**: rollout with the lower back sagging into hyperextension is the
  athlete's best-guess mechanism for the original strain 2 weeks prior.
  Not on the source doc's explicit retirement list, but genuinely absent
  from the new day structure — documented here so the reason isn't lost.

Orphaned `MovementState`/`HtProgressionState` rows for retired movements
are left in place (harmless, matches established convention throughout
this session — e.g. D6 Dips' old cable-loaded slot, D1's Pendlay Row
slot_id stability).

## Rollout order

Per-day incremental, matching this session's established, successful
pattern for every prior program restructuring (specs 25-33 and the
injury-hold work): build, verify (`pytest` green + live `/generate` smoke
test), deploy each day before starting the next.

1. **D1** — Upper Push (camber-bar bench, Stryker Pad OHP, Matrix Preacher
   Curl, Ab Trainer Cable Crunch, pull-up removed → lat pulldown added,
   Jump Rope finisher, Wall Slide warmup addition)
2. **D2** — Lower Squat (Belt Squat unchanged binding, Sissy Squat, Nordic
   Curl Max, ATG Split Squat, Hip Thrust removed)
3. **D4** — Upper Pull + Vertical Press (Seated BTN OHP, Wide-Grip Pull-up
   unchanged, Stryker Pad CSR, Cable Woodchopper retained, Jump Rope
   finisher, Wall Slide warmup addition)
4. **D5** — Lower Hinge (Kickstand RDL replaces bilateral RDL, Nordic Max
   BSS, Nordic Curl Max independent track, Hip Thrust removed)
5. **D6** — Weak Points + Isolation (new assisted Wide-Grip Pull-up, Dips
   unchanged, Camber Bar CG Bench, Better Fly accessory work)
6. **Phase flip** (CUT → STAB) once all 5 days are live and verified
7. **Final full-week verification** — `/generate` for all 5 days + both
   rest days, full test suite, live smoke-check

Retirement cleanup happens inline as each day's replacement lands (not a
separate pass) — e.g. D2's Hip Thrust removal happens as part of building
D2, not deferred.

## Testing

- Full suite green (`~/projects/IronLog-V2/.venv/bin/python -m pytest -q`)
  after every day's change, matching this session's standing baseline
  (701 passing as of the last merge).
- New/updated tests per day mirroring the established patterns:
  `test_program_seed_yaml_parity.py`, `test_library_seed.py` counts,
  `test_golive_phase1.py` `EXPECTED_NEEDS_CAL`, `test_rule_wiring.py`
  spot-checks, `test_generation_skeleton.py`/`test_generation_assembler.py`
  structural assertions where tier shape changes.
- Live verification after each day: direct `generate_session` call against
  production, confirming the assembled session's movement list and
  structure match intent.

## Open items (explicitly deferred, not blocking)

- Belt Squat Hybrid Board vs. Hyper Pro — manual test, reported back later.
- Nordic Curl Max's exact assist-ladder step size (10lb default) —
  correctable once more real data exists.
- Equipment retirement decision (BlackWing Bench vs. APEX Bench) —
  "pending James use," not this block's concern.
- AbMat ROM Pad and Zercher Pad — explicitly banked for a future block per
  the source doc, not wired this block.
