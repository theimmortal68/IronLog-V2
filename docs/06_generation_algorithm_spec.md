# Generation Algorithm (V2 Engine — the Capstone)

How a session gets built. This is the between-session brain: it assembles a `Session` (groups → planned exercises → planned sets) from the library, state, and phase, then hands it to the validator and to you for approval. It ties together every prior spec.

**The governing idea — constrained generation, not free generation.** The model never writes a workout from scratch. A deterministic skeleton is laid first, the model fills only the adaptive layer, and a deterministic validator rejects anything that breaks a hard rule. The model proposes; the rules dispose. This is what stops it drifting from your periodization on the things code already solves (load math, caps, frequencies).

---

## 0. Where this sits — the three cadences

| Loop | Trigger | Mechanism | This spec? |
|---|---|---|---|
| **Between-set** | a set is logged | deterministic autoregulation off `feedback_tap` (LLM only on pain/anomaly/note) | no — runtime |
| **Between-session** | next workout due | **generation (this spec)** | **yes** |
| **Conversational** | you type a note/request | classified `Note` → feeds the next generation as context | partially |

Generation is the between-session loop. The other two feed it (notes become context) or run downstream of it (autoregulation adjusts within a generated session).

---

## 1. Division of labor (the core principle)

| Concern | Owner |
|---|---|
| Day skeleton, anchor placement, primaries-first | **deterministic** |
| Load math, scheme resolution, increment ladders, RPE envelope | **deterministic** (progression spec) |
| Weekly-requirement accounting (knee freq, pull:push, volume) | **deterministic** ledger |
| Accessory/free-layer selection, ordering for feasibility, variant choice | **LLM** (judgment) |
| Weak-point response choice, interpreting notes, *when* to deviate | **LLM** |
| Every hard-rule check, clamp/reject | **deterministic validator** |

The LLM is grounded: it proposes by calling functions against real data (`getMovementState`, `getWeeklyLedger`, `getManifest`, `getRecentSignatures`), never by inventing numbers.

---

## 2. `DayTemplate` — per-day skeleton (config)

| Field | Notes |
|---|---|
| day_role | "Upper A" / "Lower A" / "Upper B" / "Lower B" / "Day 5" |
| primary_movement_ids | the anchor(s) for the day (e.g. Upper A → Bench) |
| required_patterns | giant-set slots by movement pattern (e.g. horiz pull ×2, vert push, …) |
| knee_block | which KOT modalities live here |
| conditioning_block | Z2 15 min end-block (Dreadmill, separate room), backward-walk first 3 min |

The template defines the slots; generation fills the adaptive ones.

---

## 3. The pipeline

```
1. RESOLVE CONTEXT
   date → active equipment manifest (phase) · EngineState (phase, bodyweight, gates)
   · PhasePolicy · DayTemplate · MovementState (loads, schemes, calibration, stall flags)
   · WeeklyLedger (what's owed) · recent signatures · open Notes

2. LAY FIXED SKELETON  (deterministic)
   place day's primary(ies) as STRAIGHT T1 groups FIRST, prescription from progression spec
   (top-set+backoff / maintain) · place HT on its days · place knee + conditioning blocks

3. COMPUTE OWED REQUIREMENTS  (deterministic)
   from WeeklyLedger: e.g. "Nordic owed 1, tib owed 1, pull:push at 1.3:1 → needs row,
   squat-pattern volume owed" → a requirement set this session should satisfy

4. LLM PROPOSES ADAPTIVE LAYER  (judgment, grounded by tools)
   fill giant-set slots from ACTIVE+feasible movements, honoring owed requirements,
   tier policy (§6), recent-signature avoidance (§7), and any weak-point bias / note →
   emits a complete Session + rationale

5. VALIDATE  (deterministic, hard)  →  clamp or reject with structured reasons (§8)
   on reject: repair loop back to step 4 (bounded); else deterministic fallback

6. APPROVE  (human-in-loop)  → PLANNED session

7. PERFORM → LOG → ANALYZE → update state + ledger → feeds next generation
```

---

## 4. Constraint catalog + priority

**Hard — structural** (never violated): primaries as straight sets first · giant sets 3×3 · knee-priority frequencies (Nordic 2×/wk, tib 2×/wk, KOT 2×/wk, sissy 1×/wk) · 2:1 horizontal pull:push · Z2 end-block with backward-walk lead-in.

**Hard — feasibility** (never violated): equipment in the active-phase manifest · single-KB · Dreadmill is an end-block, not a mid-circuit station · giant-set concurrency (≤3 items usable at once, room geometry) · setup+footwear transition budget within the 45–80 min cap · loads within floors/caps · RPE within phase cap · HT under the 220 clamp.

**Soft — novelty**: no-two-identical (signature distance, §7).

**Priority when they collide:** feasibility = training-logic > novelty. If no novel session is feasible, **relax novelty first** — never breach equipment, time, or frequency to chase variety. The model is told to group same-bar / same-station / same-footwear work to spend the transition budget well.

---

