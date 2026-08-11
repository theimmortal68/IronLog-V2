# STAB-Phase Maintenance Block Redesign — Design

**Revision 2** — superseded by the athlete's finalized doc after D1 was
actually executed. This is a clean rewrite, not an incremental patch, to
keep the decisions internally consistent (revision 1 accumulated several
conflicting mid-review corrections — see git history for that trail if the
"why" behind a specific reversal matters).

## Source of truth

`docs/program/source/2026-08-10-maintenance-block-seed-data-FINAL.md` —
the athlete's finalized doc, explicitly versioned "Wk 1 D1 execution
complete, D2-D6 seed data updated with today's changes." This **supersedes**
the earlier `2026-08-10-maintenance-block-seed-data.md` (kept in the repo
for history, not authoritative). This design doc covers the **technical
translation** into IronLog-V2's schema and a safe rollout plan — exercise
selection is the source doc's call, already made.

## Background

The athlete left the CUT phase for STAB (maintenance) and redesigned the
program alongside it — new equipment (APEX Bench + Stryker Pad, Matrix
Machine, Nordic Max, Hybrid Board, Ab Trainer, Better Fly, AbMat pads,
Belle Mere BMF Camber Bar), an active right hip/upper-glute strain driving
several exclusions, and an overhead-stability emphasis (3x/wk vertical
press vs. the prior 1x/wk).

An in-session injury investigation (right-side hip extension/flexion pain,
strength give-way on Hip Thrust, pain-free pure rotation) independently
arrived at holding Hip Thrust and most of D2/D5's hip-loaded work — the
source doc's own exclusions (all Hip Thrust removed, bilateral RDL →
unilateral Kickstand RDL, hip-hinge finishers removed) are consistent with
and supersede that interim hold.

**Root cause, resolved**: Ab Wheel rollout with the lower back sagging into
hyperextension was the athlete's identified likely mechanism for the
original strain 2 weeks prior — but it's a technique issue, not the
movement itself. The athlete performed it correctly (braced) as part of
D1's real Wk1 execution. It stays in the program (D1 T3, anti-extension
core pattern) — see §5.

**D1 has already been trained** under this redesign (Wk1, real logged
numbers, `WK1_LOCKED` throughout the source doc). D1's implementation is
therefore "reconcile to reality" — seed the exact structure executed, and
seed `MovementState` baselines from the real logged weights, not
needs-calibration placeholders.

## Decisions

### 1. Phase transition