## 5. `WeeklyLedger` — the cross-session accountant

What makes "2×/wk" and "2:1" enforceable across days rather than within one. Running tallies, reset weekly:

| Tracks | Example |
|---|---|
| knee modality counts | Nordic 1/2, tib 1/2, KOT 2/2, sissy 0/1 |
| pull:push volume ratio | 1.3 : 1 (target ≥ 2 : 1) |
| semi-anchor frequency | main row 2×, triceps 2× |
| per-pattern working volume | vs phase volume landmark |

Generation reads the ledger to compute what's owed (step 3) and writes to it post-session (step 7). End-of-week shortfalls bias the last sessions of the week.

---

## 6. Tier policy (what repeats vs churns)

| Tier | Members | Generation behavior |
|---|---|---|
| **Anchor** | 4 primaries + HT | fixed by DayTemplate; repeat for measurement; never churned |
| **Semi-anchor** | main row, main glute builder, triceps | movement stable across the meso (rotate *loading*, not the exercise) so rules/weak-points keep a reference; prefer-continue unless meso boundary |
| **Free** | other accessories, techniques, finishers | LLM picks fresh each session for novelty |

Novelty is measured on the **free + semi-anchor loading** layer only — the anchors are *supposed* to repeat, so they're excluded from the signature.

---

## 7. Novelty — session signature

A session's `signature` is a weighted feature vector over the non-anchor layer:

| Axis | Weight |
|---|---|
| exercise set (which movements) | 0.40 |
| rep-zone profile | 0.25 |
| intensity techniques used | 0.20 |
| movement-pattern distribution | 0.10 |
| ordering | 0.05 |

**Rule:** a proposal must differ by ≥ 30% from each of the last 2 same-`day_role` sessions. Soft — relax the threshold before failing feasibility. This is the formal definition of "meaningfully different stimulus," and it stops the model gaming the rule by nudging one accessory a rep.

---

## 8. Validator — deterministic gate

Runs every hard check in §4 against the proposal. Two outcomes per violation:

- **Clamp** — a recoverable numeric breach (load over a cap, RPE target above the phase cap) → silently corrected.
- **Reject** — a structural breach (missing knee modality, infeasible equipment, anchors not first) → returned to the LLM as a **structured reason** ("Nordic frequency unmet; tib owed 1") for a repair re-proposal.

**Repair loop:** bounded retries (3). If still failing, fall back to a **deterministic safe session** — the last valid session for that day_role with loads refreshed from current state. The engine never emits an invalid or empty session.

---

## 9. Weak-point response inside generation

When a `progress`-objective lift's stall criteria fire (flat e1RM over N sessions / failed prescription — and *only* progress lifts, never a maintained one), generation biases the adaptive layer toward the limiter the `ExerciseSurvey` identified:

- failure **location** → add volume to that muscular limiter
- **technique** flag → cue/load adjustment, not a new exercise
- **asymmetry** → unilateral work

Graduated L1→L2→L3 — don't skip levels. Example wired through the data: squat stalls + `HIPS_SHOOT_UP` frequency high + HT:squat ratio under floor → hold squat load, bias glute-accessory volume, rotate to a hip-dominant variant — a leap a rules engine can't make but the LLM can, *then* the validator confirms knee/equipment/volume are still intact.

---

## 10. Robustness / failure modes

- **Repair loop bounded** → deterministic fallback session (always valid).
- **Model proposes something feasible but odd** → human approval catches it; rationale is logged and replayable.
- **Offline** (basement wifi drops) → fall back to the deterministic skeleton + last-known loads; the adaptive layer degrades to a repeat, not a failure.
- **Every decision logged** (proposal, clamps, rejections, approval) so you can audit whether the agent behaves.

---

## Composition

This is the convergence point: it reads the **library** (movements, equipment, floors/caps), resolves loading via the **progression model** + **PhasePolicy**, places **HT** by its composite spec, loads off baselines from the **calibration block**, emits the **session/set-log** structures, and writes back through the analysis hook. Six specs, one runtime.

---

## Tuning parameters (locked)

- **Approval mode:** approve-every-session for the first meso, then switch to **auto-approve-clean** (only sessions that needed a repair or clamp are surfaced). Strict → relaxes once trusted.
- **Proposal granularity:** whole session in one call (validator's targeted rejections cover the repair loop; slot-by-slot not worth the extra calls).
- **Semi-anchor rotation:** hold across the whole meso; rotate only at meso/phase boundaries. Free layer carries the variety.
- **Signature weights:** exercise-set 0.40 · rep-zone 0.25 · techniques 0.20 · pattern 0.10 · order 0.05.
- **Novelty threshold:** differ ≥ 30% from the last 2 same-`day_role` sessions (soft; relax before feasibility).
- **Repair retries:** 3, then deterministic fallback.
- **Tier step-down:** 2 consecutive failed progressions at the current rung → drop a rung.
- **Stall trigger** (progress lifts → weak-point response): e1RM flat/declining over 3 sessions, or 2 failed prescriptions.