`EngineState.current_phase` flips CUT → STAB alongside the reseed (direct
write). Changes RPE band / back-off-set count via the existing
`PhasePolicy` row (`Phase.STAB`: objective MAINTAIN, RPE 6–7.5, "+1 backoff
vs CUT") — no new mechanism needed.

### 2. Global T1/T1b rep-range drop: 6-8 → 4-6

Applied during D1's real Wk1 session, to **all** T1/T1b primaries across
every day (Bench, Pendlay Row, Belt Squat, Seated BTN OHP, Kickstand RDL,
Close-Grip Bench Camber-14 — the last one is GS1-scoped but explicitly
called "T1-like" with `confirmation_window: 1`). Plain rep-range field
edits on the relevant `TierExercise` rows, no new mechanism.

### 3. Mandatory core work every session (new requirement)

Every training day gets exactly one core movement, different pattern per
day:

| Day | Movement | Pattern |
|---|---|---|
| D1 | Ab Wheel Rollout | anti-extension |
| D2 | Ab Trainer Decline Sit-up (new movement, new T4 tier) | spine flexion, bodyweight |
| D4 | Ab Trainer Hanging Leg Raise | anti-extension + hip flexion |
| D5 | Ab Trainer Russian Twist (new movement, new T4 tier) | rotation |
| D6 | AbMat Ab Bench Pad Cable Crunch | spine flexion, specialty pad |

D1, D4, D6 already had their core movement as part of an existing tier
(D1 T3 Ab Wheel, D4 T2 Hanging Leg Raise, D6 GS3 AbMat Cable Crunch) — no
new tiers needed there. **D2 and D5 each need a new standalone T4 straight
tier** for their core movement (`TierKind.T1_STRAIGHT`, `rep_ladder_at_cap`
→ `REP_LADDER`, bodyweight-first).

### 4. Equipment translation — no new `Equipment` rows

Every "new" apparatus (Stryker Pad, Matrix Machine, Nordic Max, Hybrid
Board, Ab Trainer, Better Fly, AbMat pads, Apex Bench) is a supporting
platform, not the resistance source — they become name/tag suffixes on
movements whose `load_code` is the real load equipment (DBs, Ares cable,
bodyweight/tower), mirroring the existing `Incline DB Press [DB + BENCH]`
convention. Belle Mere BMF Camber Bar reuses the existing "BMF Camber Bar"
`Equipment` row (id=3, code `SB`) per the source doc's own note that it's
the same physical bar as "BMF Pro Camber Bar" — not a new row.

**APEX attachment-conflict notes** (Stryker Pad + Matrix Machine coexist;
Ab Trainer requires exclusive mounting; FID Better Fly requires exclusive
mounting) are real physical-setup logistics the source doc tracks for the
athlete's own session planning. There is no existing schema field for this
kind of physical-setup cue (the closest precedent, `Tier.shoe`, is a
different concept — footwear, not attachment conflicts). Not modeled this
block — informational only, flagged as an open item.

### 5. Pull-up architecture

Confirmed final (matches the FINAL source doc exactly, no further
revision): 2 days/week, wide grip standard, **D1 + D6** — D4 loses it.

- **D1**: `Wide-Grip Pull-up [TOWER]` — unassisted, dead-hang, now at the
  global 4-6 rep range. **Wk1 executed 4/4/4** — seed `MovementState`
  accordingly (see §7), not needs-calibration. Progression note from the
  source doc: "5+ reps Set 1 → advance rep target" (handled by the
  existing `pull_up_rolling_max` tracking + `roll_unassisted_max`, no new
  logic).
- **D6**: `Wide-Grip Pull-up [TOWER + TUBES]` (new movement) — assisted via
  sling + single 20lb band, `assist_ladder=[20, 0]`, `ASSISTANCE_REDUCTION`,
  5-8 reps, `weekly_max_tracker` protocol. Source doc's baseline note ("7
  unassisted Set 1") is carried as context, not a seeded number — D6's
  actual current baseline needs the athlete's real current numbers, same
  as D1's.
- **D4**: pull-ups removed. Replacement: **Better Fly Lat Pulldown**
  (T1b slot, 6-8 reps, `rpe_8_standard`, `confirmation_window: 1`) — cable,
  no hip/back involvement, fills D4's vertical-pull role.
- **Retired** (orphaned in place, not deleted — matches this session's
  established convention): `Pull-up [TOWER + TUBES]` (D1's earlier
  in-conversation 3-band setup, since superseded), `Pull-up - Neutral Grip
  (Paused) [TOWER]` (D6's earlier in-conversation unassisted variant, also
  superseded).

### 6. Nordic Curl Max — final assist mechanism

Real setup, locked after 3 iterations documented in the source doc's own
"setup evolution log" (Monster Bands → Ares sled harness at the hip
[inefficient] → **Ares cable, upper-body/chest attach point, 60lb —
final**). Shared `Movement` row across D2/D5, day-scoped `MovementState`
for the "independent track" the source doc calls for (same pattern as
`Reverse Nordic Curl [GHR]`).

- `assist_ladder = [60, 55, 50, 45, 40, 35, 30, 25, 20, 15, 10, 5, 0]`
  (5lb steps — corrects revision 1's guessed 10lb steps).
- `ASSISTANCE_REDUCTION`, `confirmation_window: 2` ("2 sessions clean at
  RPE 8 before reducing assist," per the source doc).
- 6-8 reps.
- The attach-point detail (upper-body/chest, not hip/low-back) is real-world
  setup guidance with no schema equivalent — same treatment as the APEX
  conflict notes (§4): informational only.

### 7. D1 baselines — seed as real, not needs-calibration

D1 was actually trained. Seed `MovementState.current_load` /
`assist_level` directly from the source doc's `WK1_LOCKED` values instead
of leaving these slots empty:

| Slot | Value |
|---|---|
| Bench Press (Camber 21") | 155 × 3×6 @ RPE 8 |
| Pendlay Row | 170 (held — see §8) |
| Stryker Pad Seated OHP | 65 × 3×12 (source doc flags this as possibly RPE 6-7, "verify Wk2" — seed as-is, let the engine's normal floor/advance logic handle it) |
| Matrix Preacher Curl | 55 × 3×12 @ RPE 8 |
| Better Fly Standing Lateral Raise | 20 × 3×12 @ RPE 8 |
| Wide-Grip Pull-up (dead-hang) | 4/4/4 unassisted (rolling-max tracking, no scalar load) |
| Lat Prayer | 70 × 3×12 — source doc flags this as significantly under-loaded (RPE 6-7) and wants a jump to 85-95 next session. Seed the real 70 baseline; the jump itself is a Wk2 in-session decision, not seeded here (seeding a value the athlete hasn't actually lifted yet would misrepresent `MovementState` as measured when it isn't) |
| Ab Wheel Rollout | 3×8 bodyweight (protocol, no scalar load) |

D1's T2/T3 composition itself also changed from the source doc's earlier
draft to this final version — **Ab Trainer Cable Crunch is no longer part
of D1 at all** (D1's core requirement is fully covered by Ab Wheel in T3,
per §3's table), replaced in T2 by **Better Fly Standing Lateral Raise**.
T3's Better Fly Chest Fly and Better Fly Standing Lat/Front Raise (from the
earlier draft) are also gone, replaced by **Lat Prayer** (reuses the
existing `Lat Prayer [ANDREONI + FT]` movement — same cable/dual-pulley
setup already in the library) alongside the kept Pull-up and added Ab
Wheel.

### 8. Pendlay Row — held load, real progression rule

Source doc's `progression_rule: hold_load_strain_constraint` isn't an
engine rule name — maps to the existing `ProgressionRule.FIXED_LOAD` (same
mechanism already used for Reverse Hyper / Light Reverse Hyper's held
loads — a real, already-implemented no-advance rule, not a new one).
`current_load = 170`, explicitly held while the strain heals; Wk1 was
performed at 170×3×8 (over the 4-6 rep cap, logged as-is — the hold is on
load, not on stopping mid-set when the rep cap is exceeded).

### 9. Belt Squat A/B platform test

Still unresolved by design (engine has no A/B-test mechanism) — Hyper
Pro/GHR stays primary (`current_load = 260`, matches the source doc's
stated Hyper Pro baseline). Hybrid Board comparison remains a manual
decision, reported back later. `progression_rule: rep_ladder_at_cap` maps
to the existing `REP_LADDER` rule (§4 of revision 1, unchanged).

### 10. Retirement list (orphaned, not deleted)

- All Hip Thrust variants (D2, D5, D6)
- `rdl_bilateral` → replaced by Kickstand RDL
- KB Swing / Sandbag Load finishers → replaced by Jump Rope
- Old Nordic Curl [Hyper Pro] variants → replaced by Nordic Curl Max
- `Pull-up [TOWER + TUBES]`, `Pull-up - Neutral Grip (Paused) [TOWER]`
  (§5)

`Ab Wheel [WHEEL]` is explicitly NOT retired — confirmed kept, real part
of D1's executed Wk1 (§3, §7).

Orphaned `MovementState`/`HtProgressionState` rows for retired movements
are left in place (harmless, matches established convention throughout
this session).

## Rollout order

Per-day incremental, matching this session's established pattern: build,
verify (`pytest` green + live `/generate` smoke test), deploy each day
before the next. D1 is "reconcile to already-executed reality," not a
fresh build.

1. **D1** — Upper Push. Global rep-range drop (T1/T1b), final T2/T3
   composition (§7), real Wk1 baselines seeded, Wide-Grip Pull-up
   dead-hang kept, Ab Wheel kept (anti-extension core), Jump Rope finisher,
   Wall Slide warmup addition, Pendlay held via `FIXED_LOAD` (§8).
2. **D2** — Lower Squat. Belt Squat rep-range drop + `current_load=260`
   (§9), Sissy Squat, Nordic Curl Max (§6), ATG Split Squat, **new T4 core
   tier** (Ab Trainer Decline Sit-up, §3), Hip Thrust removed.
3. **D4** — Upper Pull + Vertical Press. Seated BTN OHP rep-range drop,
   pull-up removed → Better Fly Lat Pulldown (§5), Hanging Leg Raise
   (existing core slot), Stryker Pad CSR, Cable Woodchopper retained, Jump
   Rope finisher, Wall Slide warmup addition.
4. **D5** — Lower Hinge. Kickstand RDL rep-range drop, Nordic Max BSS,
   Nordic Curl Max independent track (§6), **new T4 core tier** (Ab
   Trainer Russian Twist, §3), Hip Thrust removed.
5. **D6** — Weak Points + Isolation. New assisted Wide-Grip Pull-up (§5),
   Dips unchanged, Close-Grip Bench Camber-14 at the T1-like 4-6 rep range,
   AbMat Cable Crunch (existing core slot).
6. **Phase flip** (CUT → STAB) once all 5 days are live and verified.
7. **Final full-week verification** — `/generate` for all 5 days + both
   rest days, full test suite, live smoke-check.

Retirement cleanup happens inline as each day's replacement lands, not a
separate pass.

## Testing

- Full suite green (`~/projects/IronLog-V2/.venv/bin/python -m pytest -q`)
  after every day's change (baseline: 701 passing).
- New/updated tests per day mirroring established patterns:
  `test_program_seed_yaml_parity.py`, `test_library_seed.py` counts,
  `test_golive_phase1.py` `EXPECTED_NEEDS_CAL` (D1's slots should NOT
  appear here — they're seeded from real data, not needs-calibration;
  D2/D4/D5/D6's genuinely-new movements should), `test_rule_wiring.py`
  spot-checks, `test_generation_skeleton.py`/`test_generation_assembler.py`
  structural assertions where tier shape changes.
- Live verification after each day: direct `generate_session` call against
  production, confirming the assembled session's movement list, structure,
  and (for D1) that the seeded baselines produce sane Wk2 prescriptions
  rather than a needs-calibration flag.

## Open items (explicitly deferred, not blocking)

- Belt Squat Hybrid Board vs. Hyper Pro — manual test, reported back later.
- APEX attachment-conflict logistics (Stryker+Matrix coexist, Ab
  Trainer/FID Better Fly need exclusive mounting) — no schema equivalent
  today, informational only. Real feature gap if the athlete wants this
  surfaced in-app (mirrors the `Tier.shoe` precedent but isn't the same
  concept) — not this redesign's scope.
- Client-visible per-exercise coaching cues (Ab Wheel bracing, dead-hang
  pull-up form) — `Movement.notes` exists but isn't surfaced anywhere in
  the client or generation payload today. Not this redesign's scope.
- Equipment retirement decision (BlackWing Bench vs. APEX Bench) —
  "pending James use," not this block's concern.
- AbMat ROM Pad and Zercher Pad — explicitly banked for a future block.
- D1's Stryker Pad Seated OHP (65×3×12, flagged as possibly under RPE 8)
  and Lat Prayer (70×3×12, confirmed under-loaded, wants an 85-95 jump)
  are seeded as-is from real Wk1 data — Wk2's actual progression/jump is a
  live in-session decision, not something this reseed pre-computes.
